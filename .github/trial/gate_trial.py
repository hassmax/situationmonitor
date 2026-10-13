"""Trial of the gatekeeper on the live data (read only; nothing is saved). Removed before merging."""
import copy
import json
import sys
import time
from collections import Counter
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

state = load_json(Path("state/state.json"), {})
events = load_json(Path("state/events.json"), {}).get("events", [])
cfg = config_mod.load()
t0 = now()
history = archive.recent(Path("state"), t0, dedupe.LATE_DAYS,
                         [s for g in ("rss", "bluesky", "telegram") for s in cfg.sources[g]] + list(cfg.outlets.values()))
print(f"working set {len(events)}, archive {len(history)}")
created = lambda e: min((r.get("time") or e["time"]) for r in e.get("reports") or [e])  # noqa: E731

# A. calibration on pairs the same-story check has judged
pool = {e["id"]: e for e in history + events}
pairs = [(k, v) for k, v in (state.get("dedupe") or {}).get("judged", {}).items()
         if all(i in pool for i in k.split("|"))]
print(f"judged pairs with both events available: {len(pairs)} ({sum(1 for _, v in pairs if v.get('same'))} same)")
pairs = [p for p in pairs if p[1].get("same")][:300] + [p for p in pairs if not p[1].get("same")][:900]
t = time.time()
texts = [x for k, _ in pairs for i in k.split("|") for x in gate._texts(pool[i])]
vecs = embed.vectors(texts)
print(f"embedded {len(set(texts))} texts in {time.time() - t:.1f}s; model {'on' if vecs else 'OFF'}")
if vecs:
    rows = []
    for k, v in pairs:
        a, b = (pool[i] for i in k.split("|"))
        rows.append((embed.similarity(gate._texts(a), gate._texts(b), vecs), bool(v.get("same")), dedupe.near(a, b)))
    for thr in (0.70, 0.75, 0.78, 0.80, 0.82, 0.85, 0.88, 0.90, 0.93):
        for nr in (True, False):
            sel = [r for r in rows if r[2] == nr]
            hit = [r for r in sel if r[0] is not None and r[0] >= thr]
            same = sum(1 for r in sel if r[1])
            tp = sum(1 for r in hit if r[1])
            print(f"  {'near' if nr else 'far '} >= {thr:.2f}: {len(hit)} pairs, {tp} same "
                  f"(of {same} same, {len(sel) - same} different)")

# B. replay without the model: what each 15-minute run of the last 3 days would have asked
since = t0 - timedelta(days=3)
recent = sorted([e for e in events if parse_time(created(e)) >= since], key=created)
runs = Counter()
ask_counts, tsum = [], 0.0
buckets = {}
for e in recent:
    b = int((parse_time(created(e)) - since).total_seconds() // 900)
    buckets.setdefault(b, []).append(e)
for b, new in sorted(buckets.items()):
    cut = since + timedelta(seconds=900 * b)
    before = [copy.deepcopy(e) for e in events if parse_time(created(e)) < cut]
    news = [copy.deepcopy(e) for e in new]
    for e in news:
        e.pop("gate", None)
    asked = []

    def ask(system, user, *a, **k):
        asked.append(len(json.loads(user)["cases"]))
        return None
    t = time.time()
    real = gate.recency._evidence
    gate.recency._evidence = lambda s, e: None
    try:
        gate.check(before + news, {e["id"] for e in news}, None, ask, {}, {}, cut + timedelta(minutes=15), set(), [])
    finally:
        gate.recency._evidence = real
    tsum += time.time() - t
    ask_counts.append((len(news), sum(asked)))
n = sum(a for a, _ in ask_counts)
q = sum(b for _, b in ask_counts)
print(f"replay: {len(ask_counts)} runs with new events, {n} new events, {q} needed a question "
      f"({q / max(1, n):.0%}); most in one run {max(a for a, _ in ask_counts)} new, "
      f"{max(b for _, b in ask_counts)} questions; {tsum / max(1, len(ask_counts)):.1f}s a run for screening")

# C. live: the last 4 hours' new events through the real gatekeeper (at most 6 calls)
gate.MAX_CALLS = 6
st = copy.deepcopy(state)
cut = t0 - timedelta(hours=4)
work = copy.deepcopy(events)
new = {e["id"] for e in work if parse_time(created(e)) >= cut}
for e in work:
    if e["id"] in new:
        e.pop("gate", None)
        e.pop("checked", None)
st.setdefault("dedupe", {})["judged"] = {}
t = time.time()
out, folded = gate.check(work, new, http_session(), extract.ask_json, st, cfg.settings, t0, set(), history)
print(f"live: {len(new)} new events in {time.time() - t:.0f}s, {len(folded)} folded, "
      f"{len(work) - len(out) - len(folded)} dropped as old; calls {st.get('llm_calls', {}).get('by')}")
