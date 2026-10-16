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
    assert providers.room(state, SETTINGS, NOW, "frontline_review", env) == providers.ROUTES["frontline_review"]["cerebras"] // providers.TYPICAL_CALL
    assert providers.room(state, SETTINGS, NOW, "dedupe", env) == 0               # not routed


def item(i):
    return {"id": f"p{i}", "source": "s", "platform": "rss", "time": "2026-10-04T11:00:00Z", "url": f"u{i}",
            "text": f"Drone strike on Kharkiv number {i}", "kind": "news", "weight": 1}


def test_extraction_spills_over_when_gemini_refuses_for_billing(monkeypatch):
    def cerebras(body):
        items = json.loads(body["messages"][1]["content"])["items"]
        return R(200, {"events": [{"i": it["i"], "relevant": False} for it in items]}, 15000)
    sent = routed(monkeypatch, cerebras, lambda body: R(402, text="Your prepayment credits are depleted"))
    clock = [1000.0]
    monkeypatch.setattr(providers, "_clock", lambda: clock[0])
    monkeypatch.setattr(providers, "_sleep", lambda s: clock.__setitem__(0, clock[0] + s))
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


def test_a_call_bigger_than_the_minute_waits_for_a_clear_minute(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(providers, "_clock", lambda: clock[0])
    monkeypatch.setattr(providers, "_sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    p = providers.configured({})["cerebras"]
    providers._recent["cerebras"] = [(990.0, 3000)]
    assert providers._pace(p, 36000, max_wait=65) and clock[0] > 1050    # waited for the minute to clear
    providers._recent["cerebras"] = [(clock[0] - 1, 3000)]
    assert not providers._pace(p, 36000)                                 # 25 s is not enough


def test_the_extract_share_is_held_back_from_other_jobs():
    # 2026-10-11: the other jobs used all of Cerebras' day while Gemini's calls were gone too,
    # so no post could be read for six hours. Extraction's unused share now stays free for it.
    env = {"CEREBRAS_API_KEY": "c"}
    p = providers.configured({})["cerebras"]
    state = {"providers": {"cerebras": {"date": "2026-10-04", "count": 50, "tokens": 745_000, "by": {}}}}
    hold = providers.ROUTES["extract"]["cerebras"]
    assert providers.held(p, state, NOW, "analysis") == hold
    assert providers.held(p, state, NOW, "extract") == 0
    assert not providers.has_room(p, None, state, NOW, "analysis", 20_000, env)    # 745K + 20K > 900K - 150K
    assert providers.has_room(p, None, state, NOW, "extract", 20_000, env)
    assert providers.room(state, SETTINGS, NOW, "frontline_social", env) == 0
    # once extraction has used its share, the hold is gone
    state["providers"]["cerebras"]["by"]["extract"] = {"calls": 9, "tokens": hold}
    assert providers.held(p, state, NOW, "analysis") == 0


def test_overflow_keeps_reading_past_its_share_while_cerebras_is_idle(monkeypatch):
    # 2026-10-15 18:30 UTC: Gemini's calls for the day were used up and extraction's Cerebras share
    # nearly so (145,051 of 150,000 tokens), so no post was read until midnight, with 400 waiting
    # and 600,000 tokens of Cerebras' day unused in the other jobs' shares.
    def cerebras(body):
        items = json.loads(body["messages"][1]["content"])["items"]
        return R(200, {"events": [{"i": it["i"], "relevant": False} for it in items]}, 9700)
    sent = routed(monkeypatch, cerebras)
    clock = [1000.0]
    monkeypatch.setattr(providers, "_clock", lambda: clock[0])
    monkeypatch.setattr(providers, "_sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    evening = datetime(2026, 10, 15, 18, 30, tzinfo=timezone.utc)
    state = {"llm_calls": {"date": "2026-10-15", "count": 470},
             "providers": {"cerebras": {"date": "2026-10-15", "count": 38, "tokens": 298_608,
                                        "by": {"extract": {"calls": 15, "tokens": 145_051},
                                               "gate": {"calls": 9, "tokens": 97_141},
                                               "recency": {"calls": 10, "tokens": 49_721}}}}}
    records, left, used, _ = extract.run([item(i) for i in range(100)], state, SETTINGS, evening)
    assert used == 0 and sent == [CEREBRAS, CEREBRAS]          # two overflow batches, as on any run
    assert len(left) == 20


def test_reading_new_posts_borrows_the_other_jobs_unused_shares_as_the_day_goes():
    env = {"CEREBRAS_API_KEY": "c"}
    p = providers.configured({})["cerebras"]
    share = providers.ROUTES["extract"]["cerebras"]
    others = sum(r.get("cerebras", 0) for job, r in providers.ROUTES.items() if job != "extract")
    evening = datetime(2026, 10, 15, 18, 0, tzinfo=timezone.utc)
    state = {"providers": {"cerebras": {"date": "2026-10-15", "count": 24, "tokens": share + 100_000,
                                        "by": {"extract": {"calls": 15, "tokens": share},
                                               "gate": {"calls": 9, "tokens": 100_000}}}}}
    unused = others - 100_000
    # by 18:00 three quarters of the day has gone, and so may three quarters of what the others left
    assert providers.borrowable(p, state, evening) == unused * 3 // 4
    assert providers.has_room(p, share, state, evening, "extract", 10_000, env)
    assert providers.room(state, SETTINGS, evening, "extract", env) > 0
    # little is lent early in the day, and only to reading new posts
    assert providers.borrowable(p, state, datetime(2026, 10, 15, 0, 30, tzinfo=timezone.utc)) == unused * 30 // 1440
    assert providers.cap_for(p, 50_000, state, evening, "recency") == 50_000
    # once extraction has borrowed all it may, the other jobs still have the rest of the day's part
    lent = providers.borrowable(p, state, evening)
    rec = state["providers"]["cerebras"]
    rec["by"]["extract"]["tokens"] += lent
    rec["tokens"] += lent
    assert not providers.has_room(p, share, state, evening, "extract", 10_000, env)
    assert providers.has_room(p, providers.ROUTES["analysis"]["cerebras"], state, evening, "analysis", 20_000, env)
    assert p["daily_tokens"] - rec["tokens"] == unused - lent


def test_route_shares_fit_within_the_daily_cap():
    total = sum(r.get("cerebras", 0) for r in providers.ROUTES.values())
    assert total <= providers.configured({})["cerebras"]["daily_tokens"]


def test_the_incident_checks_have_daily_gemini_shares():
    state = {"llm_calls": {"date": "2026-10-04", "count": 100,
                           "by": {"incident_grouping": 30, "incident_repair": 12}}}
    assert extract.share_left(state, SETTINGS, NOW, "incident_grouping") <= 0
    assert extract.share_left(state, SETTINGS, NOW, "incident_repair") <= 0
