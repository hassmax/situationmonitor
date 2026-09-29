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
