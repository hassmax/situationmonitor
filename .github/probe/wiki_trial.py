"""Temporary trial: the Wikipedia fill-in on a copy of the data branch's state. Nothing saved."""
import collections, json, math, sys, time
# run 2: simplified outlines
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
for cid, m in fl["baseline"]["maps"].items():
    print(f"  {cid}: {len(m['points'])} places, edited {m['edited']}; sample", [(p['name'], p['holder']) for p in m['points'][:6]])
t = time.time(); pub = cartographer.public(fl, cfg.frontlines, now); print("cartographer", round(time.time() - t, 1), "s")
print("frontline json", len(json.dumps(pub)) // 1000, "KB; credits", pub["credits"])
after = area_by(pub)
print("\n== shaded km2 before -> after")
for key in sorted(set(before) | set(after)):
    print(f"   {key[0]:9} {key[1]:26} {before.get(key, 0):>10,.0f} -> {after.get(key, 0):>10,.0f}")
for a in pub["areas"]:
    print("  ", a["id"], "|", a["source"], "|", a["settlements"], "settlements")
