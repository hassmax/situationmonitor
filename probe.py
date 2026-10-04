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
records, carriers, done = [], [], set()
n = extract._overflow([pending], "probe", state, settings, now, records, carriers, done)
print(f"overflow: {n} batch answered in {time.time() - t:.0f}s; {len(done)} items done; {len(records)} records")
for r in records[:15]:
    print(" -", r.get("type"), "|", r.get("place"), "|", (r.get("summary") or "")[:140])
print("providers:", providers.summary(state, now))

# let the full run below write the analysis now (on this copy only)
import json as _j
state.pop("analysis_attempt", None); state.pop("analysis_fp", None)
open("state/state.json", "w").write(_j.dumps(state))
