from datetime import datetime, timedelta, timezone

import dedupe

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
SETTINGS = {"dedupe_min_calls": 10}


def ev(i, summary, place="London", country="GB", hours_ago=3, type_="hybrid", theater="nato_east", **kw):
    t = (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"id": i, "theater": theater, "type": type_, "summary": summary, "place": place, "country": country,
            "lat": 51.5, "lon": -0.1, "approx": False, "severity": 2, "killed": None, "injured": None, "origins": [],
            "time": t, "updated": t, "reports": [{"url": f"u-{i}", "time": t}], **kw}


def fresh():
    # One story over two days, three map sections' worth of wording and places, plus an unrelated one.
    return [ev("a", "Five men were arrested near a UK air base used by the US.", place="United Kingdom", hours_ago=34),
            ev("b", "Counterterrorism police made explosives arrests near RAF Fairford.", place="Fairford", hours_ago=24),
            ev("c", "UK police question five men held at an airbase used by the US to attack Iran.",
               theater="mideast", hours_ago=4),
            ev("d", "A cyberattack disrupted ticket machines at London stations.", hours_ago=2),
            ev("x", "Five men arrested near an airbase in France.", country="FR", hours_ago=2)]


def test_all_hybrid_events_in_one_country_form_one_group_whatever_their_wording():
    got = [[e["id"] for e in g] for g in dedupe.groups(fresh(), {}, NOW)]
    assert got == [["a", "b", "c", "d"]]


def test_strikes_are_grouped_only_when_their_wording_overlaps():
    strikes = [ev("s1", "A drone strike hit a fuel depot in Kharkiv.", country="UA", type_="missile_drone"),
               ev("s2", "Drones struck the Kharkiv fuel depot overnight.", country="UA", type_="missile_drone"),
               ev("s3", "Shelling killed two civilians in Kherson.", country="UA", type_="artillery")]
    assert [[e["id"] for e in g] for g in dedupe.groups(strikes, {}, NOW)] == [["s1", "s2"]]


def test_model_groups_are_folded_into_the_earliest_and_remembered():
    state, calls = {}, []
    ask = lambda *a, **k: calls.append(1) or {"results": [{"i": 0, "groups": [["a", "b", "c"]]}]}
    out, folded = dedupe.run(fresh(), state, SETTINGS, NOW, ask, remaining=100, skip=set())
    assert sorted(e["id"] for e in out) == ["a", "d", "x"] and sorted(e["id"] for e in folded) == ["b", "c"]
    assert len(next(e for e in out if e["id"] == "a")["reports"]) == 3
    judged = state["dedupe"]["judged"]
    assert judged["a|b"]["same"] and not judged["a|d"]["same"]
    # later, nothing new to ask: no call
    dedupe.run(out, state, SETTINGS, NOW + timedelta(hours=2), ask, remaining=100, skip=set())
    assert len(calls) == 1


def test_ids_from_another_case_or_made_up_are_ignored():
    state = {}
    ask = lambda *a, **k: {"results": [{"i": 0, "groups": [["a", "zzz"], ["x", "d"]]}, {"i": 7, "groups": [["a", "b"]]}]}
    out, folded = dedupe.run(fresh(), state, SETTINGS, NOW, ask, remaining=100, skip=set())
    assert folded == [] and len(out) == 5


def test_failed_call_retries_after_fifteen_minutes_not_an_hour():
    state, calls = {}, []
    ask = lambda *a, **k: calls.append(1) or None
    dedupe.run(fresh(), state, SETTINGS, NOW, ask, remaining=100, skip=set())
    dedupe.run(fresh(), state, SETTINGS, NOW + timedelta(minutes=10), ask, remaining=100, skip=set())
    assert len(calls) == 1
    dedupe.run(fresh(), state, SETTINGS, NOW + timedelta(minutes=16), ask, remaining=100, skip=set())
    assert len(calls) == 2


def test_budget_and_hidden_events():
    ask = lambda *a, **k: 1 / 0
    assert dedupe.run(fresh(), {}, SETTINGS, NOW, ask, remaining=5, skip=set())[1] == []            # budget low
    events = [e for e in fresh() if e["id"] in ("a", "b")]
    assert dedupe.run(events, {}, SETTINGS, NOW, ask, remaining=100, skip={"b"})[1] == []         # hidden


def test_waves_and_alerts_are_left_to_their_own_rules():
    events = [dict(e, wave=True) for e in fresh()[:2]] + [dict(e, alert=True) for e in fresh()[2:4]]
    assert dedupe.groups(events, {}, NOW) == []


def test_talks_are_grouped_by_who_takes_part_across_theaters_and_places():
    talks = [ev("m1", "German and Russian foreign ministers hold a rare meeting at the UN.", type_="diplomacy",
                place="New York", country="US", parties=["DE", "RU"], hours_ago=30),
             ev("m2", "German and Russian foreign ministers held rare talks amid tensions.", type_="diplomacy",
                place="Berlin", country="DE", theater="ukraine", parties=["DE", "RU"], hours_ago=12),
             ev("m3", "Germany's foreign minister visited the ICC, pledging support.", type_="diplomacy",
                place="The Hague", country="NL", parties=["DE", "ICC"], hours_ago=4),
             ev("m4", "Germany's foreign minister visited the ICC in The Hague.", type_="legal",
                place="The Hague", country="NL", theater="ukraine", parties=["DE", "ICC"], hours_ago=2),
             ev("m5", "Germany and France held talks on air defence.", type_="diplomacy",
                place="Paris", country="FR", parties=["DE", "FR"], hours_ago=3)]
    got = sorted([e["id"] for e in g] for g in dedupe.groups(talks, {}, NOW))
    assert got == [["m1", "m2"], ["m3", "m4"]]


def test_answers_in_other_shapes_are_still_read():
    for reply in ({"results": [{"i": "0", "groups": [["a", "b", "c"]]}]},             # case number as text
                  {"results": [{"case": 0, "groups": [{"ids": ["a", "b", "c"]}]}]}):   # groups as objects
        state = {}
        out, folded = dedupe.run(fresh(), state, SETTINGS, NOW, lambda *a, **k: reply, remaining=100, skip=set())
        assert sorted(e["id"] for e in folded) == ["b", "c"], reply


def test_the_group_waiting_longest_is_asked_first():
    small = [ev("s1", "Drones hit a depot.", country="UA", type_="hybrid", hours_ago=1),
             ev("s2", "A depot was hit by drones.", country="UA", type_="hybrid", hours_ago=1)]
    first = dedupe.groups(fresh() + small, {}, NOW)[0]
    assert [e["id"] for e in first] == ["a", "b", "c", "d"]      # 4 UK events before 2 newer Ukrainian ones


def test_one_party_within_the_other_links_and_statements_filed_as_hybrid_join_talks():
    talks = [ev("k1", "EU foreign policy chief Kallas warns Russia is preparing a sabotage campaign.",
                type_="diplomacy", place="Brussels", country="BE", parties=["EU", "RU"], hours_ago=6),
             ev("k2", "Kallas urges vigilance against increasing Russian sabotage in Europe.",
                type_="diplomacy", place="Brussels", country="BE", parties=["EU"], hours_ago=5),
             ev("k3", "The EU's top diplomat said Russia is planning more sabotage in Europe.",
                type_="hybrid", place="Brussels", country="BE", parties=["EU", "RU"], hours_ago=4)]
    got = [sorted(e["id"] for e in g) for g in dedupe.groups(talks, {}, NOW)]
    assert ["k1", "k2", "k3"] in got


def test_different_gets_one_second_look_after_six_hours():
    evs = [ev("a", "Five men arrested near RAF Fairford."), ev("b", "Arrests near the Fairford air base.")]
    first = {"a|b": {"same": False, "at": (NOW - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")}}
    assert dedupe.groups(evs, first, NOW) == []
    old = {"a|b": {"same": False, "at": (NOW - timedelta(hours=7)).strftime("%Y-%m-%dT%H:%M:%SZ")}}
    assert len(dedupe.groups(evs, old, NOW)) == 1
    twice = {"a|b": dict(old["a|b"], n=2)}
    assert dedupe.groups(evs, twice, NOW) == []
    state = {"dedupe": {"judged": dict(old)}}
    ask = lambda *a, **k: {"results": [{"i": 0, "groups": []}]}
    dedupe.run(evs, state, SETTINGS, NOW, ask, 100, set())
    assert state["dedupe"]["judged"]["a|b"]["n"] == 2


def test_big_groups_are_shown_in_overlapping_runs_so_early_reports_are_compared():
    evs = [ev(f"h{n:02d}", f"Hybrid incident report number {n}.", hours_ago=60 - n) for n in range(30)]
    parts = dedupe.groups(evs, {}, NOW)
    assert all(len(p) <= dedupe.MAX_GROUP for p in parts)
    shown = {e["id"] for p in parts for e in p}
    assert shown == {e["id"] for e in evs}
    assert any({"h00", "h01"} <= {e["id"] for e in p} for p in parts)


def test_a_small_old_group_is_not_starved_by_a_big_new_one():
    old = [ev("o1", "A Belgian official says Iran is behind attacks on Jewish sites.", country="BE", hours_ago=50),
           ev("o2", "Belgium's intelligence chief says Tehran is behind attacks on Jewish sites.", country="BE",
              hours_ago=40)]
    new = [ev(f"n{k}", f"Hybrid incident report {k}.", country="PL", hours_ago=3 - k * 0.1) for k in range(12)]
    assert [e["id"] for e in dedupe.groups(new + old, {}, NOW)[0]] == ["o1", "o2"]


def test_a_story_told_again_days_later_is_compared_and_folded():
    old = ev("o", "British counterterrorism police made explosives arrests near RAF Fairford, an air base used for "
             "American strikes on Iran.", place="Fairford",
             hours_ago=62)
    new = ev("n", "Five men were arrested on suspicion of a bomb plot near a Royal Air Force base.", place=None,
             hours_ago=1)
    assert dedupe.groups([old, new], {}, NOW) == []               # beyond the 48-hour pair window
    assert [[e["id"] for e in p] for _, p in dedupe.late_cases([old, new], [], {}, NOW)] == [["o", "n"]]
    ask = lambda *a, **k: {"results": [{"i": 0, "groups": [{"ids": ["o", "n"], "summary": "Police arrested five men "
                                                            "near RAF Fairford."}]}]}
    out, folded = dedupe.run([old, new], {}, SETTINGS, NOW, ask, remaining=100, skip=set())
    assert [e["id"] for e in out] == ["o"] and out[0]["time"] == old["time"]  # keeps its first date


def test_a_late_report_of_an_archived_story_takes_its_date():
    archived = ev("o", "British counterterrorism police made explosives arrests near RAF Fairford, an air base.",
                  place="Fairford", hours_ago=24 * 9)
    new = ev("n", "Five men were arrested on suspicion of a bomb plot near a Royal Air Force base.", place=None,
             hours_ago=1)
    ask = lambda *a, **k: {"results": [{"i": 0, "groups": [["o", "n"]]}]}
    out, folded = dedupe.run([new], {}, SETTINGS, NOW, ask, remaining=100, skip=set(), history=[archived])
    assert [e["id"] for e in out] == ["n"] and folded == [] and out[0]["time"] == archived["time"]


def test_strikes_told_again_need_close_wording_and_the_same_names():
    old = ev("o", "Shelling killed two civilians in Kherson.", country="UA", type_="artillery", hours_ago=60)
    same = ev("s", "Shelling killed two civilians in Kherson, officials said.", country="UA", type_="artillery",
              hours_ago=1)
    other = ev("t", "Shelling killed two civilians in Nikopol.", country="UA", type_="artillery", hours_ago=1)
    pairs = [[e["id"] for e in p] for _, p in dedupe.late_cases([old, same, other], [], {}, NOW)]
    assert pairs == [["o", "s"]]


def test_folded_events_get_the_combined_headline_unless_it_adds_a_number():
    a = ev("a", "A Myanmar military airstrike in Rakhine killed 33 people.", country="MM", type_="airstrike",
           place="Rakhine State", hours_ago=20, killed=33)
    b = ev("b", "The death toll from a Myanmar airstrike in Rakhine state rose to 50.", country="MM",
           type_="airstrike", place="Rakhine State", hours_ago=3, killed=50)
    for text, expect in (("Myanmar military airstrike in Rakhine kills 50 people.", True),
                         ("Myanmar military airstrike in Rakhine kills 70 people.", False)):
        ask = lambda *x, **k: {"results": [{"i": 0, "groups": [{"ids": ["a", "b"], "summary": text}]}]}
        out, _ = dedupe.run([dict(a), dict(b)], {}, SETTINGS, NOW, ask, remaining=100, skip=set())
        assert (out[0].get("headline") == text) is expect


def test_several_calls_in_one_run_while_groups_wait(monkeypatch):
    monkeypatch.setattr(dedupe, "MAX_EVENTS", 2)
    evs = [ev(f"{c}{k}", f"Hybrid incident {c}.", country=c.upper() * 2, hours_ago=5 - k) for c in "pqrs" for k in (0, 1)]
    calls = []
    ask = lambda *a, **k: calls.append(1) or {"results": [{"i": 0, "groups": []}]}
    dedupe.run(evs, {}, SETTINGS, NOW, ask, remaining=300, skip=set())
    assert len(calls) == dedupe.MAX_CALLS_PER_RUN
    calls.clear()
    dedupe.run(evs, {}, SETTINGS, NOW, ask, remaining=60, skip=set())  # low budget: one call, the rest waits
    assert len(calls) == 1


def test_a_backlog_is_worked_every_run_otherwise_every_half_hour():
    calls = []
    ask = lambda *a, **k: calls.append(1) or {"results": [{"i": 0, "groups": []}]}
    state = {"dedupe": {"attempt": (NOW - timedelta(minutes=13)).strftime("%Y-%m-%dT%H:%M:%SZ"), "backlog": True}}
    dedupe.run(fresh(), state, SETTINGS, NOW, ask, remaining=50, skip=set())
    assert len(calls) == 1                                   # 13 minutes after the last run: asks again
    state["dedupe"].update(attempt=(NOW - timedelta(minutes=13)).strftime("%Y-%m-%dT%H:%M:%SZ"), backlog=False)
    evs = fresh() + [ev("y", "Another hybrid incident in London.", hours_ago=1)]
    dedupe.run(evs, state, SETTINGS, NOW, ask, remaining=50, skip=set())
    assert len(calls) == 1                                   # nothing was waiting: waits for 30 minutes


def test_an_explosion_described_as_sabotage_is_compared_with_the_countrys_hybrid_events():
    syria = [ev("h", "Syrian state reports say a gas pipeline fire between Al-Shola and Deir Al-Zour was sabotage.",
                country="SY", theater="mideast", hours_ago=30),
             ev("x", "Syrian officials say a gas pipeline fire near Deir Ezzor was caused by an act of sabotage.",
                country="SY", theater="mideast", type_="explosion", hours_ago=29),
             ev("y", "An explosion hit a market in Idlib.", country="SY", theater="mideast", type_="explosion", hours_ago=5)]
    assert [[e["id"] for e in g] for g in dedupe.groups(syria, {}, NOW)] == [["h", "x"]]


def test_a_planned_assault_filed_as_fighting_is_compared_with_the_same_plan_filed_as_a_buildup():
    plan = [ev("d", "Official sources report that Saudi Arabia plans an assault on Houthi forces to break the Red Sea chokehold.",
               place="Red Sea", country=None, theater="mideast", type_="deployment", hours_ago=4),
            ev("g", "Saudi-backed forces plan an assault against Houthi forces aiming to secure the Red Sea maritime routes.",
               place="Red Sea", country=None, theater="mideast", type_="ground", hours_ago=4),
            ev("f", "Clashes killed six fighters near Hodeidah.", place="Red Sea", country=None, theater="mideast",
               type_="ground", hours_ago=3)]
    assert [[e["id"] for e in g] for g in dedupe.groups(plan, {}, NOW)] == [["d", "g"]]


def test_a_new_story_filed_under_many_kinds_and_places_is_one_group():
    # One cockpit attack came in as a hybrid attack, an incursion, an airstrike and a naval incident
    # in four countries; the name no earlier event used ties them together.
    story = [ev("h", "A FlyDubai flight to Israel was targeted in a hijacking attempt.", place="Dubai", country="AE",
                theater="mideast", hours_ago=30),
             ev("i", "An Israel-bound FlyDubai flight diverted to Saudi Arabia after an incident onboard.", place="Tabuk",
                country="SA", theater="mideast", type_="incursion", hours_ago=29),
             ev("a", "Israel launched repatriation flights after an attack on a FlyDubai aircraft.", place="Tel Aviv",
                country="IL", theater="mideast", type_="airstrike", hours_ago=5),
             ev("n", "Israeli carriers plan flights from the UAE after the FlyDubai cockpit attack.",
                place="United Arab Emirates", country="AE", theater="mideast", type_="naval", hours_ago=6),
             ev("t", "Israel's foreign minister condemned the FlyDubai attack.", place="Jerusalem", country="IL",
                theater="mideast", type_="diplomacy", parties=["IL"], hours_ago=4)]
    groups = [sorted(e["id"] for e in g) for g in dedupe.groups(story, {}, NOW)]
    assert ["a", "h", "i", "n"] in groups
    assert not any("t" in g for g in groups)  # a government's reaction stays its own event
    # a name earlier events use all the time is not a new story
    old = [ev(f"o{k}", "Fighting near a FlyDubai office.", place="Dubai", country="AE", theater="mideast",
              type_="ground", hours_ago=100 + k) for k in range(2)]
    assert dedupe.story_names(story, old, NOW) == set()
    assert dedupe.story_names(story, old[:1], NOW) == {"flydubai"}
    # nor is a place name shared by strikes of every kind
    taiz = [ev(f"z{k}", f"Shelling and strikes hit Taiz district {k}.", place="Taiz", country="YE",
               theater="mideast", type_=t, hours_ago=3) for k, t in enumerate(["airstrike", "artillery", "ground", "missile_drone"])]
    assert dedupe.story_names(taiz, [], NOW) == set()
