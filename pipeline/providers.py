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
"""
from __future__ import annotations

import os

from common import log

DEFAULTS = [
    {"name": "cerebras", "label": "Cerebras", "url": "https://api.cerebras.ai/v1/chat/completions",
     "key": "CEREBRAS_API_KEY", "models": ["gpt-oss-120b"], "daily_max": 60},
    {"name": "openrouter", "label": "OpenRouter", "url": "https://openrouter.ai/api/v1/chat/completions",
     "key": "OPENROUTER_API_KEY", "models": ["nvidia/nemotron-3-ultra-550b-a55b:free", "qwen/qwen3.8-27b:free"],
     "daily_max": 45},
]


def configured(settings: dict) -> dict[str, dict]:
    return {p["name"]: p for p in (settings.get("providers") or DEFAULTS) if isinstance(p, dict) and p.get("name")}


def _count(state: dict, name: str, now, n: int = 0) -> int:
    day = now.strftime("%Y-%m-%d")
    rec = state.setdefault("providers", {}).setdefault(name, {})
    if rec.get("date") != day:
        rec.clear()
        rec.update({"date": day, "count": 0})
    rec["count"] += n
    return rec["count"]


def available(p: dict, state: dict, now, env=os.environ) -> bool:
    return bool((env.get(p.get("key") or "") or "").strip()) and _count(state, p["name"], now) < int(p.get("daily_max", 40))


def ask_json(p: dict, system_prompt: str, user_text: str, state: dict, now, max_tokens: int = 4000,
             env=os.environ) -> tuple[dict | None, str | None]:
    """One call to provider `p`: (parsed JSON, model that answered), or (None, None). Models are
    tried in order when one is unknown or busy; JSON mode is dropped if the model refuses it."""
    import extract  # the shared request and reply-parsing helpers

    token = (env.get(p.get("key") or "") or "").strip()
    if not token:
        return None, None
    for model in p.get("models") or []:
        for json_mode in (True, False):
            if _count(state, p["name"], now) >= int(p.get("daily_max", 40)):
                return None, None
            body = {"model": model, "temperature": 0, "max_tokens": max_tokens, "stream": False,
                    "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_text}]}
            if json_mode:
                body["response_format"] = {"type": "json_object"}
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
            if r.status_code == 400 and json_mode and "response_format" in (r.text or ""):
                _count(state, p["name"], now, -1)
                continue  # this model doesn't take JSON mode: ask again without it
            if r.status_code in (404, 429) or r.status_code >= 500 or (r.status_code == 400 and "model" in (r.text or "").lower()):
                log(f"[providers] {p['name']} {model}: {extract._describe(r)[:160]}; trying the next model")
                break
            if r.status_code >= 400:
                log(f"[providers] {p['name']} {model} failed: {extract._describe(r)[:200]}")
                return None, None
            content = extract._content_from_response(r)
            parsed = extract._parse_json_object(content)
            if parsed is None:
                log(f"[providers] {p['name']} {model}: the reply was not JSON ({len(content)} characters): {extract.json_error(content)}")
                return None, None
            return parsed, model
    return None, None
