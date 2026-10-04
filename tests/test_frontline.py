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
    assert c["summary"] == 'Reuters writes "Russian-occupied Crimea"'


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
