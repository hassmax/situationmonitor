"""Front-line agents: claims are read once and kept as each side's word; the assessor separates
assessed control from a claim by fixed evidence rules; the reviewer gates every change; same-named
villages far from the war are not used."""
import json
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

def test_areas_shade_corroborated_control_only_and_split_halfway_between_sides():
    import mapshapes
    from frontline import cartographer
    conflict = {**UA, "area_km": 6, "actors": [{**UA["actors"][0], "controlled": "Russian-controlled"}, UA["actors"][1]]}
    P = lambda name, lat, lon, status, holder, previous=None: {
        "name": name, "conflict": "ukraine", "lat": lat, "lon": lon, "status": status, "holder": holder,
        "previous": previous, "last": "2026-10-03T00:00:00Z"}
    layers = cartographer.areas([P("A", 50.0, 37.0, "assessed", "RU"), P("B", 50.0, 37.12, "assessed", "UA"),
                                 P("C", 50.3, 37.4, "claimed", "RU"),                  # a claim alone: not shaded
                                 P("D", 50.3, 37.8, "claimed", "UA", previous="RU"),  # claimed back: stays Russian
                                 P("E", 50.05, 37.3, "contested", "UA")], conflict)   # fighting inside: stays Ukrainian
    by = {L["id"]: L for L in layers}
    assert set(by) == {"fl-ukraine-assessed-RU", "fl-ukraine-assessed-UA", "fl-ukraine-contested"}
    ru, ua = by["fl-ukraine-assessed-RU"], by["fl-ukraine-assessed-UA"]
    assert (ru["label"], ru["color"], ru["assessment"]) == ("Russian-controlled", "#e39b5b", True)
    assert ua["label"] == "Held by Ukraine"                                                 # no `controlled` given
    inside = mapshapes._inside
    assert inside(ru["polygons"], 37.0, 50.0) and not inside(ru["polygons"], 37.12, 50.0)
    assert inside(ua["polygons"], 37.12, 50.0)
    assert inside(ru["polygons"], 37.055, 50.0) and inside(ua["polygons"], 37.065, 50.0)   # the line runs halfway
    assert not inside(ru["polygons"], 37.0, 50.2)                                           # nothing far from a named place
    assert not inside(ru["polygons"], 37.4, 50.3)                                           # C: only claimed
    assert inside(ru["polygons"], 37.8, 50.3)                                               # D: until corroborated
    assert inside(ua["polygons"], 37.3, 50.05)                                              # E
    fought = by["fl-ukraine-contested"]                                                     # E, hatched on top
    assert (fought["label"], fought["style"]) == ("Contested", "infiltration")
    assert inside(fought["polygons"], 37.3, 50.05) and not inside(fought["polygons"], 37.0, 50.0)


# ----------------------------------------------------------------------------- standing control

UA_STANDING = {**UA, "area_km": 10, "reach_km": 35,
               "actors": [{**UA["actors"][0], "held_as": ["Russian", "Russia"], "plain_occupied": True, "home": ["RU"]},
                          {**UA["actors"][1], "held_as": ["Ukrainian"]}],
               "standing": [{"region": "Crimea", "whole": True, "places": ["Simferopol", "Kerch"]},
                            {"region": "Zaporizhzhia Oblast", "places": ["Melitopol", "Berdiansk/Berdyansk",
                                                                        {"name": "Zaporizhzhia", "city_only": True}]}]}


def test_outlets_describing_a_town_as_held_are_found_and_nothing_else_is():
    from frontline import standing
    towns = standing.towns(UA_STANDING)

    def found(text):
        return [(p["name"], a) for p, a, _ in standing.find(text, UA_STANDING, towns)]
    assert found("Explosions rock Russian-occupied Melitopol") == [("Melitopol", "RU")]
    assert found("Partisans in occupied Berdyansk") == [("Berdiansk", "RU")]               # other spelling, bare "occupied"
    assert found("Shelling of Ukrainian-held Zaporizhzhia city") == [("Zaporizhzhia", "UA")]
    assert found("Russian-occupied Zaporizhzhia nuclear plant") == []                      # a facility
    assert found("Shelling of Russian-occupied Zaporizhzhia") == []                        # also the oblast's name
    assert found("Drones hit Russian-held Melitopol district") == []                       # not the town
    assert found("Life in formerly occupied Melitopol") == []
    assert found("Fighting near Melitopol") == []
    assert found("A woman from the occupied town of Tokmak") == []                       # not listed: unknown
    assert found("Blasts in Russian-held port city of Berdiansk") == [("Berdiansk", "RU")]
    assert found("Strikes on Russian-held Berdiansk port city") == [("Berdiansk", "RU")]
    assert found("Strikes on Russian-held Berdiansk port") == []                         # the port, not the town
    assert found("Russian-held areas near Melitopol") == []
    assert found("Ukraine strikes the Russian stronghold of Melitopol") == [("Melitopol", "RU")]
    crimea = standing.find("Blasts in Russian-occupied Crimea", UA_STANDING, towns)
    assert [p["name"] for p, _, _ in crimea] == ["Crimea"]
    claims_ = standing._claims({"text": "Blasts in Russian-occupied Crimea", "time": "2026-10-02T00:00:00Z",
                                "url": "https://x/1", "source": "Reuters", "group": "reuters", "side": None},
                               UA_STANDING, towns, NOW)
    assert sorted(c["name"] for c in claims_) == ["Kerch", "Simferopol"]                   # the whole region
    c = claims_[0]["claim"]
    assert (c["change"], c["actor"], c["basis"], c["aligned"]) == ("holds", "RU", "described", None)
    assert c["summary"].startswith('Reuters writes "Russian-occupied Crimea", which covers all of Crimea')


def test_two_outlets_describing_a_town_as_held_make_it_assessed():
    def described(group, days_ago, aligned=None):
        c = claim("RU", "holds", basis="described", aligned=aligned, group=group, hours_ago=24 * days_ago)
        return c
    one = place(described("reuters", 1))
    assert assess.assess(one, UA_STANDING, NOW)["status"] == "claimed"                    # one outlet: unconfirmed
    two = place(described("reuters", 1), described("bbc", 30))
    got = assess.assess(two, UA_STANDING, NOW)
    assert (got["status"], got["holder"], got["basis"]) == ("assessed", "RU", "independent outlets describe it as held")
    far = place(described("reuters", 1), described("bbc", 59))
    assert assess.assess(far, UA_STANDING, NOW)["status"] == "claimed"                    # too far apart
    other_side = place(described("kyiv-independent", 1, aligned="UA"))
    assert assess.assess(other_side, UA_STANDING, NOW)["basis"] == "the other side's own media describe it as held"


def test_standing_towns_reach_further_stay_on_land_and_home_ground_is_not_shaded():
    import mapshapes
    from frontline import cartographer
    P = lambda name, lat, lon, status, holder, country="UA", standing=False: {
        "name": name, "conflict": "ukraine", "country": country, "lat": lat, "lon": lon, "status": status,
        "holder": holder, "standing": standing, "last": "2026-10-03T00:00:00Z"}
    inside = mapshapes._inside
    # Melitopol, assessed and listed: shades 35 km; a village only claimed: nothing
    layers = cartographer.areas([P("Melitopol", 46.85, 35.37, "assessed", "RU", standing=True),
                                 P("Village", 47.6, 36.6, "claimed", "RU")], UA_STANDING)
    assert len(layers) == 1
    held = layers[0]
    assert inside(held["polygons"], 35.37, 47.10)                 # ~28 km north of Melitopol
    assert not inside(held["polygons"], 35.37, 47.25)             # beyond reach
    assert not inside(held["polygons"], 35.6, 46.55)              # the Sea of Azov is not shaded
    assert not inside(held["polygons"], 36.6, 47.6)               # a claimed village isn't shaded
    # Russian-held villages in Russia are Russia's own ground: nothing to shade
    assert cartographer.areas([P("Tetkino", 51.27, 34.27, "assessed", "RU", country="RU")], UA_STANDING) == []
    # Ukrainian-held Sumy's zone stops at the border
    sumy = cartographer.areas([P("Sumy", 50.91, 34.80, "assessed", "UA", standing=True)], {**UA_STANDING, "reach_km": 45})[0]
    assert inside(sumy["polygons"], 34.80, 50.95) and not inside(sumy["polygons"], 35.1, 51.18)   # Russia, ~36 km


def test_where_provinces_fill_a_held_town_shades_its_whole_province():
    import mapshapes
    from frontline import cartographer
    yemen = {"id": "yemen", "name": "Yemen", "countries": ["YE"], "center": [15.3, 45.0], "radius_km": 900,
             "area_km": 10, "reach_km": 35, "fill": "regions",
             "actors": [{"id": "HOUTHI", "name": "the Houthis", "controlled": "Houthi-controlled", "color": "#e39b5b"},
                        {"id": "ROYG", "name": "government forces", "controlled": "Government-controlled", "color": "#5aa9e6"}]}
    P = lambda name, lat, lon, holder, standing=True, front=False, provinces=(): {
        "name": name, "conflict": "yemen", "country": "YE", "lat": lat, "lon": lon, "status": "assessed",
        "holder": holder, "standing": standing, "fills": standing and not front, "provinces": provinces,
        "last": "2026-10-03T00:00:00Z"}
    layers = cartographer.areas([P("Saada", 16.94, 43.76, "HOUTHI"), P("Ibb", 13.97, 44.18, "HOUTHI"),
                                 P("Dhamar", 14.54, 44.40, "HOUTHI", provinces=["Raymah"]),
                                 P("Marib", 15.46, 45.32, "ROYG", front=True),
                                 P("Aden", 12.80, 45.03, "ROYG"), P("Ataq", 14.54, 46.83, "ROYG", standing=False)], yemen)
    by = {L["label"]: L for L in layers}
    inside = mapshapes._inside
    houthi, gov = by["Houthi-controlled"], by["Government-controlled"]
    assert inside(houthi["polygons"], 43.3, 17.3)        # ~60 km from Saada, still Sa'dah governorate
    assert inside(houthi["polygons"], 44.4, 13.8)        # Ibb governorate
    assert not inside(houthi["polygons"], 44.2, 15.35)   # Sanaa: no town there assessed
    assert inside(gov["polygons"], 45.0, 12.9)           # Aden
    assert not inside(gov["polygons"], 47.6, 14.4)       # Shabwah: Ataq isn't a listed town, so only its reach
    assert inside(houthi["polygons"], 43.75, 14.65)      # Raymah: no town of its own, filled from Dhamar
    assert inside(gov["polygons"], 45.32, 15.40)         # Marib, on the front: only its reach...
    assert not inside(gov["polygons"], 46.3, 15.6)       # ...not the whole governorate


# ----------------------------------------------------------------------------- ISW reader

class _Resp:
    def __init__(self, text):
        self.text, self.ok = text, True

    def raise_for_status(self):
        pass


class _Session:
    def __init__(self, pages):
        self.pages, self.asked = pages, []

    def get(self, url, **kw):
        self.asked.append(url)
        return _Resp(self.pages[url])


def test_isw_reports_are_read_for_control_claims_credited_to_isw_without_its_text():
    from frontline import isw
    roca = "https://understandingwar.org/research/russia-ukraine/russian-offensive-campaign-assessment-october-2-2026/"
    old = "https://understandingwar.org/research/russia-ukraine/russian-offensive-campaign-assessment-august-2-2026/"
    pages = {
        isw.SITEMAP_INDEX: "<loc>https://understandingwar.org/post-sitemap6.xml</loc><loc>https://understandingwar.org/map-sitemap22.xml</loc>",
        "https://understandingwar.org/post-sitemap6.xml":
            f"<loc>{old}</loc><loc>{roca}</loc><loc>https://understandingwar.org/research/china-taiwan/china-taiwan-update-october-2-2026/</loc>"
            "<loc>https://understandingwar.org/research/russia-ukraine/russian-offensive-campaign-assessment-updates-september-2026-present/</loc>",
        roca: "<html><nav>Menu</nav><article><p>Geolocated footage published on October 1 indicates that Russian forces recently "
              "seized Ulanove in Sumy Oblast.[12]</p><p>Partisans struck a depot in occupied Melitopol overnight, sources said today.</p>"
              "<p>The Kremlin continued its information operations about negotiations this week.</p></article></html>",
    }
    sess = _Session(pages)
    seen = []

    def ask(system, user, state, settings, now, **kw):
        seen.append((kw.get("purpose"), user))
        return {"reports": [{"i": 0, "claims": [{"settlement": "Ulanove", "region": "Sumy Oblast", "country": "UA",
                                                 "conflict": "ukraine", "change": "took", "actor": "RU", "claimed_by": None,
                                                 "basis": "footage", "date": "2026-10-01", "note": "footage shows Russian troops in the village"}]}]}
    state = {}
    conflicts = [{**UA_STANDING}]
    found = isw.run(conflicts, state, {}, sess, NOW, ask, budget=5)
    assert [p for p, _ in seen] == ["frontline_isw"]
    sent = json.loads(seen[0][1])["reports"]
    assert len(sent) == 2 and "Kremlin" not in json.dumps(sent)        # only sentences about control
    took = [f for f in found if f["name"] == "Ulanove"][0]["claim"]
    assert (took["basis"], took["group"], took["aligned"], took["url"]) == ("footage", "isw", None, roca)
    assert took["summary"] == "ISW: footage shows Russian troops in the village"   # the model's words, not ISW's
    described = [f for f in found if f["name"] == "Melitopol"][0]["claim"]
    assert (described["basis"], described["actor"], described["group"]) == ("described", "RU", "isw")
    assert old not in sess.asked                                         # older than the backfill
    # read once: the next run asks nothing more about it, and the sitemap waits an hour
    seen.clear()
    assert isw.run(conflicts, state, {}, sess, NOW + timedelta(minutes=15), ask, budget=5) == [] and not seen


def test_descriptions_of_a_whole_region_set_aside_by_the_old_review_rule_come_back(monkeypatch):
    c = claim("RU", "holds", basis="described", group="reuters")
    c.update(summary='Reuters writes "occupied Crimea"', rejected="mentions Crimea generally")
    other = claim("RU", "took", group="tass")
    other["rejected"] = "fighting near it"
    state = {"frontline": {"places": {"ua:saky": {**place(c, other), "name": "Saky", "asked": 2}}, "read": {}, "scout": {}}}
    frontline.update([], [UA], state, {}, NOW, lambda *a, **k: None, None, lambda purpose: 0, disabled=True)
    p = state["frontline"]["places"]["ua:saky"]
    got = [x for x in p["claims"] if x["basis"] == "described"][0]
    assert "rejected" not in got and got["summary"].endswith("which covers all of that region, Saky included")
    assert [x for x in p["claims"] if x["basis"] != "described"][0]["rejected"] == "fighting near it"   # others stay
    assert "asked" not in p and state["frontline"]["review_version"] == frontline.REVIEW_VERSION


def test_the_first_pass_searches_more_whole_regions_first_and_long_lists_get_more_turns():
    from frontline import standing
    small = {**UA, "id": "yemen", "countries": ["YE"], "standing": [{"region": "north", "places": ["Sanaa", "Ibb"]}]}
    fl = {"standing": {}}
    got = standing.due([UA_STANDING, small], fl, NOW)
    assert len(got) == min(standing.FIRST_PASS, 8)                      # all 8 listed entries at once
    assert got[0][1]["name"] == "Crimea"                                   # covers every Crimean town
    names = [t["name"] for c, t in got]
    assert "Sanaa" in names and sum(c["id"] == "ukraine" for c, _ in got) > sum(c["id"] == "yemen" for c, _ in got)
    for c, t in got:
        fl["standing"][t["search_key"]] = "2026-10-03T12:00:00Z"
    assert len(standing.due([UA_STANDING, small], fl, NOW)) <= standing.PER_RUN   # first pass done: back to PER_RUN


def test_the_reviewer_makes_a_second_call_while_many_changes_wait():
    from frontline import review
    places = {f"ua:v{i}": {**place(claim("RU", "took", basis="footage")), "name": f"V{i}"} for i in range(review.PER_CALL + 5)}
    fl = {"places": places}
    pending = [(k, {"holder": "RU", "status": "assessed", "since": "2026-10-03T10:00:00Z", "basis": "geolocated footage",
                    "last": "2026-10-03T10:00:00Z"}) for k in places]
    asked = []

    def ask(system, user, *a, **k):
        items = json.loads(user)["items"]
        asked.append(len(items))
        return {"items": [{"n": it["n"], "verdict": "confirm", "reason": "footage"} for it in items]}
    counts = review.run(pending, [UA], fl, {}, {}, NOW, ask, budget=5)
    assert asked == [review.PER_CALL, 5] and counts["confirm"] == review.PER_CALL + 5 and counts["waiting"] == 0
    assert review.run(pending[:3], [UA], {"places": places}, {}, {}, NOW, ask, budget=5)["confirm"] == 3
    assert asked[-1] == 3 and len(asked) == 3                                          # few waiting: one call


# ----------------------------------------------------------------------------- capture headlines (2026-10-05)

DRC = {"id": "drc", "name": "Eastern DRC", "countries": ["CD"], "center": [-1.5, 28.9], "radius_km": 500,
       "search_context": "Congo OR M23", "search_french": True,
       "actors": [{"id": "M23", "name": "AFC/M23", "color": "#e39b5b", "sources": ["RW"],
                   "held_as": ["M23", "AFC/M23", "rebel", "Rwandan army", "armée rwandaise"]},
                  {"id": "FARDC", "name": "Congolese forces", "color": "#5aa9e6", "sources": ["CD"],
                   "held_as": ["army", "FARDC", "armée"]}],
       "standing": [{"region": "North Kivu", "places": ["Goma", "Masisi"]}, {"region": "South Kivu", "places": ["Bukavu"]}]}


def test_the_side_named_after_the_town_counts_in_english_and_french():
    from frontline import standing
    towns = standing.towns(DRC)

    def found(text):
        return [(p["name"], a) for p, a, _ in standing.find(text, DRC, towns)]
    assert found("27 children killed in a fire in Bukavu, occupied by the Rwandan army") == [("Bukavu", "M23")]
    assert found("A Goma, ville sous contrôle de l’AFC/M23, la rentrée scolaire") == [("Goma", "M23")]
    assert found("Incendie à Bukavu occupée par l’armée rwandaise") == [("Bukavu", "M23")]   # the longer name wins over "armée"
    assert found("Goma, under M23 control since January") == [("Goma", "M23")]
    assert found("Masisi remains under the control of the army") == [("Masisi", "FARDC")]
    assert found("Goma, the M23-held provincial capital") == [("Goma", "M23")]
    assert found("Fighting near Goma, held by M23 until") == [("Goma", "M23")]
    assert found("Goma, formerly held by M23") == []
    assert found("Ships seized near Goma by the army") == []
    towns_ua = standing.towns(UA_STANDING)
    assert [p["name"] for p, _, _ in standing.find("Zaporizhzhia, held by Ukraine", UA_STANDING, towns_ua)] == []   # also the oblast


class FakeFeeds:
    """Google News searches answered from canned RSS, by edition."""

    def __init__(self, en, fr=""):
        self.en, self.fr, self.urls = en, fr, []

    def get(self, url, timeout=None):
        self.urls.append(url)
        body = self.fr if "hl=fr" in url else self.en

        class R:
            content = body.encode()

            def raise_for_status(self):
                pass
        return R()


def _rss(*items):
    rows = "".join(f"<item><title>{t} - {s}</title><link>https://news.google.com/{abs(hash(t))}</link>"
                   f"<pubDate>{d}</pubDate><source url=\"https://{h}\">{s}</source></item>" for t, s, h, d in items)
    return f"<rss><channel>{rows}</channel></rss>"


def test_searches_add_context_and_french_and_queue_capture_headlines(monkeypatch):
    from frontline import standing
    monkeypatch.setattr(standing, "PAUSE", 0)
    monkeypatch.setattr(standing, "FIRST_PASS", 1)
    day = "Fri, 02 Oct 2026 10:00:00 GMT"
    en = _rss(("Congolese army retakes Masisi from M23 rebels", "Reuters", "reuters.com", day),
              ("Masisi court jails two for theft", "Local", "local.cd", day),                     # no capture word
              ("Rebels seize Kitshanga near Masisi", "AP", "apnews.com", day),
              ("M23 rebels seize Kazinga village", "AP", "apnews.com", day),                        # the war's own words
              ("Police seize stolen cars in Kinshasa", "AP", "apnews.com", day),                    # neither
              ("Goma, under M23 control, reopens schools", "AP", "apnews.com", day))
    fr = _rss(("Masisi : les FARDC reprennent le contrôle de la cité", "Actualite.cd", "actualite.cd", day))
    session = FakeFeeds(en, fr)
    state = {"frontline": {"places": {}, "read": {}, "standing": {}, "standing_version": standing.SEARCH_VERSION}}
    small = {**DRC, "standing": [{"region": "North Kivu", "places": ["Masisi"]}]}
    standing.run([small], state, session, NOW, [], {})
    assert len(session.urls) == 2 and "hl=fr" in session.urls[1]
    assert "Congo" in session.urls[0] and "seized" in session.urls[0]                            # context and capture words
    news = state["frontline"]["news"]
    heads = sorted(x["headline"] for x in news["queue"])
    assert heads == ["Congolese army retakes Masisi from M23 rebels", "M23 rebels seize Kazinga village",
                     "Masisi : les FARDC reprennent le contrôle de la cité", "Rebels seize Kitshanga near Masisi"]   # the outlet's name cut off
    assert all(x["town"] == "Masisi" and x["conflict"] == "drc" for x in news["queue"])
    standing.run([small], {**state, "frontline": {**state["frontline"], "standing": {}}}, session, NOW, [], {})
    assert len(news["queue"]) == 4                                                                 # each headline queued once


def test_an_old_search_version_searches_every_town_again():
    from frontline import standing
    fl = {"places": {}, "read": {}, "standing": {"cd:goma": "2026-10-03T00:00:00Z"}}
    standing.run([DRC], {"frontline": fl}, FakeFeeds(_rss()), NOW, [], {})
    assert fl["standing_version"] == standing.SEARCH_VERSION and "cd:goma" in fl["standing"]


def test_capture_headlines_become_claims_credited_to_the_outlet_with_the_models_note():
    fl = {"places": {}, "read": {}}
    state = {"frontline": fl}
    news = fl.setdefault("news", {"queue": [], "seen": {}})
    t = (NOW - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    for i, (head, src, side) in enumerate([("Congolese army retakes Masisi from M23 rebels", "Reuters (via Google News)", None),
                                           ("M23 says it captured Masisi", "KT Press (via Google News)", "RW")]):
        news["queue"].append({"key": f"k{i}", "conflict": "drc", "town": "Masisi", "country": "CD", "region": "North Kivu",
                              "headline": head, "time": t, "url": f"https://x/{i}", "source": src, "group": f"g{i}", "side": side})
    seen = {}

    def ask(system, user, *a, purpose=None, **k):
        seen["purpose"], seen["user"], seen["system"] = purpose, json.loads(user), system
        return {"reports": [
            {"i": 0, "claims": [{"settlement": "Masisi", "country": "CD", "conflict": "drc", "change": "took", "actor": "FARDC",
                                 "claimed_by": None, "basis": "unattributed", "date": None, "note": "army retook the town"}]},
            {"i": 1, "claims": [{"settlement": "Masisi", "country": "CD", "conflict": "drc", "change": "took", "actor": "M23",
                                 "claimed_by": "M23", "basis": "party", "date": None, "note": "M23 claims capture"}]}]}
    got = claims.run_news([DRC], state, {}, NOW, ask, 5)
    assert seen["purpose"] == "frontline_news" and "headline" in seen["system"]
    assert seen["user"]["reports"][0]["headline"] == "Congolese army retakes Masisi from M23 rebels"
    assert seen["user"]["reports"][1]["source_speaks_for"] == "M23"
    assert [(c["name"], c["claim"]["actor"], c["claim"]["aligned"]) for c in got] == [("Masisi", "FARDC", None), ("Masisi", "M23", "M23")]
    assert got[0]["claim"]["summary"] == "Reuters: army retook the town"                         # never the headline
    assert got[0]["claim"]["via"] == "news" and news["queue"] == []
    assert claims.run_news([DRC], state, {}, NOW, ask, 5) == []                                  # read once


def test_capture_headlines_are_taken_in_turn_across_conflicts():
    q = [{"conflict": "ukraine", "time": f"2026-10-0{d}T00:00:00Z", "key": f"u{d}"} for d in range(1, 6)]
    q += [{"conflict": "drc", "time": "2026-09-20T00:00:00Z", "key": "d1"}]
    order = [x["key"] for x in claims._news_order(q)]
    assert order[:2] == ["u5", "d1"]                                                             # the older DRC one isn't last


# ----------------------------------------------------------------------------- Wikipedia baseline (2026-10-05)

WIKI_SRC = '''return { marks = {
  { lat = "15.369", long = "44.191", mark = "Dot green 0d0.svg", marksize = 35, label = "[[Sanaa]]", link = "Sanaa" },
  { lat = "12.8", long = "45.03", mark = "Location dot red.svg", marksize = 28, label = "[[Aden]]" },
  { lat = "13.58", long = "44.02", mark = "80x80-red-lime-anim.gif", label = "[[Taiz]]" },
  { lat = "15.46", long = "45.32", mark = "map-circle-red.svg", label = "[[Marib Governorate|Marib]]" },
  { lat = "15.0", long = "44.0", mark = "Map-peak-lime.svg", label = "Jabal X" },
  { lat = "14.0", long = "48.0", mark = "Map-dot-grey-68a.svg", label = "[[Azzan]]" },
  { lat = "14.9", long = "43.4", mark = "Abm-lime-icon.png", link = "Some base" },
}}'''
YE_WIKI = {"id": "yemen", "name": "Yemen", "countries": ["YE"], "center": [15.3, 45.0], "radius_km": 900, "area_km": 10,
           "actors": [{"id": "HOUTHI", "name": "the Houthis", "color": "#e39b5b", "controlled": "Houthi-controlled", "sources": ["YE"]},
                      {"id": "ROYG", "name": "the government", "color": "#5aa9e6", "controlled": "Government-controlled", "sources": []}],
           "wikipedia": {"page": "Module:Yemeni Civil War detailed map",
                         "marks": {"Location dot red.svg": "ROYG", "Dot green 0d0.svg": "HOUTHI", "map-circle-red.svg": "ROYG"}}}


def test_wikipedia_dots_become_places_by_the_legend_and_nothing_else_does():
    from frontline import wikipedia
    got = {p["name"]: (p["holder"], p["status"]) for p in wikipedia.parse(WIKI_SRC, YE_WIKI["wikipedia"])}
    assert got == {"Sanaa": ("HOUTHI", "assessed"), "Aden": ("ROYG", "assessed"), "Taiz": (None, "contested"),
                   "Marib": ("ROYG", "assessed")}                                   # peaks, bases, untracked sides left out
    pts = [{"name": "A", "lat": 13.0, "lon": 39.0, "holder": "ENDF"}, {"name": "B", "lat": 13.5, "lon": 39.5, "holder": "TPLF"},
           {"name": "C", "lat": 9.0, "lon": 38.7, "holder": "ENDF"}]
    near = wikipedia._near_only(pts, {"actors": ["ENDF"], "km": 100})
    assert [p["name"] for p in near] == ["A", "B"]                                  # Addis Ababa is far from the fighting


class FakeWiki:
    def __init__(self, fail=False):
        self.fail, self.calls = fail, 0

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls += 1
        if self.fail:
            raise OSError("down")
        body = WIKI_SRC if params.get("action") == "raw" else json.dumps(
            {"query": {"pages": {"1": {"revisions": [{"timestamp": "2026-10-04T23:47:30Z"}]}}}})

        class R:
            text = body

            def raise_for_status(self):
                pass

            def json(self):
                return json.loads(body)
        return R()


def test_the_wikipedia_maps_are_read_once_and_the_agents_evidence_wins():
    from frontline import cartographer, wikipedia
    state = {"frontline": {"places": {}, "read": {}}}
    assert wikipedia.run([YE_WIKI], {"frontline": {"places": {}}}, FakeWiki(fail=True), NOW) == 0   # tried again next run
    session = FakeWiki()
    assert wikipedia.run([YE_WIKI], state, session, NOW) == 1
    calls = session.calls
    assert wikipedia.run([YE_WIKI], state, session, NOW) == 0 and session.calls == calls             # once
    base = state["frontline"]["baseline"]["maps"]["yemen"]
    assert {p["name"]: p["country"] for p in base["points"]} == {"Sanaa": "YE", "Aden": "YE", "Taiz": "YE", "Marib": "YE"}
    # a settlement the agents published near Aden: its own evidence, not Wikipedia's dot, shades there
    mine = [{"name": "Aden", "conflict": "yemen", "lat": 12.79, "lon": 45.02}]
    names = [p["name"] for p in wikipedia.points(state["frontline"], [YE_WIKI], mine)]
    assert "Aden" not in names and "Sanaa" in names
    credit = wikipedia.credits(state["frontline"], [YE_WIKI])[0]
    assert credit["license"] == "CC BY-SA 4.0" and credit["edited"] == "2026-10-04" and "Yemeni_Civil_War" in credit["url"]
    pub = cartographer.public(state["frontline"], [YE_WIKI], NOW)
    labels = {L["label"]: L["source"] for L in pub["areas"]}
    assert labels["Houthi-controlled"] == "Wikipedia's conflict map" and "Contested" in labels
    assert pub["credits"] and pub["places"] == []                                 # the dots aren't published as places
    later = NOW + wikipedia.MAX_AGE + timedelta(days=1)
    wikipedia.run([YE_WIKI], state, session, later)
    assert state["frontline"]["baseline"]["maps"] == {} and session.calls == calls    # dropped, not read again


UA_OVERVIEW = '''-- Marker shortcuts
mk = {
	con = "80x80-red-blue-anim.gif",
	grz = "Location dot grey.svg",
	rus = "Location dot red.svg",
	ukr = "Location dot blue.svg",
	rEE = "Map-arcEE-red.svg",
}
lp = { b = "bottom", l = "left" }
return { marks = {
  { lat = "46.305", long = "31.102", mark = "Ukraine Roadmap Overlay.png", marksize = 2600 },
  { lat = "46.848", long = "35.365", mark = mk.rus, marksize = 16, position = lp.b, label = "[[Melitopol]]" },
  { lat = "48.353", long = "37.210", mark = mk.con, marksize = 8, label = "[[Rodynske]]" },
}}'''
UA_DETAILED = '''local m = require('Module:Russo-Ukrainian war overview map')
local marks = {
  { lat = "48.249", long = "37.782", mark = mk.rus, marksize = 4--[[857]], position = "none", label = "[[Novobakhmutivka, Novobakhmut Village Council|Novobakhmutivka]]" },
  { lat = "48.5", long = "37.4", mark = mk.ukr, marksize = 4, label = "[[Druzhkivka]]" },
  { lat = "50.4", long = "30.5", mark = mk.ukr, marksize = 35, label = "[[Kyiv]]" },
  { lat = "48.6", long = "37.9", mark = mk.rEE, marksize = 12 },
  { lat = "46.848", long = "35.365", mark = mk.rus, marksize = 16, label = "[[Melitopol]]" },
}'''


def test_dots_named_through_a_lookup_table_are_read_across_both_pages():
    from frontline import wikipedia
    cfg = {"marks": {"Location dot red.svg": "RU", "Location dot blue.svg": "UA"}, "near": {"actors": ["UA"], "km": 40}}
    got = wikipedia._near_only(wikipedia.parse(UA_DETAILED + "\n" + UA_OVERVIEW, cfg), cfg["near"])
    assert {p["name"]: (p["holder"], p["status"]) for p in got} == {
        "Novobakhmutivka": ("RU", "assessed"), "Druzhkivka": ("UA", "assessed"), "Melitopol": ("RU", "assessed"),
        "Rodynske": (None, "contested")}                       # Kyiv is far from the front; arcs and the road map left out
    assert [p["name"] for p in got].count("Melitopol") == 1     # listed on both pages, kept once
