"""Temporary: the regional analyst on the live site's events, with the real model (one call)."""
import json, sys
from datetime import datetime, timezone
sys.path.insert(0, "pipeline")
import analyst, config, extract
cfg = config.load()
data = json.load(open("/tmp/events.json"))
now = datetime.now(timezone.utc)
events = data["events"]
shown = analyst.regions(events, cfg.theaters, data.get("fleet") or [], data.get("flights"), now)
print("regions shown:", [(r["region"], len(r["events"]), r.get("us_carriers_nearby") and len(r["us_carriers_nearby"]), r.get("military_aircraft_broadcasting_now")) for r in shown])
print("payload chars:", len(json.dumps({"regions": shown}, ensure_ascii=False)))
state = {}
raw = {}
def ask(*a, **k):
    out = extract.ask_json(*a, **k)
    raw["reply"] = out
    return out
analyst.update(state, events, cfg.theaters, data.get("fleet") or [], data.get("flights"), cfg.settings, now, ask, 100, 5)
print("RAW:", json.dumps(raw.get("reply"), ensure_ascii=False)[:6000])
by = {e["id"]: e for e in events}
for r in (state.get("analysis") or {}).get("regions", []):
    print("==", r["name"])
    for j in r["judgments"]:
        print(f"  [{j['trend']}] {j['headline']}  ({j['confidence']}, {j['tally']})")
        print(f"     {j['text']}")
        for i in j["ids"]:
            e = by[i]
            print(f"       - {e['status']:12} {e['type']:14} {e.get('place','')[:30]:30} {e['summary'][:110]}")
