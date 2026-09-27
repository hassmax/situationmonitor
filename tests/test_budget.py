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
