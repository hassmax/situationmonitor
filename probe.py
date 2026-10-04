"""Temporary: the regional analyst on the live site's events, with the real providers."""
import json, sys
from datetime import datetime, timezone
sys.path.insert(0, "pipeline")
import analyst, config, extract
cfg = config.load()
data = json.load(open("/tmp/events.json"))
now = datetime.now(timezone.utc)
events = data["events"]
state = {"llm_model": json.load(open("/tmp/state.json")).get("llm_model")}
analyst.update(state, events, cfg.theaters, data.get("fleet") or [], data.get("flights"), cfg.settings, now, extract.ask_json, 100, 5)
a = state.get("analysis") or {}
print("written by:", a.get("by"))
by = {e["id"]: e for e in events}
for r in a.get("regions", []):
    print("==", r["name"])
    for j in r["judgments"]:
        print(f"  [{j['trend']}] {j['headline']}  ({j['confidence']}, {j['tally']})")
        print(f"     {j['text']}")
        for i in j["ids"]:
            e = by[i]
            print(f"       - {e['status']:12} {e['type']:14} {e.get('place','')[:30]:30} {e['summary'][:110]}")
print("ANALYSIS:" + json.dumps(a, ensure_ascii=False))
