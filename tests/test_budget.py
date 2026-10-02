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
    assert extract.share_left(state, settings, noon, "dedupe") == 70 + extract.SHARE_BURST   # half of 140 by noon
    for _ in range(74):
        extract._spend(state, "dedupe")
    assert extract.share_left(state, settings, noon, "dedupe") == 0
    assert extract.share_left(state, settings, noon, "extract") > 1000        # extraction has no share
    assert state["llm_calls"]["count"] == 74 and state["llm_calls"]["by"]["dedupe"] == 74
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


def test_headlines_already_judged_irrelevant_are_not_sent_again_for_a_day():
    from datetime import datetime, timedelta, timezone
    import extract
    now = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
    state = {"rejected_heads": {extract.headline_key("Polícia prende suspeitos de ataque a tiros - G1"): "2026-10-02T08:00:00Z"}}
    items = [{"platform": "rss", "text": "Polícia prende suspeitos de ataque a tiros - O Globo"},   # same headline, other outlet
             {"platform": "rss", "text": "Russian drone strike on Kharkiv - Reuters"},
             {"platform": "telegram", "text": "Polícia prende suspeitos de ataque a tiros"}]           # not news: never skipped
    assert [it["text"][:7] for it in extract.skip_rejected(items, state, now)] == ["Russian", "Polícia"]
    assert extract.skip_rejected(items[:1], state, now + timedelta(days=1)) == items[:1]           # forgotten after a day
