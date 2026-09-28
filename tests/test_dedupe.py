from datetime import datetime, timedelta, timezone

import dedupe

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
SETTINGS = {"dedupe_min_calls": 10}


def ev(i, summary, place="London", country="GB", hours_ago=3, type_="hybrid", theater="nato_east", **kw):
    t = (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"id": i, "theater": theater, "type": type_, "summary": summary, "place": place, "country": country,
            "lat": 51.5, "lon": -0.1, "approx": False, "severity": 2, "killed": None, "injured": None, "origins": [],
            "time": t, "updated": t, "reports": [{"url": f"u-{i}", "time": t}], **kw}


A = ev("a", "British police are investigating five men arrested near a US-run airbase.", hours_ago=10)
B = ev("b", "Five men arrested near RAF Fairford are questioned by counter-terrorism police.", place="Fairford",
       theater="mideast", hours_ago=4)
C = ev("c", "A cyberattack disrupted ticket machines at London stations.", hours_ago=2)
D = ev("d", "Five men arrested near an airbase in France.", country="FR", hours_ago=2)


def test_pairs_same_country_similar_wording_any_theater():
    got = [(e["id"], f["id"]) for e, f in dedupe.pairs([A, B, C, D], {}, NOW)]
    assert got == [("a", "b")]


def test_model_says_same_folds_into_the_earliest_and_is_remembered():
    state, calls = {}, []
    ask = lambda *a, **k: calls.append(1) or {"results": [{"i": 0, "same": True}]}
    events = [dict(A, reports=list(A["reports"])), dict(B, reports=list(B["reports"])), C]
    out, folded = dedupe.run(events, state, SETTINGS, NOW, ask, remaining=100, skip=set())
    assert [e["id"] for e in out] == ["a", "c"] and [e["id"] for e in folded] == ["b"]
    assert len(out[0]["reports"]) == 2 and state["dedupe"]["judged"]["a|b"]["same"] is True
    # an hour later, nothing new to ask: no call
    dedupe.run(out, state, SETTINGS, NOW + timedelta(hours=2), ask, remaining=100, skip=set())
    assert len(calls) == 1


def test_model_says_different_keeps_both_and_does_not_ask_again():
    state, calls = {}, []
    ask = lambda *a, **k: calls.append(1) or {"results": [{"i": 0, "same": False}]}
    out, folded = dedupe.run([A, B], state, SETTINGS, NOW, ask, remaining=100, skip=set())
    assert len(out) == 2 and folded == []
    dedupe.run([A, B], state, SETTINGS, NOW + timedelta(hours=2), ask, remaining=100, skip=set())
    assert len(calls) == 1


def test_budget_interval_and_hidden_events():
    ask = lambda *a, **k: 1 / 0
    assert dedupe.run([A, B], {}, SETTINGS, NOW, ask, remaining=5, skip=set())[1] == []          # budget low
    assert dedupe.run([A, B], {}, SETTINGS, NOW, ask, remaining=100, skip={"b"})[1] == []        # hidden
    state = {"dedupe": {"attempt": (NOW - timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ")}}
    assert dedupe.run([A, B], state, SETTINGS, NOW, ask, remaining=100, skip=set())[1] == []     # within the hour


def test_waves_and_alert_groups_are_left_to_their_own_grouping():
    w = dict(A, id="w", wave=True)
    assert dedupe.pairs([w, B], {}, NOW) == []
