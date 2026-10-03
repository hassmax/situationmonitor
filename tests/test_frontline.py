"""Front-line agents: claims are read once and kept as each side's word; the assessor separates
assessed control from a claim by fixed evidence rules; the reviewer gates every change; same-named
villages far from the war are not used."""
from datetime import datetime, timedelta, timezone

import frontline
from frontline import assess, claims, ledger

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
CONFLICTS = [
    {"id": "ukraine", "name": "Russia–Ukraine", "countries": ["UA", "RU"], "center": [48.4, 36.5], "radius_km": 700,
     "actors": [{"id": "RU", "name": "Russia", "color": "#e39b5b", "sources": ["RU"]},
                {"id": "UA", "name": "Ukraine", "color": "#5aa9e6", "sources": ["UA"]}]},
]
UA = CONFLICTS[0]


def report(source, side, summary, hours_ago=2, url=None):
    return {"source": source, "side": side, "group": source, "url": url or f"https://x/{abs(hash(summary))}",
            "time": (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ"), "summary": summary}


def event(eid, reports, place="Ulanove", type_="territory"):
    return {"id": eid, "type": type_, "country": "UA", "place": place, "reports": reports}


def claim(actor, change, basis="party", aligned=None, group="g", hours_ago=2, claimed_by=None):
    t = (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"time": t, "actor": actor, "change": change, "claimed_by": claimed_by, "basis": basis, "aligned": aligned,
            "group": group, "source": group, "url": f"https://x/{group}/{hours_ago}/{change}", "summary": f"{actor} {change}",
            "event": "e1"}


def place(*cs):
    return {"name": "Ulanove", "region": "Sumy Oblast", "country": "UA", "conflict": "ukraine", "lat": 51.2, "lon": 34.6,
            "claims": sorted(cs, key=lambda c: c["time"])}


# ----------------------------------------------------------------------------- claims agent

class Model:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def __call__(self, system, text, state, settings, now, max_tokens=4000, purpose="other", images=None):
        self.calls.append(purpose)
        return self.reply


def test_claims_are_read_once_and_keep_whose_word_they_are():
    ev = [event("e1", [report("Russian Ministry of Defence", "RU", "Russian MoD claims Sever grouping forces established control over Ulanove in Sumy Oblast.")])]
    model = Model({"reports": [{"i": 0, "claims": [
        {"settlement": "Ulanove", "region": "Sumy Oblast", "country": "UA", "conflict": "ukraine", "change": "took",
         "actor": "RU", "claimed_by": "RU", "basis": "party", "date": "2026-10-02"},
        {"settlement": "Somewhere", "country": "UA", "conflict": "ukraine", "change": "took", "actor": "NATO", "basis": "party"},
    ]}]})
    state = {}
    found = claims.run(ev, CONFLICTS, state, {}, NOW, model, budget=1)
    assert len(found) == 1 and model.calls == ["frontline"]          # an unknown side is dropped
    c = found[0]["claim"]
    assert (c["actor"], c["aligned"], c["basis"], c["time"][:10]) == ("RU", "RU", "party", "2026-10-02")
    assert claims.run(ev, CONFLICTS, state, {}, NOW, model, budget=1) == [] and len(model.calls) == 1   # read once


def test_no_budget_no_call_and_unrelated_events_are_not_read():
    ev = [event("e1", [report("BBC", None, "Fighting in Ulanove.")]),
          {"id": "e2", "type": "airstrike", "country": "UA", "reports": [report("BBC", None, "Strike on Kyiv.")]},
          {"id": "e3", "type": "ground", "country": "FR", "reports": [report("BBC", None, "Riot in Paris.")]}]
    model = Model({"reports": []})
    assert claims.run(ev, CONFLICTS, {}, {}, NOW, model, budget=0) == [] and not model.calls
    fl = ledger.state_of({})
    assert len(claims.waiting(ev, CONFLICTS, fl, NOW)) == 1


# ----------------------------------------------------------------------------- assessor rules

def test_one_sides_word_is_a_claim_footage_makes_it_assessed():
    p = place(claim("RU", "took", aligned="RU", claimed_by="RU", group="Russian MoD"))
    got = assess.assess(p, UA, NOW)
    assert (got["holder"], got["status"], got["basis"]) == ("RU", "claimed", "Russia's claim")
    p = place(claim("RU", "took", aligned="RU", claimed_by="RU", group="Russian MoD", hours_ago=5),
              claim("RU", "took", basis="footage", group="geolocation", hours_ago=2))
    got = assess.assess(p, UA, NOW)
    assert (got["holder"], got["status"], got["basis"]) == ("RU", "assessed", "geolocated footage")


def test_two_independent_sources_or_an_admission_confirm_a_capture():
    p = place(claim("RU", "took", basis="unattributed", group="Reuters", hours_ago=10),
              claim("RU", "took", basis="unattributed", group="AP", hours_ago=4))
    assert assess.assess(p, UA, NOW)["status"] == "assessed"
    p = place(claim("UA", "lost", aligned="UA", claimed_by="UA", group="Ukrainian General Staff"))
    got = assess.assess(p, UA, NOW)
    assert (got["holder"], got["status"], got["basis"]) == ("RU", "assessed", "the side that lost it says so")
    # two outlets aligned with the same side are still that side's word
    p = place(claim("RU", "took", aligned="RU", group="TASS", hours_ago=6), claim("RU", "took", aligned="RU", group="RIA", hours_ago=3))
    assert assess.assess(p, UA, NOW)["status"] == "claimed"


def test_a_newer_rival_claim_or_fighting_inside_changes_the_status():
    base = claim("UA", "holds", basis="footage", group="geolocation", hours_ago=48)
    got = assess.assess(place(base, claim("RU", "took", aligned="RU", claimed_by="RU", group="Russian MoD", hours_ago=3)), UA, NOW)
    assert (got["holder"], got["status"], got["previous"]) == ("RU", "claimed", "UA")
    got = assess.assess(place(base, claim("RU", "contested", group="Kyiv Independent", hours_ago=3)), UA, NOW)
    assert (got["holder"], got["status"]) == ("UA", "contested")
    got = assess.assess(place(base, claim("RU", "contested", group="Kyiv Independent", hours_ago=24 * 9)), UA, NOW)
    assert got["status"] == "assessed"                                  # old fighting no longer counts


class Geo:
    """A lookup service with fixed answers by name (default: one spot near Sumy)."""
    def __init__(self, answers=None, default=None):
        self.answers, self.default, self.asked = answers or {}, default if default is not None else [[51.2, 34.6]], []

    def candidates(self, name, region, country):
        self.asked.append(name)
        return self.answers.get(name, self.default)


def new_place(fl, name, region=None, hint=None, local=None):
    assess.add(fl, [{"name": name, "region": region, "country": "UA", "conflict": "ukraine", "hint": hint, "local": local,
                     "claim": claim("RU", "contested", group=name)}], NOW)
    return fl["places"][ledger.key(name, "UA")]


def test_a_same_named_village_is_placed_only_when_the_region_or_the_event_says_which():
    two = [[46.6, 32.7], [49.93, 36.1]]                                   # an Oleshky in Kherson and one near Kharkiv
    fl = ledger.state_of({})
    p = new_place(fl, "Oleshky", region="Kherson Oblast")
    assess.locate(fl, CONFLICTS, Geo({"Oleshky": two, "Kherson Oblast": [[46.75, 33.35]]}))
    assert (p["lat"], p["lon"]) == (46.6, 32.7)
    fl = ledger.state_of({})
    p = new_place(fl, "Oleshky", hint=[46.62, 32.71])                    # the event's own pin
    assess.locate(fl, CONFLICTS, Geo({"Oleshky": two}))
    assert (p["lat"], p["lon"]) == (46.6, 32.7)
    fl = ledger.state_of({})
    p = new_place(fl, "Oleshky")                                          # nothing says which: not guessed
    assess.locate(fl, CONFLICTS, Geo({"Oleshky": two}))
    assert p["lat"] is None and p["tries"] == 1
    fl = ledger.state_of({})
    p = new_place(fl, "Far")
    assess.locate(fl, CONFLICTS, Geo({"Far": [[48.0, 22.5]]}))           # only one, but outside the war's radius
    assert p["lat"] is None


def test_the_local_name_is_tried_and_two_spellings_of_one_place_merge():
    fl = ledger.state_of({})
    p = new_place(fl, "Kupiansk-Vuzlovyi", local="Куп'янськ-Вузловий")
    assess.locate(fl, CONFLICTS, Geo({"Kupiansk-Vuzlovyi": [], "Куп'янськ-Вузловий": [[49.68, 37.66]]}))
    assert (p["lat"], p["lon"]) == (49.68, 37.66)
    new_place(fl, "Nesterne")
    new_place(fl, "Nesternoye")
    assess.locate(fl, CONFLICTS, Geo({"Nesterne": [[50.416, 37.385]], "Nesternoye": [[50.417, 37.386]]}))
    assert assess.merge_spellings(fl) == 1
    kept = [q for q in fl["places"].values() if q["lat"] == 50.416 or q["lat"] == 50.417]
    assert len(kept) == 1 and len(kept[0]["claims"]) == 2


# ----------------------------------------------------------------------------- reviewer gate, whole round

def round_(state, reports, review_reply, claims_reply):
    calls = []

    def ask(system, text, state_, settings, now, max_tokens=4000, purpose="other", images=None):
        calls.append(purpose)
        return claims_reply if purpose == "frontline" else review_reply
    frontline.update([event("e1", reports)], CONFLICTS, state, {}, NOW, ask, Geo(), lambda purpose: 1)
    return calls


CLAIM = {"reports": [{"i": 0, "claims": [{"settlement": "Ulanove", "region": "Sumy Oblast", "country": "UA",
                                         "conflict": "ukraine", "change": "took", "actor": "RU", "claimed_by": "RU", "basis": "party"}]}]}
MOD = report("Russian Ministry of Defence", "RU", "Russian MoD claims control over Ulanove.")


def test_nothing_is_published_until_the_reviewer_confirms_it():
    state = {}
    calls = round_(state, [MOD], None, CLAIM)                           # reviewer gives no answer
    p = state["frontline"]["places"]["ua:ulanove"]
    assert calls == ["frontline", "frontline_review"] and "published" not in p
    assert frontline.public(state, CONFLICTS, NOW)["places"] == []
    calls = round_(state, [MOD], {"items": []}, CLAIM)                 # an answer that skips it: left pending
    assert calls == ["frontline_review"] and p["asked"] == 1
    assert round_(state, [MOD], {"items": []}, CLAIM) == []             # not asked again on the same evidence
    p.pop("asked")
    round_(state, [MOD], {"items": [{"n": 0, "verdict": "confirm", "reason": "MoD claim, as stated"}]}, CLAIM)
    pub = frontline.public(state, CONFLICTS, NOW)["places"]
    assert [(x["name"], x["status"], x["holder_name"], x["basis"]) for x in pub] == [("Ulanove", "claimed", "Russia", "Russia's claim")]


def test_a_rejected_change_sets_its_claims_aside():
    state = {}
    round_(state, [MOD], {"items": [{"n": 0, "verdict": "reject", "reason": "fighting near it, not inside"}]}, CLAIM)
    p = state["frontline"]["places"]["ua:ulanove"]
    assert all(c.get("rejected") for c in p["claims"]) and "published" not in p


# ----------------------------------------------------------------------------- cartographer: areas

def test_areas_shade_around_settlements_and_split_halfway_between_sides():
    import mapshapes
    from frontline import cartographer
    conflict = {**UA, "area_km": 6}
    P = lambda name, lat, lon, status, holder: {"name": name, "conflict": "ukraine", "lat": lat, "lon": lon,
                                               "status": status, "holder": holder, "last": "2026-10-03T00:00:00Z"}
    layers = cartographer.areas([P("A", 50.0, 37.0, "assessed", "RU"), P("B", 50.0, 37.12, "assessed", "UA"),
                                 P("C", 50.3, 37.4, "claimed", "RU"), P("D", 50.05, 37.3, "contested", None)], conflict)
    by = {L["id"]: L for L in layers}
    assert [L["style"] for L in layers] == ["claimed", "occupied", "occupied", "infiltration"]   # paint order
    ru, ua = by["fl-ukraine-assessed-RU"], by["fl-ukraine-assessed-UA"]
    assert (ru["label"], ru["color"], ru["assessment"]) == ("Held by Russia", "#e39b5b", True)
    inside = mapshapes._inside
    assert inside(ru["polygons"], 37.0, 50.0) and not inside(ru["polygons"], 37.12, 50.0)
    assert inside(ua["polygons"], 37.12, 50.0)
    assert inside(ru["polygons"], 37.055, 50.0) and inside(ua["polygons"], 37.065, 50.0)   # the line runs halfway
    assert not inside(ru["polygons"], 37.0, 50.2)                                           # nothing far from a named place
    assert by["fl-ukraine-claimed-RU"]["label"] == "Claimed by Russia"
    assert by["fl-ukraine-contested-none"]["label"].startswith("Contested")
