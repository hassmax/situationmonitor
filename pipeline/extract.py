"""Turn raw posts into structured events with a free LLM.

Default provider: Google's Gemini API free tier (key from https://aistudio.google.com),
called through its OpenAI-compatible endpoint. The key comes from the LLM_API_KEY
environment variable (the GEMINI_API_KEY repository secret in GitHub Actions).
Posts are batched (~25 per request) and a daily budget is spread evenly across the
day's runs. Posts that do not fit in this run wait in a queue.
"""
from __future__ import annotations

import json
import math
import os
import re
import time
from datetime import datetime, timedelta
from urllib.parse import urljoin

import requests

from common import hours_since, iso, log, parse_time


EVENT_TYPES = {
    "airstrike": "strike by aircraft",
    "missile_drone": "missile or drone attack",
    "artillery": "shelling, rockets, mortars",
    "ground": "ground fighting or raids",
    "territory": "a place changing hands, advances or withdrawals",
    "air_defense": "interceptions and shoot-downs",
    "naval": "incident at sea involving ships or submarines",
    "explosion": "blast or sabotage with unclear cause",
    "deployment": "troop or ship movements, exercises, shows of force",
    "diplomacy": "ceasefires, peace talks, signed agreements, summits, alliance or defense-pact meetings and invocations, UN Security Council action, or formal escalations such as declarations of war",
    "hybrid": "sabotage, arson, undersea cable or pipeline damage, GPS jamming, cyberattacks with physical effects, or foiled plots of these",
    "incursion": "airspace violations, drone incursions, border provocations, or military buildups at a border",
}

SYSTEM_PROMPT = """You turn raw posts from OSINT accounts, official military channels, and news feeds into structured records for a live armed-conflict map. Reply with one JSON object and nothing else.

For every input item (identified by "i"), decide whether it reports a specific, recent, concrete development in an armed conflict or a notable military action. Relevant: strikes, attacks, shelling, battles, territorial gains or losses, air-defense interceptions, missile or drone launches, naval or air incidents, significant troop deployments or military exercises, casualty reports tied to a specific attack, and hybrid-warfare incidents (sabotage, arson, cable or pipeline damage, GPS jamming, airspace or border violations, drone incursions) when a state is blamed or suspected. Arrests or charges count when they reveal a specific incident or plot. Also relevant: major diplomatic developments that bear on these conflicts, such as ceasefire or peace talks, signed agreements, summits between parties or mediators, alliance or defense-pact meetings and invocations (for example NATO Article 4 consultations or meetings under the Saudi-Pakistan-Turkey Mecca defense pact), and UN Security Council votes. Not relevant: opinion, analysis with no new event, fundraising, memes, anniversaries, domestic politics, routine condemnations or statements of concern, calls or visits with no stated outcome, and items that fit none of the theaters below.

Theater ids:
- ukraine: Russia-Ukraine war, including strikes inside Russia or Belarus and the Black Sea
- nato_east: Russia or Belarus versus NATO and the EU outside Ukraine: incidents on or over the borders of Finland, Estonia, Latvia, Lithuania, Poland, and Romania; Kaliningrad; the Baltic Sea; and Russian-linked sabotage or hybrid attacks anywhere in Europe
- mideast: Israel, Gaza, West Bank, Lebanon, Syria, Iraq, Iran, Yemen, the Gulf states, the Red Sea, the Strait of Hormuz
- horn: Sudan, South Sudan, Ethiopia, Eritrea, Somalia, Djibouti
- drc_sahel: eastern DR Congo, Rwanda, Burundi, Uganda border areas, Mali, Burkina Faso, Niger, Nigeria, Chad, Mauritania
- indopac: China, Taiwan, Japan, the Koreas, the Philippines, the South and East China Seas, Vietnam, Myanmar, Thailand, Cambodia, India, Pakistan

Event types:
{types}

Output: {{"events": [one object per input item, in any order]}}
Irrelevant item: {{"i": <n>, "relevant": false}}
Relevant item:
{{"i": <n>, "relevant": true, "type": "<event type id>", "summary": "<max 25 words>", "place": "<most specific place named, English spelling>" or null, "admin1": "<province, oblast, or state>" or null, "country": "<ISO 3166-1 alpha-2>" or null, "lat": <number> or null, "lon": <number> or null, "origin_place": "<launch or firing location if the item states it>" or null, "origin_lat": <number> or null, "origin_lon": <number> or null, "theater": "<theater id>", "severity": <1, 2, or 3>, "claim": "report" or "official_claim", "killed": <int> or null, "injured": <int> or null}}

Rules:
- Write the summary yourself in plain, neutral English. Translate non-English items. Do not copy sentences from the item.
- When the source is a party to the conflict, attribute the claim in the summary (for example "Russian MoD claims...", "IDF says...").
- For hybrid incidents and incursions, say who blames whom exactly as the item does (for example "Polish officials suspect Russian involvement"). Never state attribution the item does not make.
- Never add facts that are not in the item. Unknown casualty numbers are null.
- lat/lon: your best estimate for the named place; null if you cannot place it at least at city or district level.
- severity 3 = major (10 or more killed, strike on a capital or critical infrastructure, large territorial change, direct combat between major powers, attack with dozens of missiles or drones, a ceasefire or peace deal signed or collapsing, an alliance invoked); 2 = notable (including high-level talks or emergency alliance meetings); 1 = minor or local.
- For diplomacy, place the event where the meeting or signing happened; if no place is given, use the capital of the main party. Use the theater of the conflict it concerns, even if the meeting is elsewhere.
- claim = "official_claim" when the item is a government, military, or armed-group statement about its own actions or results; otherwise "report".
""".format(types="\n".join(f"- {k}: {v}" for k, v in EVENT_TYPES.items()))

_EN = (r"air ?strikes?|strikes?|struck|missiles?|drones?|uavs?|shahed|shell(?:ing|ed)|artillery|rockets?|"
       r"mortars?|attack(?:s|ed)?|explosions?|blasts?|killed|dead|casualt(?:y|ies)|wounded|injur(?:ed|ies)|"
       r"clash(?:es|ed)?|fighting|offensive|advanc(?:e|es|ed)|captur(?:e|ed)|seiz(?:e|ed)|liberat(?:e|ed)|"
       r"intercept(?:s|ed|ion)?|shot down|air defen[cs]e|ceasefire|truce|bomb(?:s|ing|ed)?|raids?|ambush(?:ed)?|"
       r"troops|soldiers|warships?|destroyers?|frigates?|carriers?|submarines?|navy|naval|coast guard|pla|"
       r"drills?|exercises?|incursions?|adiz|blockade|sorties?|houthis?|hezbollah|idf|irgc|hamas|rsf|m23|"
       r"jnim|al-shabaab|tplf|fano|junta|militants?|insurgents?|gunmen|sabotage|saboteurs?|arson|"
       r"undersea|cables?|pipelines?|jamming|jammed|spoofing|gps|airspace|violat(?:e|ed|es|ion|ions)|"
       r"border guards?|provocations?|shadow fleet|hybrid|cyber ?attacks?|balloons?|espionage|spies|spy|"
       r"talks|negotiat(?:e|es|ed|ing|ions?)|agreements?|accords?|summits?|mediat(?:e|ed|or|ors|ion)|envoys?|"
       r"peace|pact|treaty|security council|article 4|article 5|foreign ministers?|defen[cs]e ministers?|"
       r"chiefs of staff|delegations?")
CONFLICT_RE = re.compile(
    rf"\b(?:{_EN})\b"
    r"|удар|обстр|ракет|дрон|бпла|шахед|атак|вибух|взрыв|штурм|наступ|звільн|освобо|ппо|пво|загибл|погиб|"
    r"поранен|ранен|збит|сбит|знищ|уничтож|окупант|оккупан"
    r"|غارة|غارات|قصف|صاروخ|صواريخ|مسيرة|مسيّرة|اشتباك|انفجار|استهداف|قتلى|جرحى"
    r"|ירי|טיל|רקט|יירוט|פיגוע"
    r"|演习|军演|解放军|导弹|战机|军舰|台海",
    re.IGNORECASE,
)


class RateLimited(Exception):
    pass


def is_candidate(item: dict) -> bool:
    if not item.get("prefilter", True):
        return True
    return bool(CONFLICT_RE.search(item.get("text", "")))


def _ts(item: dict) -> float:
    t = parse_time(item.get("time"))
    return t.timestamp() if t else 0.0


def build_queue(pending: list[dict], fresh: list[dict], now: datetime, settings: dict) -> list[dict]:
    by_id: dict[str, dict] = {}
    for it in pending + fresh:
        if hours_since(it.get("time"), now) <= settings["max_item_age_hours"]:
            by_id[it["id"]] = it
    queue = sorted(by_id.values(), key=lambda it: (-int(it.get("weight", 1)), -_ts(it)))
    return queue[: settings["pending_max"]]


def calls_allowed(state: dict, settings: dict, now: datetime) -> int:
    day = now.strftime("%Y-%m-%d")
    usage = state.setdefault("llm_calls", {"date": day, "count": 0})
    if usage.get("date") != day:
        usage.update(date=day, count=0)
    remaining = int(settings["daily_llm_calls"]) - int(usage["count"])
    if remaining <= 0:
        return 0
    minutes_left = 24 * 60 - (now.hour * 60 + now.minute)
    runs_left = max(1, minutes_left // 15)
    per_run = max(1, math.ceil(remaining / runs_left))
    return min(per_run, int(settings["max_calls_per_run"]), remaining)


def _estimate_tokens(text: str) -> int:
    ascii_share = sum(1 for ch in text if ord(ch) < 128) / max(1, len(text))
    return int(len(text) / (3.6 if ascii_share > 0.8 else 1.8)) + 30


def make_batches(queue: list[dict], settings: dict) -> list[list[dict]]:
    batches, current, tokens = [], [], 0
    for it in queue:
        cost = _estimate_tokens(it["text"][:700])
        if current and (len(current) >= settings["batch_max_items"] or tokens + cost > settings["batch_token_budget"]):
            batches.append(current)
            current, tokens = [], 0
        current.append(it)
        tokens += cost
    if current:
        batches.append(current)
    return batches


def _parse_json_object(content: str) -> dict | None:
    """Parse the model's reply, tolerating code fences or stray text around the JSON."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", (content or "").strip()).strip()
    for candidate in (text, text[text.find("{"): text.rfind("}") + 1] if "{" in text else ""):
        if not candidate:
            continue
        try:
            value = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
        if isinstance(value, list):
            return {"events": value}
    return None


def _content_from_response(r: requests.Response) -> str:
    """Return the assistant text from a normal JSON reply or a streamed (SSE) reply."""
    body = r.text or ""
    if body.lstrip().startswith("data:"):
        parts = []
        for line in body.splitlines():
            line = line.strip()
            if not line.startswith("data:") or line == "data: [DONE]":
                continue
            try:
                chunk = json.loads(line[5:].strip())
                delta = chunk["choices"][0].get("delta") or chunk["choices"][0].get("message") or {}
                parts.append(delta.get("content") or "")
            except (ValueError, KeyError, IndexError, TypeError):
                continue
        return "".join(parts)
    try:
        data = r.json()
    except ValueError:
        raise RuntimeError(
            f"non-JSON reply (HTTP {r.status_code}, {r.headers.get('content-type')}): {body[:300]!r}"
        ) from None
    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError(f"unexpected reply shape: {str(data)[:300]}") from None
    content = message.get("content") or ""
    if isinstance(content, list):
        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    if not content:
        finish = (data["choices"][0] or {}).get("finish_reason")
        raise RuntimeError(f"empty reply (finish_reason={finish}); refusal={message.get('refusal')!r}")
    return content


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _post(url: str, body: dict, token: str) -> requests.Response:
    """POST without letting a redirect silently turn the request into a GET."""
    r = requests.post(url, headers=_headers(token), json=body, timeout=180, allow_redirects=False)
    hops = 0
    while r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location") and hops < 3:
        url = urljoin(url, r.headers["location"])
        log(f"[extract] redirected (HTTP {r.status_code}) to {url}; re-sending as POST")
        r = requests.post(url, headers=_headers(token), json=body, timeout=180, allow_redirects=False)
        hops += 1
    return r


def _describe(r: requests.Response) -> str:
    return f"HTTP {r.status_code} {r.headers.get('content-type', '?')}: {(r.text or '')[:200]!r}"


def _find_model(token: str, settings: dict, state: dict) -> dict | None:
    """Send a tiny request with each candidate model and keep the first that really answers."""
    url = settings["llm_url"]
    for model in settings["llm_models"]:
        for json_mode in (True, False):
            body = {
                "model": model,
                "max_tokens": 200,
                "temperature": 0,
                "messages": [{"role": "user", "content": 'Reply with the JSON object {"ok": true} and nothing else.'}],
            }
            if json_mode:
                body["response_format"] = {"type": "json_object"}
            state["llm_calls"]["count"] += 1
            try:
                r = _post(url, body, token)
            except Exception as exc:  # noqa: BLE001
                log(f"[extract] probe {model}: {exc}")
                break
            if r.status_code == 429:
                raise RateLimited(r.text[:200])
            try:
                works = bool(r.json().get("choices"))
            except (ValueError, AttributeError):
                works = False
            log(f"[extract] probe {model} json_mode={json_mode}: {_describe(r)}")
            if works:
                found = {"url": url, "model": model, "json_mode": json_mode,
                         "checked": iso(datetime.now().astimezone())}
                state["llm_model"] = found
                log(f"[extract] using {model} (json_mode={json_mode})")
                return found
            if r.status_code in (401, 403):
                log("[extract] the API key was rejected; check the GEMINI_API_KEY secret")
                return None
            if r.status_code == 404 or "not found" in (r.text or "").lower():
                break  # unknown model name: try the next one
    return None


def _call_model(batch: list[dict], token: str, chosen: dict, settings: dict) -> dict:
    payload = [
        {"i": n, "source": it["source"], "platform": it["platform"], "posted": it["time"], "text": it["text"][:700]}
        for n, it in enumerate(batch)
    ]
    body = {
        "model": chosen["model"],
        "temperature": 0.1,
        "max_tokens": int(settings["max_output_tokens"]),
        "stream": False,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps({"items": payload}, ensure_ascii=False)},
        ],
    }
    if chosen.get("json_mode", True):
        body["response_format"] = {"type": "json_object"}
    r = _post(chosen["url"], body, token)
    if r.status_code == 429:
        raise RateLimited(r.text[:200])
    if r.status_code >= 400:
        raise RuntimeError(_describe(r))
    content = _content_from_response(r)
    parsed = _parse_json_object(content)
    if parsed is None:
        raise RuntimeError(f"model reply was not JSON: {content[:300]!r}")
    return parsed


def _num(v, lo, hi):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if lo <= x <= hi and not math.isnan(x) else None


def _int_or_none(v):
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


def _clean_record(obj: dict, item: dict) -> dict | None:
    if not obj.get("relevant"):
        return None
    summary = str(obj.get("summary") or "").strip()
    if not summary:
        return None
    etype = obj.get("type")
    etype = "diplomacy" if etype == "ceasefire" else etype
    etype = etype if etype in EVENT_TYPES else "explosion"
    country = str(obj.get("country") or "").upper().strip()
    try:
        severity = min(3, max(1, int(obj.get("severity") or 1)))
    except (TypeError, ValueError):
        severity = 1
    return {
        "type": etype,
        "summary": summary[:240],
        "place": (str(obj["place"]).strip()[:120] if obj.get("place") else None),
        "admin1": (str(obj["admin1"]).strip()[:120] if obj.get("admin1") else None),
        "country": country if re.fullmatch(r"[A-Z]{2}", country) else None,
        "lat": _num(obj.get("lat"), -90, 90),
        "lon": _num(obj.get("lon"), -180, 180),
        "origin_place": (str(obj["origin_place"]).strip()[:120] if obj.get("origin_place") else None),
        "origin_lat": _num(obj.get("origin_lat"), -90, 90),
        "origin_lon": _num(obj.get("origin_lon"), -180, 180),
        "theater": str(obj.get("theater") or "").strip(),
        "severity": severity,
        "claim": "official_claim" if obj.get("claim") == "official_claim" else "report",
        "killed": _int_or_none(obj.get("killed")),
        "injured": _int_or_none(obj.get("injured")),
        "item": item,
    }


def run(queue: list[dict], state: dict, settings: dict, now: datetime, disabled: bool = False):
    """Returns (records, leftover_queue, calls_used)."""
    token = os.environ.get("LLM_API_KEY", "").strip()
    if disabled or not token:
        if not token:
            log("[extract] LLM_API_KEY is not set; add the GEMINI_API_KEY repository secret (see README)")
        return [], queue, 0
    allowed = calls_allowed(state, settings, now)
    batches = make_batches(queue, settings)
    log(f"[extract] queue={len(queue)} batches={len(batches)} allowed_calls={allowed}")
    if not allowed or not batches:
        return [], queue, 0

    chosen = state.get("llm_model")
    checked = parse_time(chosen.get("checked")) if chosen else None
    stale = (not chosen or not checked or now - checked > timedelta(hours=24)
             or chosen.get("url") != settings["llm_url"] or chosen.get("model") not in settings["llm_models"])
    if stale:
        before = state["llm_calls"]["count"]
        try:
            chosen = _find_model(token, settings, state)
        except RateLimited as exc:
            log(f"[extract] rate limited while checking models: {exc}")
            return [], queue, state["llm_calls"]["count"] - before
        if chosen is None:
            log("[extract] no model returned a usable answer; see the probe lines above")
            return [], queue, state["llm_calls"]["count"] - before
        allowed = max(0, allowed - (state["llm_calls"]["count"] - before))

    records: list[dict] = []
    done: set[str] = set()
    used = 0
    failures = 0
    for n, batch in enumerate(batches[:allowed]):
        if n:
            time.sleep(float(settings.get("seconds_between_calls", 0)))  # stay under the per-minute limit
        used += 1
        state["llm_calls"]["count"] += 1
        try:
            out = _call_model(batch, token, chosen, settings)
        except RateLimited as exc:
            log(f"[extract] rate limited, stopping for this run: {exc}")
            break
        except Exception as exc:  # noqa: BLE001
            log(f"[extract] batch failed: {exc}")
            for it in batch:
                it["attempts"] = int(it.get("attempts", 0)) + 1
            failures += 1
            if failures >= 2:
                state.pop("llm_model", None)  # re-check models next run
                log("[extract] two failures in a row; stopping for this run to save the daily budget")
                break
            continue
        failures = 0
        for it in batch:
            done.add(it["id"])
        for obj in out.get("events", []) if isinstance(out, dict) else []:
            if not isinstance(obj, dict):
                continue
            i = obj.get("i")
            if isinstance(i, int) and 0 <= i < len(batch):
                rec = _clean_record(obj, batch[i])
                if rec:
                    records.append(rec)
    leftover = [it for it in queue if it["id"] not in done and int(it.get("attempts", 0)) < 3]
    return records, leftover[: settings["pending_max"]], used
