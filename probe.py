"""Temporary: run the flight agent live (two reads 3 minutes apart) and print what the map would get."""
import collections, json, sys, time
from datetime import datetime, timezone
sys.path.insert(0, "pipeline")
import config, flights
from common import http_session
cfg = config.load()
s = http_session()
state, health = {}, {}
for i in range(2):
    now = datetime.now(timezone.utc)
    items = flights.update(state, s, health, now, cfg.flight_bases)
    for it in items:
        print("REPORT:", it["text"])
    if i == 0:
        time.sleep(180)
pub = flights.public(state, datetime.now(timezone.utc))
print("shown:", len(pub["aircraft"]), collections.Counter(f["role"] for f in pub["aircraft"]))
for f in pub["aircraft"]:
    print(f"  {f['role']:12} {f['label'][:40]:40} {f['callsign']:9} {f['reg']:10} {f['op'] or '':40.40} {f['lat']:8.3f} {f['lon']:9.3f} alt={f['alt']} pts={len(f['track'])}")
print("JSON:" + json.dumps(pub)[:60000])
