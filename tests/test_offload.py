"""Model calls offloaded to outside providers (providers.ROUTES), with Gemini as the fallback."""
import json
from datetime import datetime, timezone

import extract
import providers

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)
SETTINGS = {"daily_llm_calls": 470, "max_calls_per_run": 6, "llm_url": "https://gemini", "llm_models": ["lite"],
            "batch_max_items": 40, "batch_token_budget": 16000, "max_output_tokens": 9000, "pending_max": 400,
            "seconds_between_calls": 0, "extraction_reserve": 0}
CEREBRAS = "https://api.cerebras.ai/v1/chat/completions"


class R:
    def __init__(self, status, reply=None, tokens=None, text=None):
        self.status_code, self.headers = status, {"content-type": "application/json"}
        body = {"choices": [{"message": {"content": json.dumps(reply)}}]} if reply is not None else {"error": text}
        if tokens:
            body["usage"] = {"total_tokens": tokens}
        self.text = text if text is not None else json.dumps(body)
        self._body = body

    def json(self):
        return self._body


def gemini_state():
    return {"llm_calls": {"date": "2026-10-04", "count": 10},
            "llm_model": {"url": "https://gemini", "model": "lite", "json_mode": True, "checked": "2026-10-04T11:00:00Z"}}


def routed(monkeypatch, cerebras, gemini=None):
    sent = []

    def post(url, body, token):
        sent.append(url)
        if url == CEREBRAS:
            return cerebras(body)
        return gemini(body) if gemini else R(200, {"from": "gemini"}, 900)
    monkeypatch.setattr(extract, "_post", post)
    monkeypatch.setenv("LLM_API_KEY", "g")
    monkeypatch.setenv("CEREBRAS_API_KEY", "c")
    return sent


def test_routed_job_goes_to_cerebras_and_costs_gemini_nothing(monkeypatch):
    sent = routed(monkeypatch, lambda body: R(200, {"from": "cerebras"}, 5200))
    state = gemini_state()
    assert extract.ask_json("p", "t", state, SETTINGS, NOW, purpose="recency") == {"from": "cerebras"}
    assert sent == [CEREBRAS] and state["llm_calls"]["count"] == 10
    rec = state["providers"]["cerebras"]
    assert rec["tokens"] == 5200 and rec["by"]["recency"] == {"calls": 1, "tokens": 5200}


def test_falls_back_to_gemini_when_the_provider_fails_or_its_share_is_used(monkeypatch):
    sent = routed(monkeypatch, lambda body: R(503, text="overloaded"))
    state = gemini_state()
    assert extract.ask_json("p", "t", state, SETTINGS, NOW, purpose="frontline_review") == {"from": "gemini"}
    assert sent == [CEREBRAS, "https://gemini"] and state["llm_calls"]["count"] == 11
    assert state["llm_calls"]["tokens"]["frontline_review"] == 900          # Gemini's tokens are tallied too
    sent.clear()
    state["providers"]["cerebras"]["by"]["recency"] = {"calls": 9, "tokens": providers.ROUTES["recency"]["cerebras"]}
    extract.ask_json("p", "t", state, SETTINGS, NOW, purpose="recency")
    assert sent == ["https://gemini"]                                       # its daily token share is used up


def test_images_unrouted_jobs_and_the_analyst_stay_with_gemini(monkeypatch):
    sent = routed(monkeypatch, lambda body: R(200, {"from": "cerebras"}, 100))
    state = gemini_state()
    extract.ask_json("p", "t", state, SETTINGS, NOW, purpose="frontline_isw", images=["data:image/png;base64,AA"])
    extract.ask_json("p", "t", state, SETTINGS, NOW, purpose="dedupe")
    extract.ask_json("p", "t", state, SETTINGS, NOW, purpose="analysis")   # the analyst picks its own order
    assert sent == ["https://gemini"] * 3


def test_settings_can_reroute_or_turn_routing_off(monkeypatch):
    sent = routed(monkeypatch, lambda body: R(200, {"from": "cerebras"}, 100))
    state = gemini_state()
    extract.ask_json("p", "t", state, {**SETTINGS, "provider_routes": {"dedupe": {"cerebras": 50000}}}, NOW, purpose="dedupe")
    extract.ask_json("p", "t", state, {**SETTINGS, "provider_routes": {}}, NOW, purpose="recency")
    assert sent == [CEREBRAS, "https://gemini"]


def test_per_minute_limit_waits_briefly_else_hands_to_gemini(monkeypatch):
    clock = [1000.0]
    slept = []
    monkeypatch.setattr(providers, "_clock", lambda: clock[0])
    monkeypatch.setattr(providers, "_sleep", lambda s: (slept.append(s), clock.__setitem__(0, clock[0] + s)))
    sent = routed(monkeypatch, lambda body: R(200, {"from": "cerebras"}, 26500))
    state = gemini_state()
    extract.ask_json("p", "t", state, SETTINGS, NOW, purpose="recency")
    clock[0] += 50   # 50 s later: 10 s until the first call leaves the minute
    extract.ask_json("p", "t", state, SETTINGS, NOW, purpose="frontline")
    assert sent == [CEREBRAS, CEREBRAS] and slept and slept[0] <= providers.MAX_WAIT
    sent.clear()
    extract.ask_json("p", "t", state, SETTINGS, NOW, purpose="frontline")   # would wait ~60 s: Gemini instead
    assert sent == ["https://gemini"]


def test_a_daily_limit_refusal_leaves_the_provider_alone_until_tomorrow(monkeypatch):
    sent = routed(monkeypatch, lambda body: R(429, text="Tokens per day limit exceeded"))
    state = gemini_state()
    extract.ask_json("p", "t", state, SETTINGS, NOW, purpose="recency")
    extract.ask_json("p", "t", state, SETTINGS, NOW, purpose="recency")
    assert sent == [CEREBRAS, "https://gemini", "https://gemini"]
    assert state["providers"]["cerebras"]["count"] == 0      # refused calls are not counted
    assert providers.available(providers.configured({})["cerebras"], state, datetime(2026, 10, 5, 0, 5, tzinfo=timezone.utc),
                               {"CEREBRAS_API_KEY": "c"})


def test_room_counts_the_outside_providers_when_gemini_has_none():
    state = {"llm_calls": {"date": "2026-10-04", "count": 470}}
    env = {"CEREBRAS_API_KEY": "c"}
    assert extract.room(state, SETTINGS, NOW, "frontline_review") == 0           # no key in this test's environment
    assert providers.room(state, SETTINGS, NOW, "frontline_review", env) == 100_000 // providers.TYPICAL_CALL
    assert providers.room(state, SETTINGS, NOW, "dedupe", env) == 0               # not routed


def item(i):
    return {"id": f"p{i}", "source": "s", "platform": "rss", "time": "2026-10-04T11:00:00Z", "url": f"u{i}",
            "text": f"Drone strike on Kharkiv number {i}", "kind": "news", "weight": 1}


def test_extraction_spills_over_when_gemini_refuses_for_billing(monkeypatch):
    def cerebras(body):
        items = json.loads(body["messages"][1]["content"])["items"]
        return R(200, {"events": [{"i": it["i"], "relevant": False} for it in items]}, 15000)
    sent = routed(monkeypatch, cerebras, lambda body: R(402, text="Your prepayment credits are depleted"))
    monkeypatch.setattr(providers, "_sleep", lambda s: None)
    state = gemini_state()
    queue = [item(i) for i in range(100)]   # three batches of 40, 40, 20
    records, left, used, _ = extract.run(queue, state, SETTINGS, NOW)
    assert used == 0 and state["llm_calls"]["count"] == 10 and state.get("llm_out_of_credit")
    assert sent == ["https://gemini", CEREBRAS, CEREBRAS]          # two overflow batches a run
    assert len(left) == 20 and state["providers"]["cerebras"]["by"]["extract"]["calls"] == 2


def test_no_overflow_while_gemini_works(monkeypatch):
    sent = routed(monkeypatch, lambda body: R(200, {"events": []}, 100), lambda body: R(200, {"events": []}, 100))
    state = gemini_state()
    extract.run([item(1)], state, SETTINGS, NOW)
    assert sent == ["https://gemini"]
