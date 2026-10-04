"""Temporary: try the offloading on GitHub's servers with the real keys (removed before the PR)."""
import json, sys, time
sys.path.insert(0, "pipeline")
from datetime import datetime, timezone
import config, extract, providers
from common import load_json

state = load_json("state/state.json", {})
settings = config.load().settings
now = datetime.now(timezone.utc)
events = load_json("state/events.json", {}).get("events", [])
events.sort(key=lambda e: e.get("time") or "", reverse=True)
pending = []
for e in events:
    for r in (e.get("reports") or [])[:1]:
        if r.get("summary") and len(pending) < 40:
            pending.append({"id": f"probe{len(pending)}", "source": r.get("source") or "news", "platform": "rss",
                            "time": r.get("time") or e.get("time"), "url": r.get("url") or "", "text": r["summary"]})
print("pending items:", len(state.get("pending", [])), "batch", len(pending))
text = extract._payload(pending)
print("estimated input tokens:", extract._estimate_tokens(extract.SYSTEM_PROMPT) + extract._estimate_tokens(text))
t = time.time()
out = providers.ask_routed(extract.SYSTEM_PROMPT, text, state, settings, now, "extract",
                           max_tokens=int(settings["max_output_tokens"]), temperature=0.1, max_wait=65)
print(f"overflow batch: {'answered' if out else 'NO ANSWER'} in {time.time() - t:.0f}s")
if out:
    recs = [extract._clean_record(o, pending[o["i"]]) for o in out.get("events", [])
            if isinstance(o, dict) and isinstance(o.get("i"), int) and 0 <= o["i"] < len(pending)]
    good = [r for r in recs if r]
    print(f"answers for {len(recs)} of {len(pending)} items; {len(good)} records")
    for r in good[:12]:
        print(" -", r.get("type"), "|", r.get("place"), "|", (r.get("summary") or "")[:140])
print("providers:", providers.summary(state, now))

# let the full run below write the analysis now (on this copy only)
import json as _j
state.pop("analysis_attempt", None); state.pop("analysis_fp", None)
open("state/state.json", "w").write(_j.dumps(state))
