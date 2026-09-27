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
    "diplomacy": "ceasefires, peace talks, signed agreements, summits, visits or meetings between leaders, alliance or defense-pact meetings and invocations, UN Security Council action, or formal escalations such as declarations of war",
    "hybrid": "sabotage, arson, undersea cable or pipeline damage, GPS jamming, cyberattacks with physical effects, or foiled plots of these",
    "incursion": "airspace violations, drone incursions, border provocations, or military buildups at a border",
    "arms_transfer": "major arms deliveries, air or sea bridges (surges of cargo flights or ships carrying weapons), military aid deliveries, or intercepted weapons shipments",
    "legal": "formal legal steps about a use of force or the conduct of hostilities: Article 51 letters to the UN Security Council, War Powers Resolution reports or votes, Security Council resolutions, ICJ or ICC orders, warrants, or rulings, and official statements of the legal basis for a strike",
}

SYSTEM_PROMPT = """You turn raw posts from OSINT accounts, official military channels, and news feeds into structured records for a live armed-conflict map. Reply with one JSON object and nothing else.

For every input item (identified by "i"), decide whether it reports a specific, new, concrete development in an armed conflict or a notable military action. Relevant: strikes, attacks, shelling, battles, territorial gains or losses, air-defense interceptions, missile or drone launches, naval or air incidents, significant troop deployments or military exercises, casualty reports tied to a specific attack, and hybrid-warfare incidents (sabotage, arson, cable or pipeline damage, GPS jamming, airspace or border violations, drone incursions) when a state is blamed or suspected. Arrests or charges count when they reveal a specific incident or plot. Credible reports of preparations for military action also count (units ordered or put on notice, operational planning reported by officials, force buildups), typed as deployment. Also relevant: major diplomatic developments that bear on these conflicts, such as ceasefire or peace talks, signed agreements, summits between parties or mediators, alliance or defense-pact meetings and invocations (for example NATO Article 4 consultations or meetings under the Saudi-Pakistan-Turkey Mecca defense pact), UN Security Council votes, and visits or meetings between heads of state or government, or foreign or defense ministers, that bear on these conflicts, even when no outcome is announced (for example Israel's prime minister visiting the UAE, or Ukraine's president meeting the US president; a secret, unannounced, or first-ever visit is especially significant). Also relevant: major arms transfers and air or sea bridges to parties in these conflicts, and formal legal steps about uses of force (see the legal type). Not relevant: opinion, analysis with no new event, fundraising, memes, anniversaries, domestic politics, routine condemnations or statements of concern, routine phone calls, visits by lower-level officials with no stated outcome, and items that fit none of the theaters below.

Theater ids:
- ukraine: Russia-Ukraine war, including strikes inside Russia or Belarus and the Black Sea
- nato_east: Russia or Belarus versus NATO and the EU outside Ukraine: incidents on or over the borders of Finland, Estonia, Latvia, Lithuania, Poland, and Romania; Kaliningrad; the Baltic Sea; and Russian-linked sabotage or hybrid attacks anywhere in Europe
- mideast: Israel, Gaza, West Bank, Lebanon, Syria, Iraq, Iran, Yemen, the Gulf states, the Red Sea, the Strait of Hormuz
- horn: Sudan, South Sudan, Ethiopia, Eritrea, Somalia, Djibouti
- drc_sahel: eastern DR Congo, Rwanda, Burundi, Uganda border areas, Mali, Burkina Faso, Niger, Nigeria, Chad, Mauritania
- indopac: China, Taiwan, Japan, the Koreas, the Philippines, the South and East China Seas, Vietnam, Myanmar, Thailand, Cambodia, India, Pakistan
- latam: Latin America and the Caribbean: Cuba, Venezuela, Colombia, Ecuador, Mexico, Central America, Haiti, Guyana, the Caribbean Sea and the eastern Pacific, including US military operations there (strikes on boats, strikes on cartel or armed-group targets, deployments, planning for action against Cuba or Venezuela) and armed-group violence with political or military significance. Ordinary crime is not relevant.

Event types:
{types}

Output: {{"events": [one object per input item, in any order]}}
Irrelevant item: {{"i": <n>, "relevant": false}}
Relevant item:
{{"i": <n>, "relevant": true, "type": "<event type id>", "happened": "<when the event itself happened: YYYY-MM-DD or YYYY-MM-DDTHH:MM in UTC>" or null, "summary": "<max 25 words>", "place": "<most specific place named, English spelling>" or null, "admin1": "<province, oblast, or state>" or null, "country": "<ISO 3166-1 alpha-2>" or null, "lat": <number> or null, "lon": <number> or null, "attacker": "<ISO alpha-2 of the country whose forces carried it out>" or null, "parties": ["<ISO alpha-2 of each country, or UN, EU, NATO, AU, ICC, ICJ, whose officials take part>"], "origins": [{{"place": "<launch or firing area named in the item>", "lat": <number>, "lon": <number>}}], "launched": <int> or null, "intercepted": <int> or null, "alert": true or false, "transfer": <transfer object, arms_transfer only>, "legal_basis": "<max 12 words>" or null, "theater": "<theater id>", "severity": <1, 2, or 3>, "claim": "report" or "official_claim", "killed": <int> or null, "injured": <int> or null}}

Optional key for any item (relevant or not): if the item says where a US Navy aircraft carrier (hull CVN-##) is, or that one departed, arrived, or is heading somewhere, add
"carrier": {{"hull": "CVN-78", "status": "departed" | "underway" | "operating" | "arrived" | "in port", "place": "<where it is now>", "lat": <number>, "lon": <number>, "heading_to": {{"place": "<stated destination>", "lat": <number>, "lon": <number>}} or null}}
Include it even when the item is otherwise not relevant (then keep "relevant": false). Only US aircraft carriers; ignore other ships.

Transfer object: {{"kind": "delivery" | "pledge" | "interdiction", "supplier": "<ISO alpha-2>", "recipient": "<ISO alpha-2>", "mode": "air" | "sea" | "land" | "unspecified", "from": {{"place": "...", "lat": <number>, "lon": <number>}} or null, "to": {{"place": "...", "lat": <number>, "lon": <number>}} or null, "via": [{{"place": "<named transit hub>", "lat": <number>, "lon": <number>}}], "what": "<max 8 words>", "flights": <int> or null, "value_usd": <number> or null}}

Rules:
- Freshness: an item is relevant only if the event it reports happened within about 24 hours before the item was posted ("posted"). Articles that recap, react to, or analyze something older are not relevant, unless they reveal significant new facts about it (new casualty figures, a new attribution, a new official response); in that case the new facts are the event. Always fill "happened" when the item states or clearly implies when it happened ("on Tuesday", "overnight", "yesterday"), working from the posted date. News headlines are often undated and written in the present tense even when a story is republished months later: if you know from your own knowledge that the event happened earlier, put that date in "happened" (the item will then be treated as old).
- Write the summary yourself in plain, neutral English. Translate non-English items. Do not copy sentences from the item.
- When the source is a party to the conflict, attribute the claim in the summary (for example "Russian MoD claims...", "IDF says...").
- For hybrid incidents and incursions, say who blames whom exactly as the item does (for example "Polish officials suspect Russian involvement"). Never state attribution the item does not make.
- Never add facts that are not in the item. Unknown casualty numbers are null.
- lat/lon: your best estimate for the named place; null if you cannot place it at least at city or district level. Incidents at sea always get coordinates: work them out from the stated reference ("23 nautical miles northeast of Khasab", "off Fujairah"), or use the center of the named strait or sea. Put the sea area's name (for example "Strait of Hormuz") in place.
- Ship attacks: when a report says "unknown projectile", keep it unknown; name an attacker only when a source does (for example "US Central Command says an Iranian drone struck the tanker").
- severity 3 = major (10 or more killed, strike on a capital or critical infrastructure, large territorial change, direct combat between major powers, attack with dozens of missiles or drones, a ceasefire or peace deal signed or collapsing, an alliance invoked); 2 = notable (including high-level talks or emergency alliance meetings); 1 = minor or local.
- attacker: the country whose forces carried out a strike, launch, raid, or incursion, when the item states or clearly implies it ("Russian drones" = RU, "Ukrainian drones hit a refinery" = UA, Houthi missiles = YE, Hezbollah rockets = LB, Iranian missiles = IR). For interceptions, the side whose weapons were intercepted. Otherwise null.
- parties: for diplomacy and legal items only, the countries (ISO alpha-2) or bodies (UN, EU, NATO, AU, ICC, ICJ) whose officials take part: who meets whom, who signs, who files or rules (Netanyahu visiting the UAE = ["IL", "AE"]; Iran proposing a deal to the US = ["IR", "US"]). Otherwise [].
- origins: launch or firing areas the item actually names (for example "launched from Kursk and Primorsko-Akhtarsk"), at most 6, with your coordinate estimate for each. Use [] when none are named. Never guess a launch site.
- launched / intercepted: totals for a mass air attack when the item gives them ("Russia launched 120 drones, 98 were shot down" = 120 / 98). Otherwise null.
- alert: true when the item is only a real-time warning or tracking update about drones or missiles still in flight (for example an air force post that a drone is heading toward, approaching, or passing a place), with no hit, interception, damage, or casualties reported. Put place and lat/lon at the place named as the target or current position, and type it missile_drone. Otherwise false. Drones or aircraft entering another country's airspace are incursions, not alerts.
- For preparations or buildups aimed at a country, place the event in that country (its capital if nothing more specific), and say in the summary that it is planning or preparation, not action.
- For diplomacy, place the event where the meeting or signing happened; if no place is given, use the capital of the main party. Use the theater of the conflict it concerns, even if the meeting is elsewhere.
- transfer kind: "delivery" = weapons observed or reported moving or arriving (tracked flights, imaged ships, confirmed arrivals); "pledge" = a package announced, approved, or sold but not yet reported delivered; "interdiction" = a shipment seized, intercepted, or destroyed in transit.
- transfer from / to: only departure and arrival points the item names (airfield, port, city). Use null when none is named; never substitute a capital. via: transit hubs the item names (for example Ramstein, Rzeszow), else [].
- For an arms_transfer, put the event's own place and lat/lon at the named arrival point, or for an interdiction where it was seized; if none is named, use the recipient country's capital. Use the theater of the conflict the weapons are for.
- legal_basis: only when the item states the justification the acting state gives for using force (for example "self-defense under UN Charter Article 51", "host-state consent", "2001 AUMF"). Never infer one. For legal-type events, place them where the step happened (UN headquarters, The Hague, Washington) but use the theater of the conflict concerned.
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
       r"wars?|wartime|visit(?:s|ed|ing)?|trip|met|meets?|meeting|hosts?|hosted|"
       r"talks|negotiat(?:e|es|ed|ing|ions?)|agreements?|accords?|summits?|mediat(?:e|ed|or|ors|ion)|envoys?|"
       r"peace|pact|treaty|security council|article 4|article 5|foreign ministers?|defen[cs]e ministers?|"
       r"chiefs of staff|delegations?|military|pentagon|southcom|southern command|deploy(?:s|ed|ing|ment|ments)?|"
       r"build-?up|mobili[sz](?:e|ed|ation)|on notice|cartels?|guerrillas?|eln|farc|gangs?|"
       r"ataques?|bombardeos?|enfrentamientos?|militares|ej[eé]rcito|muertos|fuerzas armadas|"
       r"carriers?|strike group|cvn|uss|airlift|air ?bridge|shipments?|deliver(?:y|ies|ed)|military aid|"
       r"c-17|il-76|antonov|arms|weapons|munitions|ammunition|article 51|war powers|aumf|icj|icc|"
       r"self-defen[cs]e|warrants?|rulings?|provisional measures|legal basis|"
       r"tankers?|vessels?|ships?|shipping|cargo|freighters?|bulk carrier|container ship|merchant|mariners?|seafarers?|"
       r"crew|ukmto|ambrey|jmic|projectiles?|hormuz|bab el-mandeb|red sea|gulf of aden|hijack(?:ed|ing)?|boarded|"
       r"mines?|limpet|sank|sinking|ablaze|adrift|hits?")
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


# Words added to the filter on 2026-09-27. Posts that matched only these were rejected before
# then; run.py uses this once to give them another look.
_ADDED_WORDS = re.compile(r"\b(?:wars?|wartime|visit(?:s|ed|ing)?|trip|met|meets?|meeting|hosts?|hosted)\b", re.IGNORECASE)


def rejected_before_added_words(item: dict) -> bool:
    text = item.get("text", "")
    return (item.get("prefilter", True) and bool(CONFLICT_RE.search(text))
            and not CONFLICT_RE.search(_ADDED_WORDS.sub(" ", text)))


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
        # backfilled items carry their own, longer age limit
        if hours_since(it.get("time"), now) <= it.get("max_age_h", settings["max_item_age_hours"]):
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


def _place(o) -> dict | None:
    if not isinstance(o, dict):
        return None
    lat, lon = _num(o.get("lat"), -90, 90), _num(o.get("lon"), -180, 180)
    if lat is None or lon is None:
        return None
    return {"place": (str(o.get("place")).strip()[:80] if o.get("place") else None), "lat": round(lat, 3), "lon": round(lon, 3)}


def _iso2(v) -> str | None:
    v = str(v or "").upper().strip()
    return v if re.fullmatch(r"[A-Z]{2}", v) else None


def _clean_transfer(t) -> dict | None:
    if not isinstance(t, dict):
        return None
    via = [v for v in (_place(x) for x in (t.get("via") or [])[:3]) if v]
    out = {
        "kind": t.get("kind") if t.get("kind") in ("delivery", "pledge", "interdiction") else "delivery",
        "via": via,
        "value_usd": _num(t.get("value_usd"), 0, 1e13),
        "supplier": _iso2(t.get("supplier")),
        "recipient": _iso2(t.get("recipient")),
        "mode": t.get("mode") if t.get("mode") in ("air", "sea", "land") else "unspecified",
        "from": _place(t.get("from")),
        "to": _place(t.get("to")),
        "what": (str(t["what"]).strip()[:80] if t.get("what") else None),
        "flights": _int_or_none(t.get("flights")),
    }
    return out if out["supplier"] and out["recipient"] else None


def clean_carrier(obj: dict, item: dict) -> dict | None:
    """A carrier position report, independent of whether the item is a map event."""
    c = obj.get("carrier") if isinstance(obj, dict) else None
    if not isinstance(c, dict):
        return None
    m = re.search(r"(\d{2})", str(c.get("hull") or ""))
    here = _place(c)
    if not m or not here:
        return None
    status = c.get("status") if c.get("status") in ("departed", "underway", "operating", "arrived", "in port") else "operating"
    return {"hull": f"CVN-{m.group(1)}", "status": status, **here, "heading_to": _place(c.get("heading_to")),
            "time": item["time"], "source": item["source"], "url": item["url"]}


def _happened(value, item: dict) -> str | None:
    """When the event happened, clamped to no later than the post itself. Date-only values
    are read as the end of that day (the latest the event could have happened)."""
    if not value:
        return None
    v = str(value).strip()
    t = parse_time(v + "T23:59:00Z" if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) else v)
    posted = parse_time(item.get("time"))
    if not t or not posted:
        return None
    return iso(min(t, posted))


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
    attacker = str(obj.get("attacker") or "").upper().strip()
    origins = []
    raw_origins = obj.get("origins") if isinstance(obj.get("origins"), list) else []
    if not raw_origins and obj.get("origin_lat") is not None:  # older single-origin shape
        raw_origins = [{"place": obj.get("origin_place"), "lat": obj.get("origin_lat"), "lon": obj.get("origin_lon")}]
    for o in raw_origins[:6]:
        if not isinstance(o, dict):
            continue
        olat, olon = _num(o.get("lat"), -90, 90), _num(o.get("lon"), -180, 180)
        if olat is not None and olon is not None:
            origins.append({"place": (str(o.get("place")).strip()[:80] if o.get("place") else None),
                            "lat": round(olat, 3), "lon": round(olon, 3)})
    try:
        severity = min(3, max(1, int(obj.get("severity") or 1)))
    except (TypeError, ValueError):
        severity = 1
    return {
        "type": etype,
        "happened": _happened(obj.get("happened"), item),
        "summary": summary[:240],
        "place": (str(obj["place"]).strip()[:120] if obj.get("place") else None),
        "admin1": (str(obj["admin1"]).strip()[:120] if obj.get("admin1") else None),
        "country": country if re.fullmatch(r"[A-Z]{2}", country) else None,
        "lat": _num(obj.get("lat"), -90, 90),
        "lon": _num(obj.get("lon"), -180, 180),
        "origins": origins,
        "attacker": attacker if re.fullmatch(r"[A-Z]{2}", attacker) else None,
        "parties": sorted({str(x).upper().strip() for x in (obj.get("parties") if isinstance(obj.get("parties"), list) else [])
                           if re.fullmatch(r"[A-Z]{2,4}", str(x).upper().strip())})[:6],
        "launched": _int_or_none(obj.get("launched")),
        "intercepted": _int_or_none(obj.get("intercepted")),
        "alert": str(obj.get("alert")).lower() == "true",
        "transfer": _clean_transfer(obj.get("transfer")) if etype == "arms_transfer" else None,
        "legal_basis": (str(obj["legal_basis"]).strip()[:120] if obj.get("legal_basis") else None),
        "theater": str(obj.get("theater") or "").strip(),
        "severity": severity,
        "claim": "official_claim" if obj.get("claim") == "official_claim" else "report",
        "killed": _int_or_none(obj.get("killed")),
        "injured": _int_or_none(obj.get("injured")),
        "item": item,
    }


def run(queue: list[dict], state: dict, settings: dict, now: datetime, disabled: bool = False):
    """Returns (records, leftover_queue, calls_used, carrier_reports)."""
    token = os.environ.get("LLM_API_KEY", "").strip()
    if disabled or not token:
        if not token:
            log("[extract] LLM_API_KEY is not set; add the GEMINI_API_KEY repository secret (see README)")
        return [], queue, 0, []
    allowed = calls_allowed(state, settings, now)
    batches = make_batches(queue, settings)
    log(f"[extract] queue={len(queue)} batches={len(batches)} allowed_calls={allowed}")
    if not allowed or not batches:
        return [], queue, 0, []

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
            return [], queue, state["llm_calls"]["count"] - before, []
        if chosen is None:
            log("[extract] no model returned a usable answer; see the probe lines above")
            return [], queue, state["llm_calls"]["count"] - before, []
        allowed = max(0, allowed - (state["llm_calls"]["count"] - before))

    records: list[dict] = []
    carriers: list[dict] = []
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
                cv = clean_carrier(obj, batch[i])
                if cv:
                    carriers.append(cv)
                rec = _clean_record(obj, batch[i])
                if rec:
                    records.append(rec)
                elif batch[i].get("platform") == "rss":
                    # Headlines the model set aside, kept briefly so a missed story can be traced.
                    # News headlines and links only: post text from other platforms is never stored.
                    state.setdefault("model_rejected", []).append({
                        "time": batch[i]["time"], "source": batch[i]["source"], "url": batch[i]["url"],
                        "headline": batch[i]["text"].split("\n", 1)[0][:200], "at": iso(now)})
    state["model_rejected"] = state.get("model_rejected", [])[-400:]
    leftover = [it for it in queue if it["id"] not in done and int(it.get("attempts", 0)) < 3]
    return records, leftover[: settings["pending_max"]], used, carriers


def ask_json(system_prompt: str, user_text: str, state: dict, settings: dict, now: datetime,
             max_tokens: int = 4000) -> dict | None:
    """One budgeted model call outside the batch loop. Returns parsed JSON or None."""
    token = os.environ.get("LLM_API_KEY", "").strip()
    chosen = state.get("llm_model")
    if not token or not chosen or calls_allowed(state, settings, now) <= 0:
        return None
    body = {
        "model": chosen["model"], "temperature": 0, "max_tokens": max_tokens, "stream": False,
        "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_text}],
    }
    if chosen.get("json_mode", True):
        body["response_format"] = {"type": "json_object"}
    state["llm_calls"]["count"] += 1
    try:
        r = _post(chosen["url"], body, token)
        if r.status_code >= 400:
            log(f"[extract] one-off call failed: {_describe(r)}")
            return None
        return _parse_json_object(_content_from_response(r))
    except Exception as exc:  # noqa: BLE001
        log(f"[extract] one-off call failed: {exc}")
        return None
