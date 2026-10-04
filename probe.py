"""Temporary: flight log over 12 minutes, then the analyst (Cerebras) on the live events with it."""
import json, sys, time
from datetime import datetime, timezone
sys.path.insert(0, "pipeline")
import analyst, config, extract, flights
from common import http_session
cfg = config.load()
s = http_session()
state = {"llm_model": json.load(open("/tmp/state.json")).get("llm_model")}
for i in range(4):
    flights.update(state, s, {}, datetime.now(timezone.utc), cfg.flight_bases)
    if i < 3:
        time.sleep(240)
now = datetime.now(timezone.utc)
fl = flights.for_analyst(state, now)
print("aircraft now:", len(fl["aircraft"]), "movements:", len(fl["movements"]))
for m in fl["movements"]:
    print("  MOVE", m["id"], m["text"])
data = json.load(open("/tmp/events.json"))
events = data["events"]
analyst.update(state, events, cfg.theaters, data.get("fleet") or [], fl, cfg.settings, now, extract.ask_json, 100, 5)
a = state.get("analysis") or {}
print("written by:", a.get("by"), "| flight credit:", a.get("flight_credit"))
for r in a.get("regions", []):
    print("==", r["name"])
    for j in r["judgments"]:
        print(f"  [{j['trend']}] {j['headline']}  ({j['confidence']}, {j['tally']})")
        print(f"     {j['text']}")
        for f in j.get("flights") or []:
            print("       ✈", f["what"])
