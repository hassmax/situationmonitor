"""An overloaded or unreachable model must not use up the daily budget."""
from datetime import datetime, timezone

import requests

import extract

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
SETTINGS = {"daily_llm_calls": 400, "max_calls_per_run": 6, "llm_url": "https://example.com", "llm_models": ["m1", "m2"]}


class Resp:
    def __init__(self, status, text="busy"):
        self.status_code, self.text, self.headers = status, text, {"content-type": "application/json"}

    def json(self):
        return {"error": self.text}


def test_busy_answers_are_not_counted(monkeypatch):
    monkeypatch.setattr(extract, "_post", lambda *a, **k: Resp(503))
    state = {"llm_calls": {"date": "2026-09-28", "count": 10}}
    assert extract._find_model("key", SETTINGS, state) is None
    assert state["llm_calls"]["count"] == 10
    monkeypatch.setenv("LLM_API_KEY", "key")
    state["llm_model"] = {"url": "https://example.com", "model": "m1", "json_mode": True}
    assert extract.ask_json("p", "t", state, SETTINGS, NOW) is None
    assert state["llm_calls"]["count"] == 10


def test_unreachable_is_not_counted_but_a_real_error_is(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "key")
    state = {"llm_calls": {"date": "2026-09-28", "count": 10},
             "llm_model": {"url": "https://example.com", "model": "m1", "json_mode": True}}

    def down(*a, **k):
        raise requests.ConnectionError("down")
    monkeypatch.setattr(extract, "_post", down)
    extract.ask_json("p", "t", state, SETTINGS, NOW)
    assert state["llm_calls"]["count"] == 10
    monkeypatch.setattr(extract, "_post", lambda *a, **k: Resp(400, "bad request"))
    extract.ask_json("p", "t", state, SETTINGS, NOW)
    assert state["llm_calls"]["count"] == 11


class Ok:
    status_code, headers = 200, {"content-type": "application/json"}
    text = '{"choices": [{"message": {"content": "{\\"events\\": []}"}}]}'

    def json(self):
        return {"choices": [{"message": {"content": '{"ok": true, "events": []}'}}]}


FALLBACK = {**SETTINGS, "llm_models": ["lite"], "llm_fallback_models": ["flash"]}


def by_model(busy):
    def post(url, body, token):
        return Resp(503) if body["model"] in busy else Ok()
    return post


def test_backup_model_is_used_when_the_preferred_ones_are_busy(monkeypatch):
    monkeypatch.setattr(extract, "_post", by_model({"lite"}))
    state = {"llm_calls": {"date": "2026-09-28", "count": 10}}
    found = extract._find_model("key", FALLBACK, state)
    assert found["model"] == "flash" and found["fallback"] is True
    assert state["llm_calls"]["count"] == 11          # only the probe that answered counts


def test_one_off_call_switches_to_a_backup_when_busy(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "key")
    monkeypatch.setattr(extract, "_post", by_model({"lite"}))
    state = {"llm_calls": {"date": "2026-09-28", "count": 10},
             "llm_model": {"url": "https://example.com", "model": "lite", "json_mode": True}}
    assert extract.ask_json("p", "t", state, FALLBACK, NOW) == {"ok": True, "events": []}
    assert state["llm_model"]["model"] == "flash"


def test_backup_is_rechecked_after_an_hour():
    from datetime import timedelta
    chosen = {"url": "https://example.com", "model": "flash", "fallback": True,
              "checked": (NOW - timedelta(minutes=70)).strftime("%Y-%m-%dT%H:%M:%SZ")}
    checked = extract.parse_time(chosen["checked"])
    assert chosen.get("fallback") and NOW - checked > extract.FALLBACK_RECHECK


def test_extraction_switches_models_mid_run(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "key")
    monkeypatch.setattr(extract, "_post", by_model({"lite"}))
    monkeypatch.setattr(extract.time, "sleep", lambda s: None)
    settings = {**FALLBACK, "extraction_reserve": 0, "batch_max_items": 25, "batch_token_budget": 10000,
                "max_output_tokens": 1000, "max_item_age_hours": 36, "pending_max": 400, "seconds_between_calls": 0}
    state = {"llm_calls": {"date": "2026-09-28", "count": 0},
             "llm_model": {"url": "https://example.com", "model": "lite", "json_mode": True,
                           "checked": NOW.strftime("%Y-%m-%dT%H:%M:%SZ")}}
    item = {"id": "i1", "source": "s", "platform": "rss", "time": "2026-09-28T11:00:00Z", "text": "Drone strike on Kyiv",
            "url": "u", "weight": 1}
    records, leftover, used, _ = extract.run([item], state, settings, NOW)
    assert state["llm_model"]["model"] == "flash" and leftover == [] and used == 1


def test_depleted_credits_cost_nothing_keep_posts_and_are_rechecked_hourly(monkeypatch):
    """HTTP 402 ("prepayment credits are depleted"): not counted, one probe instead of every
    model, posts keep their place in the queue, and nothing is asked for an hour."""
    from datetime import timedelta
    calls = []

    def post(url, body, token):
        calls.append(body["model"])
        return Resp(402, "Your prepayment credits are depleted")
    monkeypatch.setattr(extract, "_post", post)
    monkeypatch.setenv("LLM_API_KEY", "key")
    state = {"llm_calls": {"date": "2026-09-28", "count": 10},
             "llm_model": {"url": "https://example.com", "model": "m1", "json_mode": True,
                           "checked": "2026-09-28T11:00:00Z"}}
    import config
    settings = {**config.load().settings, **SETTINGS, "extraction_reserve": 0, "seconds_between_calls": 0}
    queue = [{"id": f"p{i}", "source": "s", "platform": "rss", "time": "2026-09-28T11:00:00Z", "text": "Missile strike",
              "kind": "news", "weight": 1} for i in range(3)]
    records, left, used, _ = extract.run(queue, state, settings, NOW)
    assert (records, used, state["llm_calls"]["count"]) == ([], 0, 10)
    assert [it["id"] for it in left] == ["p0", "p1", "p2"] and not any(it.get("attempts") for it in left)
    assert state.get("llm_out_of_credit") and "llm_model" not in state
    calls.clear()
    for _ in range(3):   # the same hour: nothing is sent
        extract.run(left, state, settings, NOW + timedelta(minutes=15))
        assert extract.ask_json("p", "t", state, settings, NOW + timedelta(minutes=15)) is None
    assert calls == []
    extract.run(left, state, settings, NOW + timedelta(hours=1, minutes=5))   # an hour on: one probe
    assert calls == ["m1"] and state["llm_calls"]["count"] == 10
