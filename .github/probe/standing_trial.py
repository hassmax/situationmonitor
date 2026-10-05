"""Temporary live trial (not part of the pipeline): the new standing searches and the capture-headline
reader on a copy of the data branch's state, outside Ukraine. Nothing is saved.""" Run 2: headlines with the war's own words.
import collections, json, sys
from datetime import datetime, timezone
sys.path.insert(0, "pipeline")
import config, extract, geo
from common import http_session
from frontline import assess, cartographer, claims, ledger, review, standing

cfg = config.load()
state = json.load(open("state.json"))
settings = cfg.settings
now = datetime.now(timezone.utc)
session = http_session()
fl = ledger.state_of(state)
conf = [c for c in cfg.frontlines if c["id"] != "ukraine"]
before = cartographer.public(fl, cfg.frontlines, now)

def area_by(pub):
    import math
    def ring(r):
        lat0 = sum(q[1] for q in r) / len(r); k = 111.32 * math.cos(math.radians(lat0)); a = 0
        for (x1, y1), (x2, y2) in zip(r, r[1:] + r[:1]): a += (x1 * k) * (y2 * 110.57) - (x2 * k) * (y1 * 110.57)
        return abs(a) / 2
    out = collections.Counter()
    for a in pub["areas"]:
        out[(a["conflict"], a["label"])] += sum(ring(p[0]) - sum(ring(h) for h in p[1:]) for p in a["polygons"])
    return out

standing.FIRST_PASS = 400
standing.PAUSE = 0.4
fl["standing"] = {}
found = standing.run(conf, state, session, now, [], cfg.outlets)
print(f"\n== described as held: {len(found)}")
for c in found:
    print("  ", c["conflict"], c["name"], c["claim"]["actor"], "|", c["claim"]["summary"][:140], "|", c["claim"]["group"])
print("added", assess.add(fl, found, now))
q = fl["news"]["queue"]
print(f"\n== capture headlines queued: {len(q)}", dict(collections.Counter(x["conflict"] for x in q)))
news_claims = []
for _ in range(10):
    if not fl["news"]["queue"]:
        break
    got = claims.run_news(cfg.frontlines, state, settings, now, extract.ask_json, 1)
    news_claims += got
print(f"\n== claims from headlines: {len(news_claims)}")
for c in news_claims:
    k = c["claim"]
    print(f"   {c['conflict']:8} {c['name']:18} {k['change']:9} {str(k['actor']):7} by={str(k['claimed_by']):7} {k['basis']:12} "
          f"aligned={str(k['aligned']):6} {k['time'][:10]} | {k['summary'][:110]} | {k['group']}")
print("added", assess.add(fl, news_claims, now))
print("placed", assess.locate(fl, cfg.frontlines, geo.Geocoder(state["geocache"], session, 150)))
pending = []
for k, p in fl["places"].items():
    if p["conflict"] == "ukraine" or p.get("lat") is None:
        continue
    conflict = ledger.conflict_for(cfg.frontlines, p["country"], p["conflict"])
    prop = assess.assess(p, conflict, now)
    pub = p.get("published") or {}
    if prop and (prop.get("holder"), prop["status"]) != (pub.get("holder"), pub.get("status")):
        pending.append((k, prop))
print(f"\n== proposed changes outside Ukraine: {len(pending)}")
for k, prop in pending:
    print("  ", k, prop["status"], prop.get("holder"), "|", prop.get("basis"), "| was", (fl["places"][k].get("published") or {}).get("status"))
review.PER_CALL = 15
counts = review.run(pending, cfg.frontlines, fl, state, settings, now, extract.ask_json, 4)
print("\n== review", counts)
for k, _ in pending:
    pub = fl["places"][k].get("published") or {}
    print("  ", k, pub.get("status"), pub.get("holder"), "|", (pub.get("note") or "")[:120])
after = cartographer.public(fl, cfg.frontlines, now)
a0, a1 = area_by(before), area_by(after)
print("\n== shaded km2 before -> after")
for key in sorted(set(a0) | set(a1)):
    print(f"   {key[0]:9} {key[1]:28} {a0.get(key, 0):>10,.0f} -> {a1.get(key, 0):>10,.0f}")
print("\n[budget]", (state.get("llm_calls") or {}).get("by"))
