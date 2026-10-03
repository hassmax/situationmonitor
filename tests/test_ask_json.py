"""One-off model calls: a rate-limit refusal is not counted; an unusable reply is."""
from datetime import datetime, timezone

import extract

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
SETTINGS = {"daily_llm_calls": 470, "max_calls_per_run": 12}


class R:
    def __init__(self, status, content="{}"):
        self.status_code, self.headers, self.text, self._content = status, {}, content, content

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


def call(monkeypatch, reply):
    monkeypatch.setenv("LLM_API_KEY", "x")
    monkeypatch.setattr(extract, "_post", lambda url, body, token: reply)
    state = {"llm_calls": {"date": "2026-10-03", "count": 0, "by": {}}, "llm_model": {"url": "u", "model": "m"}}
    got = extract.ask_json("sys", "q", state, SETTINGS, NOW, purpose="frontline")
    return got, state["llm_calls"]["count"]


def test_a_rate_limit_refusal_is_not_counted(monkeypatch):
    assert call(monkeypatch, R(429, "slow down")) == (None, 0)


def test_an_unusable_reply_is_counted_and_gives_nothing(monkeypatch):
    assert call(monkeypatch, R(200, "not json at all")) == (None, 1)
    assert call(monkeypatch, R(200, '{"reports": []}')) == ({"reports": []}, 1)


def test_a_botched_character_code_loses_one_letter_not_the_reply():
    bad = '{"reports": [{"i": 0, "claims": [{"settlement": "Mekelle", "local_name": "\\u1218\\u12\\u1208", "region": "Tigray"}]}]}'
    got = extract._parse_json_object(bad)
    assert got["reports"][0]["claims"][0]["settlement"] == "Mekelle"
    assert extract._parse_json_object('{"a": [1, 2,], "b": None}') == {"a": [1, 2], "b": None}
