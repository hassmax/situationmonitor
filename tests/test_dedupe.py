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


def test_waves_alerts_and_diplomacy_are_left_to_their_own_rules():
    events = [dict(e, wave=True) for e in fresh()[:2]] + [ev("p", "Talks in London.", type_="diplomacy"),
                                                           ev("q", "More talks in London.", type_="diplomacy")]
    assert dedupe.groups(events, {}, NOW) == []
