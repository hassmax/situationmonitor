from datetime import datetime, timedelta, timezone

import brief

NOW = datetime(2026, 9, 27, 21, 0, tzinfo=timezone.utc)
SETTINGS = {"brief_min_calls": 5}


def ev(i, theater="ukraine", hours_ago=1, status="corroborated", severity=2, **kw):
    t = (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"id": i, "theater": theater, "type": "missile_drone", "place": "Kyiv", "summary": f"event {i}",
            "status": status, "severity": severity, "sources_count": 2, "time": t, "updated": t, "reports": [], **kw}


EVENTS = [ev("a1"), ev("b2", theater="mideast"), ev("old", hours_ago=9)]


def test_window_keeps_only_last_six_hours():
    assert [e["id"] for e in brief.window_events(EVENTS, NOW)] == ["a1", "b2"]


def test_validate_drops_bullets_citing_unknown_ids():
    reply = {"bullets": [{"text": "good", "ids": ["a1"]}, {"text": "made up", "ids": ["zzz"]},
                         {"text": "half made up", "ids": ["a1", "zzz"]}, {"text": "out of window", "ids": ["old"]}],
             "theaters": [{"id": "ukraine", "text": "ok", "ids": ["a1"]},
                          {"id": "ukraine", "text": "duplicate theater", "ids": ["a1"]},
                          {"id": "mideast", "text": "wrong theater for a1", "ids": ["a1"]}]}
    out = brief.validate(reply, brief.window_events(EVENTS, NOW))
    assert out["bullets"] == [{"text": "good", "ids": ["a1"]}]
    assert out["theaters"] == [{"id": "ukraine", "text": "ok", "ids": ["a1"]}]


def test_validate_caps_bullets_and_rejects_garbage():
    events = brief.window_events(EVENTS, NOW)
    many = {"bullets": [{"text": f"b{n}", "ids": ["a1"]} for n in range(9)]}
    assert len(brief.validate(many, events)["bullets"]) == 6
    assert brief.validate("not json", events) is None
    assert brief.validate({"bullets": [{"text": "x", "ids": ["nope"]}]}, events) is None
    # an uncited line only stands alone ("nothing significant happened")
    alone = brief.validate({"bullets": [{"text": "Nothing significant.", "ids": []}]}, events)
    assert alone == {"bullets": [{"text": "Nothing significant.", "ids": []}], "theaters": []}
    mixed = brief.validate({"bullets": [{"text": "x", "ids": ["a1"]}, {"text": "uncited", "ids": []}]}, events)
    assert [b["text"] for b in mixed["bullets"]] == ["x"]


def test_update_writes_brief_and_publishes_timestamp():
    state = {}
    ask = lambda *a, **k: {"bullets": [{"text": "A corroborated strike on Kyiv.", "ids": ["a1"]}], "theaters": []}
    brief.update(state, EVENTS, {}, SETTINGS, NOW, ask, remaining=100)
    assert state["brief"]["generated_at"] == "2026-09-27T21:00:00Z"
    assert state["brief"]["window_hours"] == 6
    assert state["brief"]["bullets"][0]["ids"] == ["a1"]


def test_failed_or_invalid_call_keeps_previous_brief_and_timestamp():
    prev = {"generated_at": "2026-09-27T18:00:00Z", "window_hours": 6, "bullets": [{"text": "old", "ids": ["x"]}], "theaters": []}
    for ask in (lambda *a, **k: None, lambda *a, **k: {"bullets": [{"text": "bad", "ids": ["nope"]}]}):
        state = {"brief": dict(prev)}
        brief.update(state, EVENTS, {}, SETTINGS, NOW, ask, remaining=100)
        assert state["brief"] == prev


def test_at_most_hourly_and_only_when_events_changed():
    calls = []
    ask = lambda *a, **k: calls.append(1) or {"bullets": [{"text": "x", "ids": ["a1"]}]}
    state = {}
    brief.update(state, EVENTS, {}, SETTINGS, NOW, ask, remaining=100)
    brief.update(state, EVENTS + [ev("c3")], {}, SETTINGS, NOW + timedelta(minutes=30), ask, remaining=100)
    assert len(calls) == 1                                   # within the hour: no new call
    brief.update(state, EVENTS, {}, SETTINGS, NOW + timedelta(hours=2), ask, remaining=100)
    assert len(calls) == 1                                   # nothing changed: no new call
    brief.update(state, EVENTS + [ev("c3")], {}, SETTINGS, NOW + timedelta(hours=2), ask, remaining=100)
    assert len(calls) == 2


def test_skipped_when_budget_is_low():
    calls = []
    state = {}
    brief.update(state, EVENTS, {}, SETTINGS, NOW, lambda *a, **k: calls.append(1), remaining=4)
    assert calls == [] and "brief" not in state


def test_no_events_needs_no_model_call():
    state = {}
    brief.update(state, [], {}, SETTINGS, NOW, lambda *a, **k: 1 / 0, remaining=100)
    assert state["brief"]["bullets"] == [{"text": brief.NOTHING, "ids": []}]


def test_prompt_input_carries_confidence_and_side():
    claimed = ev("c1", status="claimed", reports=[{"side": "RU"}])
    facts = brief._facts(claimed, {"ukraine": "Russia-Ukraine"})
    assert facts["confidence"] == "claimed only by sources aligned with one side"
    assert facts["aligned_with"] == ["RU"]
