"""Regional analyst: a separate agent that says what is changing in each region ("US increasing force
posture in the Middle East", "US withdrawing aircraft from the Middle East"), shown at the top of
the list in place of the old "What changed in the last 6 hours" bullets (the owner's request,
2026-10-04).

Each region (listed theater) is shown to the model with its events from the last ANALYSIS_DAYS
(the last WINDOW_HOURS marked new), activity counts for the last 6 and 24 hours against the days
before, the US carriers within REACH_KM of the region, and the military aircraft the flight agent
sees over it. For each region where something is changing, the model writes up to PER_REGION
judgments: a short headline, a direction (TRENDS), one or two sentences, and the ids of the
events it rests on.

What keeps it honest (the old per-theater lines were removed because they folded single-source
reports into statements of fact):
- every judgment must cite at least MIN_CITED events or tracked flights of that region (a trend, not
  one event's news; one flight of a notable kind, SINGLE_FLIGHT_ROLES, may stand alone), and
  judgments citing anything else are dropped;
- the confidence shown is worked out here from the cited events, never by the model: "higher" with
  CORROBORATED_HIGH or more corroborated events, "moderate" with one, "low" with none;
- a judgment citing any event that isn't corroborated must say so in its words ("reports suggest",
  "claims"), else it is dropped (in the first trial a single-source capture, cited beside a
  corroborated summit, was stated as fact), and may not call itself corroborated or confirmed;
- no outside knowledge and no predictions; event ids written into the text are stripped.

At most every MIN_INTERVAL, and only when the last WINDOW_HOURS of events changed. Who writes it
(ORDER, setting `analysis_order`): the free outside providers in providers.py (Cerebras, then
OpenRouter), each with its own key and daily limit, then Gemini's regular Flash, then Gemini's usual
model (Flash-Lite, which in the first trial wrote single events up as trends and hedged corroborated
ones). Gemini calls use the shared budget (purpose "analysis", paced share `analysis_daily_max`;
skipped when fewer than brief_min_calls calls are left). The first valid answer is kept, with the
model that wrote it (`by`); if none, the previous analysis stays with its time.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import timedelta

import flights as flights_mod
import providers
from extract import _estimate_tokens as extract_tokens
from common import haversine_km, iso, log, parse_time

WINDOW_HOURS = 6
ANALYSIS_DAYS = 3
MIN_INTERVAL = timedelta(hours=1)
MAX_EVENTS_PER_REGION = 40
PER_REGION = 2
MAX_JUDGMENTS = 14
REACH_KM = 2500           # carriers and aircraft counted for a region: this far from its camera point
CORROBORATED_HIGH = 2
ANALYSIS_WAIT = 65  # seconds the analysis may wait for an outside provider's per-minute limit
FIT_MIN_EVENTS = 12  # the fewest events a region keeps when a request is trimmed to fit a provider
MIN_CITED = 2
MAX_FLIGHTS_PER_REGION = 15
# A single tracked flight of these kinds may be a line of its own ("KC-135 tanker left Al Udeid and
# landed at Incirlik", the owner's example, 2026-10-04); transports only in groups.
SINGLE_FLIGHT_ROLES = {"bomber", "tanker", "surveillance", "command", "government"}
VERSION = 1
TRENDS = ("escalating", "de-escalating", "shifting", "steady")
# Who is asked, in order (providers.py): the free outside providers first, whose models write better
# analysis, then Gemini's regular Flash, then Gemini's usual model. A provider without its key is skipped.
ORDER = ["cerebras", "openrouter", "gemini-flash", "gemini"]

TYPES = {
    "airstrike": "airstrike", "missile_drone": "drone or missile attack", "artillery": "shelling",
    "ground": "ground fighting", "territory": "territorial change", "air_defense": "air defense",
    "naval": "naval incident", "explosion": "explosion", "deployment": "deployment or exercise",
    "diplomacy": "diplomacy", "ceasefire": "diplomacy", "hybrid": "sabotage or hybrid attack",
    "incursion": "airspace or border incursion", "arms_transfer": "arms or forces moved", "legal": "legal step",
    "production": "arms production",
}
FAMILY = {
    "airstrike": "strikes", "missile_drone": "strikes", "artillery": "strikes", "explosion": "strikes", "air_defense": "strikes",
    "ground": "ground fighting", "territory": "ground fighting", "naval": "naval", "deployment": "force posture",
    "arms_transfer": "force posture", "incursion": "force posture", "production": "force posture", "hybrid": "hybrid",
    "diplomacy": "diplomacy", "ceasefire": "diplomacy", "legal": "diplomacy",
}
CONFIDENCE = {"corroborated": "corroborated by independent sources", "unconfirmed": "single-source report",
              "claimed": "claimed only by sources aligned with one side"}

PROMPT = """You are a careful military and political analyst writing for a live armed-conflict map. For each region you get the map's own events from the last 3 days (those from the last 6 hours are marked "new"), activity counts against the days before, and context: US aircraft carriers near the region and military aircraft broadcasting their position over it.

Say what is changing in each region: the direction things are moving, as an analyst would put it in one line. A judgment is a trend drawn from several events, not the news of one event.

Good headlines (actor, direction, what, where):
- "US increasing force posture in the Gulf"
- "US withdrawing bombers from Europe"
- "Russia intensifying drone strikes on Ukraine's energy grid"
- "Sudan's army regaining ground in North Kordofan"
- "Houthis widening attacks to Saudi oil sites"
- "Pakistan–Afghanistan talks stalling"
Bad: "Japan lodges protest after Marine arrest" (one event, no direction), "Army retakes town as summit convenes" (two unrelated things).

What to look for: force posture (deployments, forces moved, carriers, tankers and transports in the air, exercises), the level and targets of strikes and fighting, ground changing hands, attacks spreading to new places or targets, diplomacy advancing or stalling. Focus on what the last 6 hours add to the picture of the last 3 days.

Rules:
- Use only what you are given. No outside knowledge, no background, no predictions of what will happen next.
- Every judgment cites at least two events of its region (ids from that region only) that point the same way. Never combine unrelated events into one judgment.
- "flight_movements" are military aircraft tracked by their own transponders (public ADS-B data, adsb.lol) taking off from or landing at watched bases, each with an id you may cite like an event. They show where aircraft went and when, never why. Several aircraft of one kind leaving or reaching a base can be a force-posture judgment ("US tankers leaving Al Udeid", "US bombers arriving at Diego Garcia"). Write a movement as the data shows it ("KC-135 tankers took off from Al Udeid and were last seen 400 km to the northwest"); give a destination only where a landing was seen. Never infer a mission, target or intent from flights. One flight of a bomber, tanker, surveillance aircraft, airborne command post or government VIP flight may be a line of its own ("KC-135 tanker left Al Udeid and landed at Incirlik", trend "shifting"); transports only when several move together.
- Carriers and the count of aircraft broadcasting now are context only: mention them together with cited events or flights, never as the only basis.
- Weigh confidence: events marked single-source or one side's claim are weaker. When a judgment cites any of them, say so in the words ("reports suggest", "Russia claims", "unconfirmed reports"). Keep each event's own attribution. Never state a single-source report or a claim as fact. Corroborated events are stated plainly, without "unconfirmed".
- The cited events must be separate incidents showing a pattern (several strikes, several deployments). One incident and the reactions to it (an arrest and the protest about it) is news, not a trend: leave it out.
- Name actors only as the events name them. Don't assign blame or intent the events don't state.
- Plain, neutral language; no drama. Keep numbers exactly as given.
- Never write event ids in the text; they go only in "ids".
- Only regions where something notable is happening; at most 2 judgments per region, most important first. Skip a region rather than write filler.
- Never write "corroborated" or "confirmed" in the text: the confidence is shown beside each judgment, worked out from the events it cites.
- "trend" is one of: escalating (more or heavier fighting, strikes, buildup, threats, expulsions, new sanctions), de-escalating (less fighting, withdrawals, ceasefires, talks agreed, proposed or advancing), shifting (a change of focus, place or method rather than of level), steady (a pattern of several events continuing at about the same level; never for a one-off).

Reply with one JSON object and nothing else:
{"regions": [{"region": "<region id>", "judgments": [{"headline": "<at most 12 words>", "trend": "<trend>", "text": "<one or two sentences>", "ids": ["<event id>", ...]}]}]}"""


def _hours_ago(e: dict, now) -> float:
    t = parse_time(e.get("time"))
    return (now - t).total_seconds() / 3600 if t else 1e9


def _facts(e: dict, now) -> dict:
    f = {"id": e["id"], "hours_ago": round(_hours_ago(e, now), 1), "new": _hours_ago(e, now) <= WINDOW_HOURS,
         "type": "drone and missile alerts" if e.get("alert") else TYPES.get(e.get("type"), e.get("type")),
         "place": e.get("place"), "country": e.get("country"), "summary": e.get("summary"),
         "confidence": CONFIDENCE.get(e.get("status"), e.get("status")), "severity": e.get("severity")}
    if e.get("status") == "claimed":
        f["aligned_with"] = sorted({r["side"] for r in e.get("reports", []) if r.get("side")})
    if e.get("attacker"):
        f["actor"] = e["attacker"]
    if e.get("parties"):
        f["parties"] = e["parties"]
    if e.get("wave"):
        f["attack_wave"] = {"launched": e.get("launched"), "intercepted": e.get("intercepted"), "locations": len(e.get("targets") or [])}
    t = e.get("transfer")
    if t:
        f["transfer"] = {k: t.get(k) for k in ("supplier", "recipient", "what", "mode") if t.get(k)}
        for end in ("from", "to"):
            if isinstance(t.get(end), dict) and t[end].get("place"):
                f["transfer"][end] = t[end]["place"]
    if e.get("possibly_old"):
        f["possibly_old_story"] = True
    return f


def _counts(events: list[dict], now) -> dict:
    """Events by kind: last 6 h, last 24 h, and the daily average of the 3 days before."""
    out: dict[str, dict] = {}
    for e in events:
        h = _hours_ago(e, now)
        fam = "drone and missile alerts" if e.get("alert") else FAMILY.get(e.get("type"), "other")
        c = out.setdefault(fam, {"last_6h": 0, "last_24h": 0, "daily_avg_previous_3_days": 0.0})
        if h <= WINDOW_HOURS:
            c["last_6h"] += 1
        if h <= 24:
            c["last_24h"] += 1
        elif h <= 96:
            c["daily_avg_previous_3_days"] += 1 / 3
    for c in out.values():
        c["daily_avg_previous_3_days"] = round(c["daily_avg_previous_3_days"], 1)
    return out


def _near(th: dict, lat, lon) -> bool:
    cam = th.get("camera") or {}
    return lat is not None and cam.get("lat") is not None and haversine_km(cam["lat"], cam["lng"], lat, lon) <= REACH_KM


def regions(events: list[dict], theaters: list[dict], carriers: list[dict], flights: dict | None, now) -> list[dict]:
    """What the model is shown, region by region (listed theaters with events in the last days)."""
    out = []
    aircraft = (flights or {}).get("aircraft") or []
    for th in theaters:
        if th.get("listed") is False:
            continue
        mine = [e for e in events if e.get("theater") == th["id"]]
        recent = [e for e in mine if _hours_ago(e, now) <= ANALYSIS_DAYS * 24]
        moves = [m for m in (flights or {}).get("movements") or [] if any(_near(th, lat, lon) for lat, lon in m["places"])]
        if not recent and not moves:
            continue
        recent.sort(key=lambda e: (_hours_ago(e, now) > WINDOW_HOURS, -(e.get("severity") or 1), _hours_ago(e, now)))
        r = {"region": th["id"], "name": th["name"], "activity": _counts(mine, now),
             "events": [_facts(e, now) for e in recent[:MAX_EVENTS_PER_REGION]]}
        near_cvn = [{"carrier": c.get("name"), "where": c.get("place"), "status": c.get("status"), "as_of": c.get("as_of")}
                    for c in carriers if not c.get("at_home") and _near(th, c.get("lat"), c.get("lon"))]
        if near_cvn:
            r["us_carriers_nearby"] = near_cvn
        planes = [f for f in aircraft if _near(th, f.get("lat"), f.get("lon"))]
        if planes:
            by_role: dict[str, int] = {}
            for f in planes:
                by_role[f["role"]] = by_role.get(f["role"], 0) + 1
            r["military_aircraft_broadcasting_now"] = by_role
        if moves:
            moves.sort(key=lambda m: (-flights_mod.IMPORTANCE.get(m["role"], 0), -(parse_time(m.get("time")) or now).timestamp()))
            r["flight_movements"] = [{"id": m["id"], "role": m["role"], "what": m["text"]} for m in moves[:MAX_FLIGHTS_PER_REGION]]
        out.append(r)
    return out


def fingerprint(events: list[dict], now, flights: dict | None = None) -> str:
    rows = sorted((e["id"], e.get("updated"), e.get("status"), e.get("summary"), e.get("severity"))
                  for e in events if _hours_ago(e, now) <= WINDOW_HOURS)
    rows += sorted((m["id"], m["text"]) for m in (flights or {}).get("movements") or []
                   if m.get("time") and _hours_ago({"time": m["time"]}, now) <= WINDOW_HOURS)
    return hashlib.sha1(json.dumps(rows, ensure_ascii=False).encode("utf-8")).hexdigest()


# Words that attribute or hedge: a judgment resting on no corroborated event must use one.
_HEDGE = re.compile(r"\b(?:report\w*|claim\w*|say|says|said|stat(?:e|es|ed|ing)|according|alleg\w*|single-source|"
                    r"unconfirmed|unverified|aligned|reportedly|assert\w*|accus\w*|suggest\w*|indicat\w*|appear\w*|"
                    r"possibl\w*|may|might)\b", re.IGNORECASE)
_CONFIRMED = re.compile(r"\b(?:corroborat\w*|confirm(?:s|ed)?)\b", re.IGNORECASE)
_ID_IN_TEXT = re.compile(r"\s*[(\[]\s*(?:ids?:?\s*)?[0-9a-f]{12}(?:\s*[,;/]\s*[0-9a-f]{12})*\s*[)\]]|\b[0-9a-f]{12}\b")


def _clean(text: str, limit: int, ids=()) -> str:
    """The text without event ids the model sometimes writes into it ("... (3d4de44f80ca).")."""
    text = _ID_IN_TEXT.sub("", str(text or ""))
    if ids:
        known = "|".join(re.escape(i) for i in ids)
        text = re.sub(rf"\s*[(\[]\s*(?:ids?:?\s*)?(?:{known})(?:\s*[,;/]\s*(?:{known}))*\s*[)\]]|\b(?:{known})\b", "", text)
    text = re.sub(r"\s+([.,;:])", r"\1", re.sub(r"\s{2,}", " ", text)).strip()
    return text[:limit]


def tally(ids: list[str], by_id: dict) -> dict:
    """Cited evidence by kind. Tracked flights (the aircraft's own transponder data) count as
    tracked, which weighs like a corroborated event: they show where aircraft went, not why."""
    t = {"corroborated": 0, "single_source": 0, "claimed": 0, "tracked": 0}
    for i in ids:
        s = by_id[i].get("status")
        t["tracked" if s == "tracked" else "corroborated" if s == "corroborated" else "claimed" if s == "claimed" else "single_source"] += 1
    return t


def confidence(t: dict) -> str:
    strong = t["corroborated"] + t.get("tracked", 0)
    return "higher" if strong >= CORROBORATED_HIGH else "moderate" if strong else "low"


def validate(reply, shown: list[dict], events: list[dict], flights: dict | None = None) -> list[dict] | None:
    """Regions with their checked judgments, in the order the regions were shown."""
    if not isinstance(reply, dict) or not isinstance(reply.get("regions"), list):
        return None
    moves = {m["id"]: m for m in (flights or {}).get("movements") or []}
    by_id = {e["id"]: e for e in events} | {i: {"status": "tracked"} for i in moves}
    allowed = {r["region"]: {f["id"] for f in r["events"]} | {m["id"] for m in r.get("flight_movements") or []} for r in shown}
    names = {r["region"]: r["name"] for r in shown}
    got: dict[str, list] = {}
    total = 0
    for reg in reply["regions"]:
        rid = reg.get("region") if isinstance(reg, dict) else None
        if rid not in allowed:
            continue
        for j in reg.get("judgments") or []:
            if not isinstance(j, dict) or total >= MAX_JUDGMENTS or len(got.get(rid, [])) >= PER_REGION:
                continue
            ids = [i for i in dict.fromkeys(j.get("ids") or []) if isinstance(i, str)]
            headline, text = _clean(j.get("headline"), 120, ids), _clean(j.get("text"), 400, ids)
            trend = str(j.get("trend") or "").lower()
            single_flight = len(ids) == 1 and ids[0] in moves and moves[ids[0]].get("role") in SINGLE_FLIGHT_ROLES
            if (len(ids) < MIN_CITED and not single_flight) or not headline or trend not in TRENDS or not all(i in allowed[rid] for i in ids):
                continue
            t = tally(ids, by_id)
            if (t["single_source"] or t["claimed"]) and not _HEDGE.search(f"{headline} {text}"):
                continue  # single-source reports or claims, stated as fact
            if (t["single_source"] or t["claimed"]) and _CONFIRMED.search(f"{headline} {text}"):
                continue  # "Corroborated reports indicate..." over a one-sided claim (Cerebras trial, 2026-10-04)
            flown = [{"label": moves[i]["label"], "url": moves[i]["url"], "what": moves[i]["text"]} for i in ids if i in moves]
            got.setdefault(rid, []).append({"headline": headline, "trend": trend, "text": text,
                                            "ids": [i for i in ids if i not in moves][:12], "flights": flown[:12],
                                            "tally": t, "confidence": confidence(t)})
            total += 1
    if not got:
        return None
    return [{"theater": rid, "name": names[rid], "judgments": got[rid]} for rid in allowed if rid in got]


def _fit(shown: list[dict], payload: dict, budget: int) -> tuple[list[dict], str]:
    """The regions with fewer events each, so the request (instructions included) stays within
    `budget` tokens: each region keeps its first events, which are the new ones, then the most
    serious (see `regions`)."""
    cap = MAX_EVENTS_PER_REGION
    while True:
        trimmed = [{**r, "events": r["events"][:cap]} for r in shown]
        text = json.dumps({**payload, "regions": trimmed}, ensure_ascii=False)
        if extract_tokens(PROMPT + text) <= budget or cap <= FIT_MIN_EVENTS:
            if cap < MAX_EVENTS_PER_REGION:
                log(f"[analyst] up to {cap} events a region, to fit the provider's per-minute limit")
            return trimmed, text
        cap -= 4


def update(state: dict, events: list[dict], theaters: list[dict], carriers: list[dict], flights: dict | None,
           settings: dict, now, ask, remaining: int, share: int) -> None:
    """Write a new analysis into state["analysis"] when it is due; otherwise keep the previous one."""
    tried = parse_time(state.get("analysis_attempt"))
    if tried and now - tried < MIN_INTERVAL:
        return
    fp = fingerprint(events, now, flights)
    if state.get("analysis") and state.get("analysis_fp") == fp:
        return
    shown = regions(events, theaters, carriers, flights, now)
    if not shown:
        state["analysis"] = {"generated_at": iso(now), "window_hours": WINDOW_HOURS, "version": VERSION, "regions": []}
        state["analysis_fp"] = fp
        return
    order = settings.get("analysis_order") or ORDER
    outside = [providers.configured(settings).get(s) for s in order if s not in ("gemini-flash", "gemini")]
    caps = {p["name"]: cap for p, cap in providers.routes(settings, "analysis")}
    outside = [p for p in outside if p and providers.has_room(p, caps.get(p["name"]), state, now, "analysis", 0)]
    if remaining < int(settings.get("brief_min_calls", 5)):
        share = 0  # Gemini's day is nearly used up: outside providers only
    if share <= 0 and not outside:
        log(f"[analyst] skipped: no outside provider available, {remaining} Gemini calls left today, {share} in its share")
        return
    state["analysis_attempt"] = iso(now)
    payload = {"now": iso(now), "window_hours": WINDOW_HOURS, "context_days": ANALYSIS_DAYS, "regions": shown}
    text = json.dumps(payload, ensure_ascii=False)
    out, by = None, None
    for step in order:
        if step in ("gemini-flash", "gemini"):
            # Gemini: regular Flash (also free, its own smaller daily limit) first, then the usual model
            if share <= 0:
                continue
            flash = (settings.get("llm_fallback_models") or [None])[0] if step == "gemini-flash" else None
            if step == "gemini-flash" and not flash:
                continue
            # thinking models (regular Flash) spend part of max_tokens before answering: 3,000 cut a reply short
            reply = ask(PROMPT, text, state, settings, now, max_tokens=8000, purpose="analysis", **({"model": flash} if flash else {}))
            share -= 1
            model, label = flash or (state.get("llm_model") or {}).get("model"), "Gemini"
        else:
            p = providers.configured(settings).get(step)
            if not p:
                continue
            seen, sent = shown, text
            if p.get("tokens_per_minute"):
                # Cerebras counts the prompt and the answer allowance against its minute's limit: a full
                # request (about 24,000 tokens plus 8,000, 2026-10-04) was refused even in a clear minute
                seen, sent = _fit(shown, payload, int(p["tokens_per_minute"]) - 8000)
            if not providers.has_room(p, caps.get(step), state, now, "analysis", extract_tokens(PROMPT + sent) + 2000):
                continue
            reply, model = providers.ask_json(p, PROMPT, sent, state, now, max_tokens=8000, purpose="analysis",
                                              max_wait=ANALYSIS_WAIT)
            label = p.get("label") or step
            out = validate(reply, seen, events, flights)
            if out is not None:
                by = f"{model} ({label})" if model else label
                break
            if reply is not None:
                log(f"[analyst] {step}: nothing usable in the reply")
            continue
        out = validate(reply, shown, events, flights)
        if out is not None:
            by = f"{model} ({label})" if model else label
            break
        if reply is not None:
            log(f"[analyst] {step}: nothing usable in the reply")
    if out is None:
        log("[analyst] no usable analysis from the model; keeping the previous one")
        return
    state["analysis"] = {"generated_at": iso(now), "window_hours": WINDOW_HOURS, "context_days": ANALYSIS_DAYS,
                         "version": VERSION, "by": by, "regions": out}
    if any(j.get("flights") for r in out for j in r["judgments"]):
        state["analysis"]["flight_credit"] = {"text": (flights or {}).get("attribution"), "url": (flights or {}).get("license_url")}
    state["analysis_fp"] = fp
    log(f"[analyst] written by {by}: {sum(len(r['judgments']) for r in out)} judgments for {len(out)} regions")
