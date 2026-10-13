"""Trial of the gatekeeper on the live data (read only; nothing is saved). Removed before merging."""
import copy
import json
import sys
import time
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, "pipeline")
import archive  # noqa: E402
import config as config_mod  # noqa: E402
import dedupe  # noqa: E402
import embed  # noqa: E402
import extract  # noqa: E402
import gate  # noqa: E402
from common import http_session, load_json, now, parse_time  # noqa: E402

summary = []
state = load_json(Path("state/state.json"), {})
events = load_json(Path("state/events.json"), {}).get("events", [])
cfg = config_mod.load()
t0 = now()
history = archive.recent(Path("state"), t0, dedupe.LATE_DAYS,
                         [s for g in ("rss", "bluesky", "telegram") for s in cfg.sources[g]] + list(cfg.outlets.values()))
created = lambda e: min((r.get("time") or e["time"]) for r in e.get("reports") or [e])  # noqa: E731
summary.append(f"working set {len(events)}, archive {len(history)}")

# B. replay without the model: each 15-minute run of the last 2 days
quiet = gate.log
gate.log = lambda *a: None
since = t0 - timedelta(days=2)
buckets = {}
for e in sorted([e for e in events if parse_time(created(e)) >= since], key=created):
    buckets.setdefault(int((parse_time(created(e)) - since).total_seconds() // 900), []).append(e)
rows, times = [], []
gate.recency._evidence = lambda s, e: None
for b, new in sorted(buckets.items()):
    cut = since + timedelta(seconds=900 * b)
    before = [copy.deepcopy(e) for e in events if parse_time(created(e)) < cut]
    news = [copy.deepcopy(e) for e in new]
    for e in news:
        e.pop("gate", None)
    asked = []
    t = time.time()
    gate.check(before + news, {e["id"] for e in news}, None,
               lambda s, u, *a, **k: asked.append(len(json.loads(u)["cases"])), {}, {}, cut + timedelta(minutes=15), set(), [])
    times.append(time.time() - t)
    rows.append((len(news), sum(asked)))
n, q = sum(a for a, _ in rows), sum(b for _, b in rows)
summary.append(f"replay 2 days: {len(rows)} runs, {n} new events, {q} needed a question ({q / max(1, n):.0%}), "
               f"most in one run {max(a for a, _ in rows)}; screening first run {times[0]:.1f}s, "
               f"then average {sum(times[1:]) / max(1, len(times) - 1):.2f}s, slowest {max(times[1:] or [0]):.1f}s; "
               f"meaning model {'ON' if embed._model else 'OFF'}")
gate.log = quiet
del gate.recency._evidence  # restore the real searches
import importlib  # noqa: E402
importlib.reload(gate.recency)

# C. live: the last 4 hours' new events through the real gatekeeper (at most 6 calls)
gate.MAX_CALLS = 6
st = copy.deepcopy(state)
work = copy.deepcopy(events)
cut = t0 - timedelta(hours=4)
new = {e["id"] for e in work if parse_time(created(e)) >= cut}
for e in work:
    if e["id"] in new:
        e.pop("gate", None)
        e.pop("checked", None)
st.setdefault("dedupe", {})["judged"] = {}
lines = []
real_log = gate.log
gate.log = lambda m: (lines.append(m), print(m))
t = time.time()
out, folded = gate.check(work, new, http_session(), extract.ask_json, st, cfg.settings, t0, set(), history)
summary.append(f"live: {len(new)} new events in {time.time() - t:.0f}s, {len(folded)} folded, "
               f"{len(work) - len(out) - len(folded)} dropped as old; Gemini {st.get('llm_calls', {}).get('by')}, "
               f"providers {({k: v.get('by') for k, v in (st.get('providers') or {}).items()})}")
summary += [m[:260] for m in lines if "same story" in m or "old news" in m or "no model" in m]
# how the meaning model's scores line up with the model's answers
by = {e["id"]: e for e in events + history}
pairs = [(k, v) for k, v in st["dedupe"]["judged"].items() if v.get("gate")]
vecs = embed.vectors([t for k, _ in pairs for i in k.split("|") if i in by for t in gate._texts(by[i])])
for k, v in sorted(pairs, key=lambda kv: -kv[1]["same"]):
    a, b = k.split("|")
    if a in by and b in by:
        s = embed.similarity(gate._texts(by[a]), gate._texts(by[b]), vecs)
        summary.append(f"  {'SAME' if v['same'] else 'diff'} meaning {s if s is None else round(s, 2)} near {dedupe.near(by[a], by[b])}: "
                       f"{by[a]['summary'][:60]!r} | {by[b]['summary'][:60]!r}")
print("\n===== SUMMARY =====")
print("\n".join(summary[:120]))
