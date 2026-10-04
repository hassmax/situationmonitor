"""Assessor: no model. Files the claims agent's claims under their settlement, looks the
settlement up once, and works out what the evidence adds up to, by fixed rules modelled on how ISW
separates "assessed" control from a side's claim:

- A claim names who holds the settlement after it: "took"/"holds" -> the actor; "lost" -> the
  opponent (two-sided conflicts only); "contested" -> nobody.
- It is STRONG evidence when it rests on geolocated footage, reporting from the scene, both sides'
  acknowledgement, or an independent analyst (ISW); when the side that lost it says so itself;
  when the opposing side's sources say the same; or when 2+ independent source groups, one of them
  not aligned with either side, report it within CORROBORATE of each other (as for events).
  Outlets describing the town as held as settled fact (standing.py, basis "described") count the
  same way over DESCRIBED_WINDOW, or at once when it is the other side's own media.
  Otherwise it is one side's word (or unattributed): a CLAIM.
- assessed: the holder named by the latest strong evidence ("since" = its first strong evidence
  after the last strong evidence for anyone else).
- claimed: a newer claim names a different holder (or there is no strong evidence at all).
- contested: fighting inside reported since the latest strong evidence, within CONTESTED_FOR.
Nothing here is published directly: the reviewer agent checks every change first.
"""
from __future__ import annotations

from datetime import timedelta

from common import haversine_km, iso, log, parse_time

from . import ledger

CORROBORATE = timedelta(days=3)
DESCRIBED_WINDOW = timedelta(days=45)  # outlets describing a town as held agree if this close together
HINT_KM = 60        # a lookup this close to the event's own pin is the place meant
REGION_KM = 200     # ... else it must lie this close to the region the report names
SAME_KM = 60        # candidates this close together are one answer
MERGE_KM = 3        # two spellings placed this close together are one settlement
CONTESTED_FOR = timedelta(days=5)
LOOKUP_TRIES = 3
STRENGTH = ["footage", "on_scene", "both_sides", "admitted", "analyst", "corroborated", "conceded", "described"]
BASIS_TEXT = {"footage": "geolocated footage", "on_scene": "reporting from the scene",
              "both_sides": "both sides acknowledge it", "analyst": "independent analysts' assessment",
              "admitted": "the side that lost it says so",
              "conceded": "the other side's own media describe it as held",
              "described": "independent outlets describe it as held"}


def add(fl: dict, found: list[dict], now) -> int:
    """File new claims in the ledger; returns how many were new."""
    added = 0
    cutoff = iso(now - timedelta(days=ledger.MEMORY_DAYS))
    for f in found:
        k = ledger.key(f["name"], f["country"])
        p = fl["places"].setdefault(k, {"name": f["name"], "region": f["region"], "country": f["country"],
                                        "conflict": f["conflict"], "lat": None, "lon": None, "tries": 0, "claims": []})
        for k2 in ("region", "local", "hint"):
            if not p.get(k2) and f.get(k2):
                p[k2] = f[k2]
        c = f["claim"]
        if any(x["url"] == c["url"] and x["actor"] == c["actor"] and x["change"] == c["change"] for x in p["claims"]):
            continue
        p["claims"].append(c)
        p["claims"] = sorted((x for x in p["claims"] if x["time"] >= cutoff), key=lambda x: x["time"])[-ledger.MAX_CLAIMS:]
        added += 1
    return added


def _pick(found: list, p: dict, conflict: dict, places: dict, geocoder):
    """The lookup result meant, or None when it can't be told apart from same-named places:
    near the event's own pin; else near the region the report names; else the only answer (all
    within SAME_KM); else, near the front (other settlements of this conflict on the map)."""
    cy, cx = conflict["center"]
    found = [c for c in found if haversine_km(c[0], c[1], cy, cx) <= conflict["radius_km"]]
    if not found:
        return None
    if p.get("hint"):
        near = sorted((haversine_km(c[0], c[1], *p["hint"]), c) for c in found)
        if near[0][0] <= HINT_KM:
            return near[0][1]
    if p.get("region"):
        anchor = geocoder.candidates(p["region"], None, p["country"])
        if anchor is None:
            return "later"  # no lookup budget left: decide next run
        if anchor:
            found = [c for c in found if haversine_km(c[0], c[1], *anchor[0]) <= REGION_KM]
            if not found:
                return None
    if all(haversine_km(c[0], c[1], *found[0]) <= SAME_KM for c in found):
        return found[0]
    front = [(q["lat"], q["lon"]) for q in places.values()
             if q is not p and q.get("published") and q.get("lat") is not None and q["conflict"] == p["conflict"]]
    if front:
        ranked = sorted((min(haversine_km(c[0], c[1], a, b) for a, b in front), c) for c in found)
        if ranked[0][0] <= SAME_KM and ranked[1][0] > ranked[0][0] + SAME_KM:
            return ranked[0][1]
    return None


def locate(fl: dict, conflicts: list[dict], geocoder) -> int:
    """Look up settlements not placed yet (English name, then the local-script name). A name shared
    by several places is placed only when the event's pin, the region or the front tells which."""
    placed = 0
    for p in list(fl["places"].values()):
        if p.get("lat") is not None or p.get("tries", 0) >= LOOKUP_TRIES:
            continue
        conflict = ledger.conflict_for(conflicts, p["country"], p["conflict"])
        if not conflict:
            continue
        found = geocoder.candidates(p["name"], p.get("region"), p["country"])
        if found == [] and p.get("local"):
            found = geocoder.candidates(p["local"], None, p["country"])
        if found is None:
            continue  # no lookup budget left this run, or the service failed: try again later
        got = _pick(found, p, conflict, fl["places"], geocoder) if found else None
        if got == "later":
            continue
        if got:
            p["lat"], p["lon"] = got[0], got[1]
            placed += 1
        else:
            p["tries"] = p.get("tries", 0) + 1
            if p["tries"] >= LOOKUP_TRIES:
                log(f"[frontline] could not place {p['name']} ({p.get('region') or p['country']}): "
                    f"{'not found' if not found else 'several places share the name'}; left off the map")
    return placed


def merge_spellings(fl: dict) -> int:
    """Settlements filed under two spellings ("Nesterne", "Nesternoye") that the lookup put at the
    same spot are one: the claims go to the record that has been on the map, else the older one."""
    placed = [(k, p) for k, p in fl["places"].items() if p.get("lat") is not None]
    gone = set()
    for i, (k1, a) in enumerate(placed):
        if k1 in gone:
            continue
        for k2, b in placed[i + 1:]:
            if k2 in gone or a["conflict"] != b["conflict"] or haversine_km(a["lat"], a["lon"], b["lat"], b["lon"]) > MERGE_KM:
                continue
            keep, drop = (a, b) if a.get("published") or not b.get("published") else (b, a)
            seen = {(c["url"], c["actor"], c["change"]) for c in keep["claims"]}
            keep["claims"] = sorted(keep["claims"] + [c for c in drop["claims"] if (c["url"], c["actor"], c["change"]) not in seen],
                                    key=lambda c: c["time"])[-ledger.MAX_CLAIMS:]
            keep.pop("asked", None)
            gone.add(k2 if drop is b else k1)
            if drop is a:
                break
    for k in gone:
        del fl["places"][k]
    return len(gone)


def holder_of(c: dict, conflict: dict) -> str | None:
    if c["change"] in ("took", "holds"):
        return c["actor"]
    if c["change"] == "lost":
        return ledger.other(conflict, c["actor"])
    return None


def _strength(c: dict, h: str | None, claims: list[dict], conflict: dict) -> str | None:
    """Why a claim is strong evidence for holder h (a BASIS_TEXT key, or "corroborated"), else None."""
    if not h:
        return None
    if c["basis"] in ledger.STRONG_BASES:
        return c["basis"]
    if c["change"] == "lost" and c.get("aligned") == c["actor"]:
        return "admitted"
    described = c["basis"] == ledger.DESCRIBED
    if described and c.get("aligned") and c["aligned"] != h:
        return "conceded"     # the other side's own media call it held by h ("occupied Melitopol")
    t = parse_time(c["time"])
    window = DESCRIBED_WINDOW if described else CORROBORATE
    same = [x for x in claims if x is not c and holder_of(x, conflict) == h
            and abs(parse_time(x["time"]) - t) <= window]
    if c.get("aligned") and any(x.get("aligned") and x["aligned"] != c["aligned"] for x in same):
        return "both_sides"   # opposing sides' sources agree
    groups = {x["group"] for x in same + [c]}
    if len(groups) >= 2 and any(not x.get("aligned") for x in same + [c]):
        return "described" if described and all(x["basis"] == ledger.DESCRIBED for x in same) else "corroborated"
    return None


def assess(p: dict, conflict: dict, now) -> dict | None:
    """What the evidence on one settlement adds up to: {holder, status, since, basis, sources, last, event}."""
    claims = [c for c in p["claims"] if not c.get("rejected")]
    if not claims:
        return None
    rows = [(c, holder_of(c, conflict)) for c in claims]
    strong = [(c, h, _strength(c, h, claims, conflict)) for c, h in rows]
    strong = [s for s in strong if s[2]]
    out = {"last": claims[-1]["time"]}
    if strong:
        c, h, why = strong[-1]
        since, reasons = c["time"], []
        for c2, h2, why2 in reversed(strong):
            if h2 != h:
                break
            since = c2["time"]
            reasons.append(why2)
        why = min(reasons, key=lambda r: STRENGTH.index(r))   # cite the strongest evidence
        newer = [(x, hx) for x, hx in rows if x["time"] > c["time"]]
        groups = {x["group"] for x, hx in rows if hx == h}
        out.update(holder=h, status="assessed", since=since, sources=len(groups), event=c.get("event"),
                   basis=BASIS_TEXT.get(why) or f"{len(groups)} independent sources")
    else:
        newer = rows
    rival = [(x, hx) for x, hx in newer if hx and hx != out.get("holder")]
    fighting = [x for x, hx in newer if x["change"] == "contested" and now - parse_time(x["time"]) <= CONTESTED_FOR]
    if rival:
        x, hx = rival[-1]
        first = x["time"]
        for y, hy in reversed(rival):  # since the first of the run of claims naming this holder
            if hy != hx:
                break
            first = y["time"]
        who = ledger.actor(conflict, x.get("aligned"))
        out.update(holder=hx, status="claimed", since=first, event=x.get("event"), previous=out.get("holder"),
                   sources=len({y["group"] for y, hy in rival if hy == hx}),
                   basis=f"{ledger.possessive(who['name'])} claim" if who else "unconfirmed reports")
    elif fighting:
        out.update(status="contested", event=fighting[-1].get("event"), since=fighting[0]["time"],
                   basis="fighting reported inside", sources=len({x["group"] for x in fighting}))
        out.setdefault("holder", None)
    if "status" not in out:
        return None
    return out
