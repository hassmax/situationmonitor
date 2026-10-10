"""Other free AI providers, used beside Gemini (the owner asked for the best free options,
2026-10-04). Each is an OpenAI-compatible chat endpoint with its own key and its own daily limit,
counted here (`state["providers"]`), never against Gemini's budget.

Checked from GitHub's servers on 2026-10-04:
- Cerebras: gpt-oss-120b (OpenAI's open-weight model), free tier 30,000 tokens a minute and
  1,000,000 a day. Key: the CEREBRAS_API_KEY secret (free account at cloud.cerebras.ai).
- OpenRouter: 17 free models (":free"), up to 50 requests a day without bought credit. Key: the
  OPENROUTER_API_KEY secret (free account at openrouter.ai).
- Not used: Groq (its free tier allows 8,000 tokens a minute, less than one analysis request), and
  GitHub Models (from this repository's Actions every request, even a malformed one, came back as a
  bare "OK" with no answer).

A provider without its key is skipped. The regional analyst (analyst.py) asks these first, in the
order of `analysis_order`, and Gemini last, so nothing breaks before the keys are added.

Offloading (2026-10-04, the owner asked to move model calls off Gemini where possible): one-off
text calls whose purpose has a route (`provider_routes` in sources.yaml, else ROUTES) go to the
routed providers first and to Gemini only when none answers (extract.ask_json); extraction spills
over to them when Gemini can't take it (extract.run). Each provider has a daily token cap
(`daily_tokens`, under its real limit) and a per-minute one (`tokens_per_minute`): a call that
would pass the minute's limit waits up to MAX_WAIT seconds, else goes to Gemini. Each route gives
its purpose a daily token share on that provider, so no one job can take the whole day. Tokens are
counted from the reply's `usage` (estimated from the text when it has none), per provider and
purpose, in `state["providers"]`.
"""
from __future__ import annotations

import os
import time

from common import log

DEFAULTS = [
    # Cerebras' free limits are 30,000 tokens a minute and 1,000,000 a day; the caps leave a margin.
    {"name": "cerebras", "label": "Cerebras", "url": "https://api.cerebras.ai/v1/chat/completions",
     "key": "CEREBRAS_API_KEY", "models": ["gpt-oss-120b"], "daily_max": 1000,
     "daily_tokens": 900_000, "tokens_per_minute": 27_000},
    {"name": "openrouter", "label": "OpenRouter", "url": "https://openrouter.ai/api/v1/chat/completions",
     "key": "OPENROUTER_API_KEY", "models": ["nvidia/nemotron-3-ultra-550b-a55b:free", "qwen/qwen3.8-27b:free"],
     "daily_max": 45},
]
# Which purposes go to which providers first, with each purpose's daily token share there. Image
# reading (the Map Room) stays on Gemini. Together the shares stay within Cerebras' daily cap.
ROUTES = {
    "analysis": {"cerebras": 380_000},
    "incident_repair": {"cerebras": 150_000},  # bounded legacy campaign migration
    "recency": {"cerebras": 50_000},
    "frontline_review": {"cerebras": 80_000},
    "frontline_isw": {"cerebras": 100_000},
    "frontline": {"cerebras": 60_000},
    "extract": {"cerebras": 110_000},   # overflow only: when Gemini can't take the batches
    "frontline_social": {"cerebras": 120_000},
}
MAX_WAIT = 25          # seconds a call may wait for the minute's token limit before going to Gemini
COOL_DOWN = 60         # seconds a provider is left alone after a rate-limit refusal
TYPICAL_CALL = 8000    # tokens assumed for a purpose's call before any has been measured

_recent: dict[str, list[tuple[float, int]]] = {}   # provider -> (when, tokens) in this run, for the minute's limit
_cool: dict[str, float] = {}
_clock = time.monotonic
_sleep = time.sleep


def configured(settings: dict) -> dict[str, dict]:
    return {p["name"]: p for p in (settings.get("providers") or DEFAULTS) if isinstance(p, dict) and p.get("name")}


def _rec(state: dict, name: str, now) -> dict:
    day = now.strftime("%Y-%m-%d")
    rec = state.setdefault("providers", {}).setdefault(name, {})
    if rec.get("date") != day:
        rec.clear()
        rec.update({"date": day, "count": 0, "tokens": 0, "by": {}})
    rec.setdefault("tokens", 0)
    rec.setdefault("by", {})
    return rec


def _count(state: dict, name: str, now, n: int = 0) -> int:
    rec = _rec(state, name, now)
    rec["count"] += n
    return rec["count"]


def record(state: dict, name: str, now, purpose: str, tokens: int) -> None:
    """Add one answered call's tokens to the provider's day, in total and under its purpose."""
    rec = _rec(state, name, now)
    rec["tokens"] += int(tokens)
    by = rec["by"].setdefault(purpose, {"calls": 0, "tokens": 0})
    by["calls"] += 1
    by["tokens"] += int(tokens)


def available(p: dict, state: dict, now, env=os.environ) -> bool:
    return (bool((env.get(p.get("key") or "") or "").strip()) and _count(state, p["name"], now) < int(p.get("daily_max", 40))
            and not _rec(state, p["name"], now).get("full") and _cool.get(p["name"], 0) <= _clock()
            and (not p.get("daily_tokens") or _rec(state, p["name"], now)["tokens"] < int(p["daily_tokens"])))


def routes(settings: dict, purpose: str) -> list[tuple[dict, int | None]]:
    """(provider, the purpose's daily token share there) in the order to try them."""
    table = settings.get("provider_routes")
    table = ROUTES if table is None else table
    known = configured(settings)
    return [(known[name], int(cap) if cap else None) for name, cap in (table.get(purpose) or {}).items() if name in known]


def typical(state: dict, name: str, now, purpose: str) -> int:
    by = (_rec(state, name, now)["by"]).get(purpose) or {}
    return int(by["tokens"] / by["calls"]) if by.get("calls") else TYPICAL_CALL


def has_room(p: dict, cap: int | None, state: dict, now, purpose: str, need: int, env=os.environ) -> bool:
    """Whether `p` can take a call of about `need` tokens for `purpose` today."""
    if not available(p, state, now, env):
        return False
    rec = _rec(state, p["name"], now)
    if p.get("daily_tokens") and rec["tokens"] + need > int(p["daily_tokens"]):
        return False
    return not cap or int((rec["by"].get(purpose) or {}).get("tokens", 0)) + need <= cap


def room(state: dict, settings: dict, now, purpose: str, env=os.environ) -> int:
    """Calls of `purpose` the routed providers could still take today (by its typical size)."""
    best = 0
    for p, cap in routes(settings, purpose):
        if not available(p, state, now, env):
            continue
        size = max(1, typical(state, p["name"], now, purpose))
        rec = _rec(state, p["name"], now)
        left = [int(p.get("daily_max", 40)) - rec["count"]]
        if p.get("daily_tokens"):
            left.append((int(p["daily_tokens"]) - rec["tokens"]) // size)
        if cap:
            left.append((cap - int((rec["by"].get(purpose) or {}).get("tokens", 0))) // size)
        best = max(best, min(left))
    return max(0, best)


def _pace(p: dict, need: int, max_wait: float = MAX_WAIT) -> bool:
    """Keep under the provider's tokens-a-minute limit: wait up to `max_wait` seconds, else False."""
    limit = int(p.get("tokens_per_minute") or 0)
    if not limit:
        return True
    name = p["name"]
    need = min(need, limit)  # a call bigger than the minute's limit goes alone, once the minute is clear
    while True:
        now = _clock()
        recent = [(t, n) for t, n in _recent.get(name, []) if now - t < 60]
        _recent[name] = recent
        if sum(n for _, n in recent) + need <= limit or not recent:
            return True
        # the oldest calls drop out of the minute first
        total, wait = sum(n for _, n in recent) + need, 0.0
        for t, n in recent:
            total -= n
            wait = 60 - (now - t) + 0.5
            if total <= limit:
                break
        if wait > max_wait:
            return False
        log(f"[providers] {name}: waiting {wait:.0f}s for the minute's token limit")
        _sleep(wait)


def ask_json(p: dict, system_prompt: str, user_text: str, state: dict, now, max_tokens: int = 4000,
             env=os.environ, purpose: str = "analysis", temperature: float = 0,
             max_wait: float = MAX_WAIT, effort: str | None = None) -> tuple[dict | None, str | None]:
    """One call to provider `p`: (parsed JSON, model that answered), or (None, None). Models are
    tried in order when one is unknown or busy; JSON mode is dropped if the model refuses it.
    `effort` ("low") asks a reasoning model to think less, leaving more of max_tokens for the answer;
    dropped if the model refuses it."""
    import extract  # the shared request and reply-parsing helpers

    token = (env.get(p.get("key") or "") or "").strip()
    if not token:
        return None, None
    sent = extract._estimate_tokens(system_prompt) + extract._estimate_tokens(user_text)
    need = sent + max_tokens  # Cerebras counts the answer allowance against the minute's limit
    for model in p.get("models") or []:
        for json_mode in (True, False):
            if not available(p, state, now, env):
                return None, None
            if not _pace(p, need, max_wait):
                log(f"[providers] {p['name']}: the minute's token limit is reached; {purpose} goes to Gemini")
                return None, None
            body = {"model": model, "temperature": temperature, "max_tokens": max_tokens, "stream": False,
                    "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_text}]}
            if json_mode:
                body["response_format"] = {"type": "json_object"}
            if effort:
                body["reasoning_effort"] = effort
            _count(state, p["name"], now, 1)
            try:
                r = extract._post(p["url"], body, token)
            except Exception as exc:  # noqa: BLE001 - never reached the provider
                _count(state, p["name"], now, -1)
                log(f"[providers] {p['name']} {model}: {exc}")
                return None, None
            if r.status_code in (401, 403):
                log(f"[providers] {p['name']}: the key was refused ({r.status_code}); check the {p.get('key')} secret")
                return None, None
            if r.status_code == 400 and effort and "reasoning_effort" in (r.text or ""):
                _count(state, p["name"], now, -1)
                effort = None
                log(f"[providers] {p['name']} {model}: reasoning_effort refused; asking without it")
                continue  # note: this skips the JSON-mode-off retry for this model, which is rarely needed
            if r.status_code == 400 and json_mode and "response_format" in (r.text or ""):
                _count(state, p["name"], now, -1)
                continue  # this model doesn't take JSON mode: ask again without it
            if r.status_code == 429:
                _count(state, p["name"], now, -1)
                _recent.setdefault(p["name"], []).append((_clock(), need))
                text = (r.text or "").lower()
                if "day" in text or "daily" in text:
                    _rec(state, p["name"], now)["full"] = True   # the day's limit: left alone until tomorrow
                else:
                    _cool[p["name"]] = _clock() + COOL_DOWN
                log(f"[providers] {p['name']} {model}: refused for its rate limit ({extract._describe(r)[:140]})")
                return None, None
            if r.status_code == 404 or r.status_code >= 500 or (r.status_code == 400 and "model" in (r.text or "").lower()):
                log(f"[providers] {p['name']} {model}: {extract._describe(r)[:160]}; trying the next model")
                break
            if r.status_code >= 400:
                log(f"[providers] {p['name']} {model} failed: {extract._describe(r)[:200]}")
                return None, None
            try:
                content = extract._content_from_response(r)
            except RuntimeError as exc:
                log(f"[providers] {p['name']} {model}: {exc}")
                return None, None
            used = extract.usage_tokens(r) or sent + extract._estimate_tokens(content)
            record(state, p["name"], now, purpose, used)
            _recent.setdefault(p["name"], []).append((_clock(), used))
            parsed = extract._parse_json_object(content)
            if parsed is None:
                log(f"[providers] {p['name']} {model}: the reply was not JSON ({len(content)} characters): {extract.json_error(content)}")
                return None, None
            return parsed, model
    return None, None


def ask_routed(system_prompt: str, user_text: str, state: dict, settings: dict, now, purpose: str,
               max_tokens: int = 4000, env=os.environ, temperature: float = 0,
               max_wait: float = MAX_WAIT, effort: str | None = None) -> dict | None:
    """Ask the providers routed for `purpose`, in order, within their token caps. None if none answered."""
    import extract

    need = extract._estimate_tokens(system_prompt) + extract._estimate_tokens(user_text) + max_tokens // 4
    for p, cap in routes(settings, purpose):
        if not has_room(p, cap, state, now, purpose, need, env):
            continue
        reply, _ = ask_json(p, system_prompt, user_text, state, now, max_tokens, env, purpose, temperature, max_wait, effort)
        if reply is not None:
            return reply
    return None


def summary(state: dict, now) -> str:
    """One log line: each provider's calls and tokens today by purpose."""
    day = now.strftime("%Y-%m-%d")
    parts = []
    for name, rec in sorted((state.get("providers") or {}).items()):
        if rec.get("date") != day or not rec.get("count"):
            continue
        by = ", ".join(f"{k} {v['calls']}/{v['tokens']:,}" for k, v in sorted((rec.get("by") or {}).items()))
        parts.append(f"{name} {rec['count']} calls, {rec.get('tokens', 0):,} tokens ({by})")
    return "; ".join(parts)
