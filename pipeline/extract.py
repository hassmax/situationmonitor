"""Turn raw posts into structured events with a free GitHub Models LLM.

Runs inside GitHub Actions using the workflow's own GITHUB_TOKEN (permission `models: read`),
so there is no API key and no bill. The free tier allows roughly 150 requests a day on
low-tier models, so posts are batched (~18 per request) and a daily budget is spread
evenly across the day's runs. Posts that do not fit in this run wait in a queue.
"""
from __future__ import annotations

import json
import math
import os
import re
from datetime import datetime

import requests

from common import hours_since, log, parse_time

ENDPOINT = "https://models.github.ai/inference/chat/completions"

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
    "ceasefire": "ceasefire, truce, formal escalation, or talks that change the fighting",
}

SYSTEM_PROMPT = """You turn raw posts from OSINT accounts, official military channels, and news feeds into structured records for a live armed-conflict map. Reply with one JSON object and nothing else.

For every input item (identified by "i"), decide whether it reports a specific, recent, concrete development in an armed conflict or a notable military action. Relevant: strikes, attacks, shelling, battles, territorial gains or losses, air-defense interceptions, missile or drone launches, naval or air incidents, significant troop deployments or military exercises (especially by US or Chinese forces), casualty reports tied to a specific attack, ceasefires or formal escalations. Not relevant: opinion, analysis with no new event, fundraising, memes, anniversaries, general politics, and posts too vague to place on a map.

Theater ids:
- ukraine: Russia-Ukraine war, including strikes inside Russia or Belarus and the Black Sea
- mideast: Israel, Gaza, West Bank, Lebanon, Syria, Iraq, Iran, Yemen, the Gulf states, the Red Sea, the Strait of Hormuz
- horn: Sudan, South Sudan, Ethiopia, Eritrea, Somalia, Djibouti
- drc_sahel: eastern DR Congo, Rwanda, Burundi, Uganda border areas, Mali, Burkina Faso, Niger, Nigeria, Chad, Mauritania
- indopac: China, Taiwan, Japan, the Koreas, the Philippines, the South and East China Seas, Vietnam, Myanmar, Thailand, Cambodia, India, Pakistan
- other: anywhere else (only relevant if US or Chinese military forces are involved)

Event types:
{types}

Output: {{"events": [one object per input item, in any order]}}
Irrelevant item: {{"i": <n>, "relevant": false}}
Relevant item:
{{"i": <n>, "relevant": true, "type": "<event type id>", "summary": "<max 25 words>", "place": "<most specific place named, English spelling>" or null, "admin1": "<province, oblast, or state>" or null, "country": "<ISO 3166-1 alpha-2>" or null, "lat": <number> or null, "lon": <number> or null, "origin_place": "<launch or firing location if the item states it>" or null, "origin_lat": <number> or null, "origin_lon": <number> or null, "theater": "<theater id>", "us": <bool>, "cn": <bool>, "severity": <1, 2, or 3>, "claim": "report" or "official_claim", "killed": <int> or null, "injured": <int> or null}}

Rules:
- Write the summary yourself in plain, neutral English. Translate non-English items. Do not copy sentences from the item.
- When the source is a party to the conflict, attribute the claim in the summary (for example "Russian MoD claims...", "IDF says...").
- Never add facts that are not in the item. Unknown casualty numbers are null.
- lat/lon: your best estimate for the named place; null if you cannot place it at least at city or district level.
- severity 3 = major (10 or more killed, strike on a capital or critical infrastructure, large territorial change, direct combat by US or Chinese forces, attack with dozens of missiles or drones); 2 = notable; 1 = minor or local.
- claim = "official_claim" when the item is a government, military, or armed-group statement about its own actions or results; otherwise "report".
- us / cn: true only when that country's own military is an actor, not when officials merely comment.
""".format(types="\n".join(f"- {k}: {v}" for k, v in EVENT_TYPES.items()))

_EN = (r"air ?strikes?|strikes?|struck|missiles?|drones?|uavs?|shahed|shell(?:ing|ed)|artillery|rockets?|"
       r"mortars?|attack(?:s|ed)?|explosions?|blasts?|killed|dead|casualt(?:y|ies)|wounded|injur(?:ed|ies)|"
       r"clash(?:es|ed)?|fighting|offensive|advanc(?:e|es|ed)|captur(?:e|ed)|seiz(?:e|ed)|liberat(?:e|ed)|"
       r"intercept(?:s|ed|ion)?|shot down|air defen[cs]e|ceasefire|truce|bomb(?:s|ing|ed)?|raids?|ambush(?:ed)?|"
       r"troops|soldiers|warships?|destroyers?|frigates?|carriers?|submarines?|navy|naval|coast guard|pla|"
       r"drills?|exercises?|incursions?|adiz|blockade|sorties?|houthis?|hezbollah|idf|irgc|hamas|rsf|m23|"
       r"jnim|al-shabaab|tplf|fano|junta|militants?|insurgents?|gunmen")
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


def _call_model(batch: list[dict], model: str, token: str) -> dict:
    payload = [
        {"i": n, "source": it["source"], "platform": it["platform"], "posted": it["time"], "text": it["text"][:700]}
        for n, it in enumerate(batch)
    ]
    body = {
        "model": model,
        "temperature": 0.1,
        "max_tokens": 3500,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps({"items": payload}, ensure_ascii=False)},
        ],
    }
    r = requests.post(
        ENDPOINT,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                 "Accept": "application/json"},
        json=body,
        timeout=120,
    )
    if r.status_code == 429:
        raise RateLimited(r.text[:200])
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
    content = r.json()["choices"][0]["message"]["content"] or "{}"
    content = re.sub(r"^```(?:json)?|```$", "", content.strip()).strip()
    return json.loads(content)


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
    etype = obj.get("type") if obj.get("type") in EVENT_TYPES else "explosion"
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
        "us": bool(obj.get("us")),
        "cn": bool(obj.get("cn")),
        "severity": severity,
        "claim": "official_claim" if obj.get("claim") == "official_claim" else "report",
        "killed": _int_or_none(obj.get("killed")),
        "injured": _int_or_none(obj.get("injured")),
        "item": item,
    }


def run(queue: list[dict], state: dict, settings: dict, now: datetime, disabled: bool = False):
    """Returns (records, leftover_queue, calls_used)."""
    token = os.environ.get("MODELS_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if disabled or not token:
        if not token:
            log("[extract] no GITHUB_TOKEN/MODELS_TOKEN; skipping extraction")
        return [], queue, 0
    allowed = calls_allowed(state, settings, now)
    batches = make_batches(queue, settings)
    log(f"[extract] queue={len(queue)} batches={len(batches)} allowed_calls={allowed}")
    records: list[dict] = []
    done: set[str] = set()
    used = 0
    for batch in batches[:allowed]:
        used += 1
        state["llm_calls"]["count"] += 1
        try:
            out = _call_model(batch, settings["model"], token)
        except RateLimited as exc:
            log(f"[extract] rate limited, stopping for this run: {exc}")
            break
        except Exception as exc:  # noqa: BLE001
            log(f"[extract] batch failed: {exc}")
            for it in batch:
                it["attempts"] = int(it.get("attempts", 0)) + 1
            continue
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
