from datetime import datetime, timedelta, timezone

import dedupe

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
SETTINGS = {"dedupe_min_calls": 10}


def ev(i, summary, type_="airstrike", country="PS", place="Gaza", hours_ago=3, lat=31.5, lon=34.45, **kw):
    t = (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"id": i, "theater": "mideast", "type": type_, "summary": summary, "place": place, "country": country,
            "lat": lat, "lon": lon, "approx": False, "severity": 2, "killed": None, "injured": None, "origins": [],
            "time": t, "updated": t, "reports": [{"url": f"u-{i}", "time": t, "summary": summary}], **kw}


def fresh():
    # one Gaza strike filed three ways (the map on 2026-10-06), a different strike, and unrelated news
    return [ev("a", "An Israeli strike killed one Palestinian in Gaza City.", "artillery", hours_ago=7),
            ev("b", "An Israeli strike killed one Palestinian in Gaza City.", "missile_drone", hours_ago=4),
            ev("c", "An Israeli strike killed one Palestinian in Gaza City.", "airstrike", hours_ago=1),
            ev("d", "Israeli tanks shelled homes in Khan Younis, wounding five.", "artillery", hours_ago=2),
            ev("x", "Germany holds talks on joining a fighter jet programme.", "diplomacy", "DE", "Berlin", 2,
               52.5, 13.4)]


def background():
    """Unrelated events elsewhere, as the map always has: rare words weigh by how rare they are on it."""
    lines = ["Russian drones struck energy facilities in Kharkiv overnight.", "Sudan's army retook a town in North Kordofan.",
             "Myanmar junta airstrike hit a village in Sagaing.", "Ukraine's air force downed 40 drones over Odesa.",
             "Houthi missile intercepted over the Red Sea.", "Somali forces raided an al-Shabaab camp near Baidoa.",
             "Chinese coast guard ships entered waters near Taiwan.", "NATO jets scrambled over the Baltic Sea.",
             "M23 rebels advanced near Uvira in South Kivu.", "Israeli troops raided Jenin in the West Bank.",
             "Pakistan's army killed militants in Waziristan.", "Mali's army reported an attack near Gao.",
             "Iran's foreign minister met his Omani counterpart in Muscat.", "The EU adopted new sanctions on Russia.",
             "Venezuela deployed troops to its eastern border.", "A cargo ship reported an explosion off Eritrea.",
             "Ethiopian forces clashed with Fano militias in Amhara.", "Turkey struck PKK targets in northern Iraq.",
             "South Korea held artillery drills near the border.", "Japan scrambled fighters as Russian bombers flew by."]
    return [ev(f"bg{i}", t, "ground", "ZZ", "Elsewhere", 10 + i, 0.0, -150.0 + i) for i, t in enumerate(lines)]


def reply_for(cases_seen, groups):
    """A model reply: for each case, the candidates in `groups` (by event id) judged the same."""
    out = []
    for c in cases_seen:
        same = [f["id"] for f in c["candidates"] if f["id"] in groups.get(c["event"]["id"], ())]
        out.append({"i": c["i"], "same": same, "type": "airstrike", "summary": "An Israeli strike killed one Palestinian in Gaza City."})
    return {"results": out}


class Model:
    def __init__(self, groups=None):
        self.groups, self.calls = groups or {}, []

    def __call__(self, system, user, *a, **k):
        import json
        cases = json.loads(user)["cases"]
        self.calls.append(cases)
        return reply_for(cases, self.groups)


def test_an_event_filed_under_different_kinds_is_compared_and_folded_into_one():
    model = Model({"c": {"a", "b"}})
    out, folded = dedupe.run(fresh(), {}, SETTINGS, NOW, model, remaining=100, skip=set())
    first = model.calls[0]
    c = next(x for x in first if x["event"]["id"] == "c")              # newest first
    assert {f["id"] for f in c["candidates"]} >= {"a", "b"} and c["event"]["kind"] == "airstrike"
    assert "x" not in {f["id"] for x in first for f in x["candidates"]}  # Germany: another country, other words
    ids = {e["id"] for e in out}
    assert "a" in ids and "b" not in ids and "c" not in ids               # the earliest keeps its id
    kept = next(e for e in out if e["id"] == "a")
    assert kept["type"] == "airstrike" and len(kept["reports"]) == 3 and {e["id"] for e in folded} == {"b", "c"}
    assert kept["headline"].startswith("An Israeli strike")


def test_each_pair_is_asked_once_and_close_pairs_get_one_second_look():
    state, model = {}, Model()
    dedupe.run(fresh(), state, SETTINGS, NOW, model, remaining=100, skip=set())
    asked = sum(len(c["candidates"]) for c in model.calls[0])
    assert asked == len(state["dedupe"]["judged"])                       # every pair recorded once
    dedupe.run(fresh(), state, SETTINGS, NOW + timedelta(hours=1), model, remaining=100, skip=set())
    assert len(model.calls) == 1                                         # nothing new to ask
    dedupe.run(fresh(), state, SETTINGS, NOW + timedelta(hours=7), model, remaining=100, skip=set())
    again = {(c["event"]["id"], f["id"]) for c in model.calls[1] for f in c["candidates"]}
    assert again and all(dedupe._score(*map(dedupe._vectors(fresh()).get, p)) >= dedupe.SECOND_LOOK_MIN for p in again)
    dedupe.run(fresh(), state, SETTINGS, NOW + timedelta(hours=14), model, remaining=100, skip=set())
    assert len(model.calls) == 2                                         # two answers: settled


def test_near_identical_reports_of_a_deal_or_move_are_folded_without_asking():
    move = [ev("f1", "All 12 U.S. B-1B Lancer bombers have left RAF Fairford in the UK.", "arms_transfer", "GB",
               "RAF Fairford", 5, 51.68, -1.79),
            ev("f2", "All 12 US B-1B Lancer bombers have departed RAF Fairford in the UK.", "deployment", "GB",
               "Fairford", 3, 51.68, -1.79),
            ev("s1", "Aselsan signed $2.75 billion in deals for air defense systems.", "production", "TR", "Ankara", 4, 39.9, 32.8),
            ev("s2", "Aselsan signed $1.2 billion in deals for air defense systems.", "production", "TR", "Ankara", 2, 39.9, 32.8)]
    model = Model()
    out, folded = dedupe.run(move + background(), {}, SETTINGS, NOW, model, remaining=100, skip=set())
    assert [e["id"] for e in folded] == ["f2"]                           # the bombers: one move
    assert {(x["event"]["id"], f["id"]) for c in model.calls for x in c for f in x["candidates"]} == {("s2", "s1")}
    assert "s2" in {e["id"] for e in out}                                # different sums: asked, not folded


def test_strikes_are_never_folded_without_the_model():
    two = [ev("a", "An Israeli strike killed one Palestinian in Gaza City.", hours_ago=5),
           ev("b", "An Israeli strike killed one Palestinian in Gaza City.", hours_ago=2)]
    out, folded = dedupe.run(two, {}, SETTINGS, NOW, Model(), remaining=100, skip=set())
    assert folded == [] and len(out) == 2                                # two strikes the same day can both be real


def test_events_at_sea_are_compared_by_distance_and_statements_by_party():
    sea = [ev("n1", "UKMTO reports a tanker struck by an unknown projectile in the Strait of Hormuz.", "naval", None,
              "Strait of Hormuz", 6, 26.6, 56.4),
           ev("n2", "UKMTO reports an oil tanker was hit by an unknown projectile near the Strait of Hormuz.",
              "missile_drone", "OM", "Musandam", 3, 26.2, 56.3)]
    talks = [ev("t1", "Germany began exploratory talks about joining the Global Combat Air Programme.", "diplomacy", "GB",
                "London", 9, 51.5, -0.1, parties=["DE", "GB"]),
             ev("t2", "Germany holds exploratory talks on joining the UK, Italian and Japanese Global Combat Air Programme.",
                "diplomacy", "DE", "Berlin", 4, 52.5, 13.4, parties=["DE", "GB", "IT", "JP"])]
    model = Model()
    dedupe.run(sea + talks, {}, SETTINGS, NOW, model, remaining=100, skip=set())
    pairs = {frozenset((c["event"]["id"], f["id"])) for c in model.calls[0] for f in c["candidates"]}
    assert frozenset(("n1", "n2")) in pairs and frozenset(("t1", "t2")) in pairs


def test_an_attack_wave_takes_in_a_duplicate_and_two_waves_stay_apart():
    wave = ev("w", "North Korea fired a hypersonic missile using AI into the East Sea.", "missile_drone", "KP",
              "East Sea", 6, 39.0, 129.0, wave=True, targets=[])
    test_ = ev("p", "North Korea tested a hypersonic missile using AI, launched into the East Sea.", "production", "KP",
               "Pyongyang", 3, 39.0, 125.7)
    other = ev("w2", "North Korea fired a hypersonic missile using AI into the East Sea.", "missile_drone", "KP",
               "East Sea", 2, 39.0, 129.0, wave=True, targets=[])
    model = Model({"p": {"w", "w2"}})
    out, folded = dedupe.run([wave, test_, other], {}, SETTINGS, NOW, model, remaining=100, skip=set())
    assert {e["id"] for e in out} == {"w", "w2"} and out[0]["type"] == "missile_drone"   # into the earlier wave
    assert [e["id"] for e in folded] == ["p"]


def test_a_late_report_restores_the_archived_identity_date_and_reports():
    archived = ev("old", "Sri Lankan troops took part in a joint counter-terrorism exercise in Russia.", "deployment", "RU",
                  "Moscow", 24 * 8, 55.7, 37.6)
    new = ev("new", "Sri Lankan troops participated in a joint anti-terrorist military exercise in Russia.", "deployment",
             "RU", "Moscow", 2, 55.7, 37.6)
    model = Model({"new": {"old"}})
    out, folded = dedupe.run([new] + background(), {}, SETTINGS, NOW, model, remaining=100, skip=set(), history=[archived])
    kept = next(e for e in out if e["id"] == "old")
    assert kept["time"] == archived["time"] and kept["updated"] == new["updated"]
    assert {r["url"] for r in kept["reports"]} == {"u-old", "u-new"}
    assert [e["id"] for e in folded] == ["new"]


def test_budget_floors_shares_hidden_events_and_the_wait_after_a_failure():
    assert dedupe.run(fresh(), {}, SETTINGS, NOW, Model(), remaining=5, skip=set())[1] == []       # budget low
    assert dedupe.run(fresh(), {}, SETTINGS, NOW, Model(), remaining=100, skip={"a", "b", "c"})[1] == []  # hidden
    state, calls = {}, []
    dedupe.run(fresh(), state, SETTINGS, NOW, lambda *a, **k: calls.append(1), remaining=100, skip=set())   # no answer
    dedupe.run(fresh(), state, SETTINGS, NOW + timedelta(minutes=10), lambda *a, **k: calls.append(1), 100, set())
    assert len(calls) == 1                                               # waits RETRY after a failure
    places = ["Rafah", "Jabalia", "Deir al-Balah", "Beit Lahia", "Nuseirat", "Bureij", "Maghazi", "Zawaida",
              "Shujaiya", "Zeitoun", "Tuffah", "Daraj", "Sabra", "Tel al-Hawa", "Rimal", "Shati", "Bani Suheila",
              "Abasan", "Khuza'a", "Qarara", "Mawasi", "Shaboura", "Tal al-Sultan", "Yibna", "Saftawi", "Karama",
              "Atatra", "Sudaniya", "Salatin", "Izbat Beit Hanoun", "Muntar", "Netzarim", "Juhor al-Dik", "Mughraqa",
              "Wadi Gaza", "Hamad", "Fukhari", "Musabbah", "Nasr", "Shoka"]
    many = [ev(f"e{i}", f"An Israeli drone struck a house in {places[i // 2]}, local medics said.", hours_ago=i % 40)
            for i in range(80)]
    model = Model()
    dedupe.run(many, {}, SETTINGS, NOW, model, remaining=300, skip=set())
    assert len(model.calls) == dedupe.MAX_CALLS_PER_RUN and all(len(c) <= dedupe.CASES_PER_CALL for c in model.calls)
    model = Model()
    dedupe.run(many, {}, SETTINGS, NOW, model, remaining=60, skip=set())   # low budget: one call, the rest waits
    assert len(model.calls) == 1


def test_incidents_far_apart_are_never_candidates_unless_one_is_pinned_broadly():
    kab = [ev("k1", "The Ukrainian Air Force reported guided aerial bombs launched over Dnipropetrovsk Oblast.", "airstrike",
              "UA", "Dnipropetrovsk Oblast", 6, 48.45, 35.05),
           ev("k2", "The Ukrainian Air Force reported guided aerial bombs launched over Kharkiv Oblast.", "airstrike",
              "UA", "Kharkiv Oblast", 3, 49.99, 36.23)]
    assert dedupe.apart(*kab)                                            # ~190 km: two reports
    broad = dict(kab[1], approx=True, place="Ukraine")
    assert not dedupe.apart(kab[0], broad)                               # a whole-country pin may be either
    sea = [ev("y", "Explosions were reported near a ship off Yemen's coast.", "naval", None, "Gulf of Aden", 5, 12.6, 45.0),
           ev("h", "A security incident was reported near the Strait of Hormuz.", "naval", None, "Strait of Hormuz", 2, 26.6, 56.4)]
    assert dedupe.apart(*sea)
