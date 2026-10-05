"""Temporary trial: Ukraine's Wikipedia map on a copy of the data branch's state. Nothing saved."""
import collections, json, math, sys, time
# run 2: reach per side
from datetime import datetime, timezone
sys.path.insert(0, "pipeline")
import config
from common import http_session
from frontline import cartographer, wikipedia

cfg = config.load()
state = json.load(open("state.json"))
fl = state["frontline"]
now = datetime.now(timezone.utc)

def area_by(pub):
    def ring(r):
        lat0 = sum(q[1] for q in r) / len(r); k = 111.32 * math.cos(math.radians(lat0)); a = 0
        for (x1, y1), (x2, y2) in zip(r, r[1:] + r[:1]): a += (x1 * k) * (y2 * 110.57) - (x2 * k) * (y1 * 110.57)
        return abs(a) / 2
    out = collections.Counter()
    for a in pub["areas"]:
        out[(a["conflict"], a["label"])] += sum(ring(p[0]) - sum(ring(h) for h in p[1:]) for p in a["polygons"])
    return out

before = area_by(cartographer.public(fl, cfg.frontlines, now))
print("read", wikipedia.run(cfg.frontlines, state, http_session(), now))
m = fl["baseline"]["maps"].get("ukraine")
if m:
    c = collections.Counter((p["holder"] or "contested", p["country"]) for p in m["points"])
    print("ukraine", len(m["points"]), "places", dict(c), "edited", m["edited"])
    print("sample", [(p["name"], p["holder"]) for p in m["points"][:10]])
t = time.time(); pub = cartographer.public(fl, cfg.frontlines, now); print("cartographer", round(time.time() - t, 1), "s, json", len(json.dumps(pub)) // 1000, "KB")
after = area_by(pub)
for key in sorted(set(before) | set(after)):
    if key[0] == "ukraine":
        print(f"   {key[1]:24} {before.get(key, 0):>10,.0f} -> {after.get(key, 0):>10,.0f}")
