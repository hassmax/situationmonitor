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
