"""Audit: checks every run for mistakes in what the map shows, no model calls (the owner, 2026-10-07:
"Do a sweep two times a day to make sure everything is being reported and displayed correctly and
learn from your mistakes"). Each check is a mistake already found on the map once; the twice-daily
sweep reads the findings (`state["audit"]`, and the `[audit]` log line), fixes the cause in code,
and adds a check here for every new kind of mistake it finds, so none goes unnoticed twice.

Checks (the mistake that led to each, and the fix):
- route_end_unplaced: a route end that is a US command or a whole region but not marked as one
  ("CENTCOM" drawn from Nepal, "Africa" from Chad, 2026-10-07; geo.pin_commands)
- carrier_route: an aircraft carrier's own move drawn as a supply route beside its track
  (2026-10-07; merge.carrier_moves)
- strike_mistyped: a drone or missile attack by an attacker with known launch areas, filed as a kind
  that gets no launch lines (Russian strikes on Ukraine, 2026-10-07; merge.drone_strikes)
- pin_outside_country: an event pinned far outside the country it names (Baidoa in the Indian
  Ocean, 2026-10-01)
- route_end_in_supplier: a delivery from one country to another that ends in the supplier's own
  country (Morocco's F-16 "to Greenville", 2026-10-07)
- carriers_together: two carriers placed together away from a home port (Ford and Eisenhower filed
  in Thailand where only the Bush was, 2026-10-06; fleet._other_there)
- carrier_vague_place: a carrier placed at a region's name ("West Asia", which landed on the Dubai coast,
  2026-10-07; fleet.VAGUE)
- carrier_too_fast: a carrier's move faster than it can sail (fleet._too_fast)
- future_time: an event timed after the moment it was read (common.make_item clamps it)
- (a delivery's pin may lie in its supplier or recipient too: Algeria's equipment for Moscow was filed under
  Poland, the country it passed through, 2026-10-07; not a mistake)
- duplicate_summary: two events with the same words within two days (dedupe.py)
"""
from __future__ import annotations

import re
from datetime import timedelta

from common import haversine_km, iso, log, parse_time

EXAMPLES = 5            # stored per check
PIN_KM = 100            # an event this far outside its country, inside another one, names the wrong country
SEA_KM = 1500           # ... or this far out at sea is misplaced (small islands aren't on the globe's map)
TERRITORIES = {"US": ["US", "PR", "GU", "VI", "AS", "MP"], "GB": ["GB", "FK"], "FR": ["FR", "NC", "PF"], "DK": ["DK", "GL"]}
TOGETHER_KM = 300
HOME_KM = 80
# ISO alpha-2 -> numeric, for the globe's country shapes (the same table as ISO_NUM in site/app.js)
_ISO_NUM = dict((s[:2], s[2:]) for s in (
    "AD020,AE784,AF004,AG028,AL008,AM051,AO024,AR032,AT040,AU036,AZ031,BA070,BD050,BE056,BF854,BG100,BH048,"
    "BI108,BJ204,BN096,BO068,BR076,BS044,BT064,BW072,BY112,BZ084,CA124,CD180,CF140,CG178,CH756,CI384,CL152,"
    "CM120,CN156,CO170,CR188,CU192,CY196,CZ203,DE276,DJ262,DK208,DO214,DZ012,EC218,EE233,EG818,EH732,ER232,"
    "ES724,ET231,FI246,FJ242,FK238,FR250,GA266,GB826,GE268,GH288,GL304,GM270,GN324,GQ226,GR300,GT320,GW624,"
    "GY328,HN340,HR191,HT332,HU348,ID360,IE372,IL376,IN356,IQ368,IR364,IS352,IT380,JM388,JO400,JP392,KE404,"
    "KG417,KH116,KP408,KR410,KW414,KZ398,LA418,LB422,LK144,LR430,LS426,LT440,LU442,LV428,LY434,MA504,MD498,"
    "ME499,MG450,MK807,ML466,MM104,MN496,MR478,MW454,MX484,MY458,MZ508,NA516,NC540,NE562,NG566,NI558,NL528,"
    "NO578,NP524,NZ554,OM512,PA591,PE604,PG598,PH608,PK586,PL616,PR630,PS275,PT620,PY600,QA634,RO642,RS688,"
    "RU643,RW646,SA682,SB090,SD729,SE752,SI705,SK703,SL694,SN686,SO706,SR740,SS728,SV222,SY760,SZ748,TD148,"
    "TG768,TH764,TJ762,TL626,TM795,TN788,TR792,TT780,TW158,TZ834,UA804,UG800,US840,UY858,UZ860,VE862,VN704,"
    "VU548,YE887,ZA710,ZM894,ZW716,GU316,VI850").split(","))
LAUNCHED = {"missile_drone", "air_defense", "airstrike"}       # kinds site/app.js draws launch lines for
ANCHORED = {"RU", "UA", "IR", "YE", "LB", "IL", "KP"}          # attackers with known launch areas (ANCHORS)


def _country_km(cc: str | None, lat: float, lon: float) -> float | None:
    """0 inside the country's shape on the globe (its overseas territories included), else the
    distance to its nearest outline point; None when the country has no shape."""
    from frontline import land
    codes = TERRITORIES.get(str(cc or "").upper(), [str(cc or "").upper()])
    rings = [r for c in codes for r in land._rings().get(_ISO_NUM.get(c, ""), [])]
    if not rings:
        return None
    if land.inside(rings, lon, lat):
        return 0.0
    return min(haversine_km(lat, lon, float(y), float(x)) for r in rings for x, y in r[::3])


def _country_of(lat: float, lon: float) -> str | None:
    """The country whose shape on the globe holds the point, if any."""
    from frontline import land
    shapes = land._rings()
    return next((cc for cc, num in _ISO_NUM.items() if shapes.get(num) and land.inside(shapes[num], lon, lat)), None)


def run(events: list[dict], fleet_rows: list[dict], state: dict, now) -> dict:
    """Run every check over the events and carriers about to be published; stores and returns
    {time, counts, examples}."""
    from fleet import _too_fast
    from geo import _command, region_anchor
    from merge import ATTACK_RE, CARRIER_RE, CREWED_RE, DRONE_MISSILE_RE
    found: dict[str, list] = {}

    def flag(check, e, detail):
        found.setdefault(check, []).append({"id": e.get("id") or e.get("hull"), "detail": detail[:160]})

    recent = now - timedelta(days=3)
    seen: dict[str, dict] = {}
    for e in events:
        t = parse_time(e.get("time"))
        if t and t > now + timedelta(minutes=5):
            flag("future_time", e, f"{e['time']} {e.get('summary', '')}")
        if not t or t < recent:
            continue
        summary = e.get("summary") or ""
        tr = e.get("transfer") or {}
        if e.get("type") == "arms_transfer" and tr:
            for end in ("from", "to"):
                v = tr.get(end) or {}
                if v.get("place") and not v.get("region") and (_command(v["place"]) or region_anchor(v["place"])):
                    flag("route_end_unplaced", e, f"{end} {v['place']!r}: {summary}")
            if tr.get("supplier") and tr.get("supplier") == tr.get("recipient") and CARRIER_RE.search(summary):
                flag("carrier_route", e, summary)
            to = tr.get("to") or {}
            if tr.get("supplier") and tr.get("recipient") and tr["supplier"] != tr["recipient"] \
                    and to.get("lat") is not None and not to.get("region") \
                    and _country_km(tr["supplier"], to["lat"], to["lon"]) == 0.0 \
                    and _country_km(tr["recipient"], to["lat"], to["lon"]) not in (0.0, None):
                flag("route_end_in_supplier", e, f"{tr['supplier']}->{tr['recipient']} ends at {to.get('place')!r}: {summary}")
        if e.get("attacker") in ANCHORED and e.get("type") == "explosion" and not e.get("alert") \
                and DRONE_MISSILE_RE.search(summary) and ATTACK_RE.search(summary) and not CREWED_RE.search(summary):
            flag("strike_mistyped", e, f"{e['type']}: {summary}")
        if e.get("lat") is not None and e.get("country") and not e.get("approx") and e.get("type") != "naval" \
                and not e.get("wave") and not e.get("alert"):
            # a route's pin is its far end: it may lie in the supplier, the recipient or a country passed through
            ends = {e["country"], tr.get("supplier"), tr.get("recipient")} if e.get("type") == "arms_transfer" and tr else {e["country"]}
            ds = [_country_km(c, e["lat"], e["lon"]) for c in ends if c]
            d = min((x for x in ds if x is not None), default=None)
            if d is not None and d > PIN_KM:
                other = _country_of(e["lat"], e["lon"])
                if other or d > SEA_KM:
                    flag("pin_outside_country", e, f"{d:.0f} km outside {e['country']}, {'in ' + other if other else 'at sea'}, "
                                                   f"at {e.get('place')!r}: {summary}")
        key = re.sub(r"\W+", " ", summary.lower()).strip()
        if key and key in seen and abs((parse_time(seen[key]["time"]) - t).total_seconds()) < 48 * 3600:
            flag("duplicate_summary", e, f"same words as {seen[key]['id']}: {summary}")
        seen.setdefault(key, e)

    from fleet import HOME
    homes = set(HOME.values())
    away = [c for c in fleet_rows if c.get("lat") is not None and not c.get("at_home")
            and not any(haversine_km(c["lat"], c["lon"], hl, ho) < HOME_KM for _, hl, ho in homes)]
    for i, a in enumerate(away):
        for b in away[i + 1:]:
            if haversine_km(a["lat"], a["lon"], b["lat"], b["lon"]) < TOGETHER_KM:
                flag("carriers_together", a, f"{a['name']} and {b['name']} both at {a.get('place')!r} / {b.get('place')!r}")
    from fleet import _vague
    for c in fleet_rows:
        if c.get("lat") is not None and not c.get("at_home") and _vague(c.get("place")):
            flag("carrier_vague_place", c, f"{c['name']} placed at {c.get('place')!r}, a region, not a place")
        p = c.get("prev")
        if p and c.get("as_of") and p.get("as_of") and _too_fast(p, c):
            flag("carrier_too_fast", c, f"{c['name']}: {p.get('place')!r} to {c.get('place')!r} by {c['as_of'][:16]}")

    out = {"time": iso(now), "counts": {k: len(v) for k, v in sorted(found.items())},
           "examples": {k: v[:EXAMPLES] for k, v in sorted(found.items())}}
    state["audit"] = out
    log(f"[audit] {sum(out['counts'].values())} findings" + (f": {out['counts']}" if out["counts"] else ""))
    return out
