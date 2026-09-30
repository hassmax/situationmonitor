from datetime import datetime, timezone

import extract

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)
SETTINGS = {"daily_llm_calls": 400, "max_calls_per_run": 6}


def test_extraction_reserve_stops_extraction_below_thirty():
    state = {"llm_calls": {"date": "2026-09-27", "count": 371}}   # 29 left
    assert extract.calls_remaining(state, SETTINGS, NOW) == 29
    assert extract.calls_allowed(state, SETTINGS, NOW, reserve=30) == 0
    assert extract.calls_allowed(state, SETTINGS, NOW) > 0         # other uses may still call


def test_new_day_resets_the_counter():
    state = {"llm_calls": {"date": "2026-09-26", "count": 400}}
    assert extract.calls_remaining(state, SETTINGS, NOW) == 400


def test_the_same_story_check_has_a_paced_daily_share():
    from datetime import datetime, timezone
    import extract
    state, settings = {"llm_calls": {"date": "2026-09-30", "count": 0}}, {"daily_llm_calls": 400}
    noon = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
    assert extract.share_left(state, settings, noon, "dedupe") == 45 + extract.SHARE_BURST   # half of 90 by noon
    for _ in range(49):
        extract._spend(state, "dedupe")
    assert extract.share_left(state, settings, noon, "dedupe") == 0
    assert extract.share_left(state, settings, noon, "extract") > 1000        # extraction has no share
    assert state["llm_calls"]["count"] == 49 and state["llm_calls"]["by"]["dedupe"] == 49
    next_day = datetime(2026, 10, 1, 0, 5, tzinfo=timezone.utc)
    assert extract.share_left(state, settings, next_day, "dedupe") == extract.SHARE_BURST    # a new day


def test_the_same_story_check_stops_when_its_share_is_used():
    import dedupe
    from test_dedupe import NOW, SETTINGS, fresh
    calls = []
    ask = lambda *a, **k: calls.append(k.get("purpose")) or {"results": [{"i": 0, "groups": []}]}  # noqa: E731
    dedupe.run(fresh(), {}, SETTINGS, NOW, ask, remaining=300, skip=set(), share=0)
    assert calls == []
    dedupe.run(fresh(), {}, SETTINGS, NOW, ask, remaining=300, skip=set(), share=1)
    assert calls == ["dedupe"]
