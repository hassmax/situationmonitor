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
- every judgment must cite events of that region, and judgments citing anything else are dropped;
- the confidence shown is worked out here from the cited events, never by the model: "higher" with
  CORROBORATED_HIGH or more corroborated events, "moderate" with one, "low" with none;
- a judgment resting on no corroborated event must say so in its words ("reports suggest", "claims"),
  else it is dropped;
- no outside knowledge and no predictions; event ids written into the text are stripped.

One model call (purpose "analysis", paced daily share `analysis_daily_max`), at most every
MIN_INTERVAL and only when the last WINDOW_HOURS of events changed; skipped when fewer than
brief_min_calls calls are left. If the call fails or nothing valid comes back, the previous analysis
stays with its time.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import timedelta

from common import haversine_km, iso, log, parse_time

WINDOW_HOURS = 6
ANALYSIS_DAYS = 3
MIN_INTERVAL = timedelta(hours=1)
MAX_EVENTS_PER_REGION = 40
PER_REGION = 2
MAX_JUDGMENTS = 14
REACH_KM = 2500           # carriers and aircraft counted for a region: this far from its camera point
CORROBORATED_HIGH = 2
VERSION = 1
TRENDS = ("escalating", "de-escalating", "shifting", "steady")

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

Say what is changing in each region: the direction things are moving, as an analyst would put it in one line ("US increasing force posture in the Middle East", "Russia shifting strikes to Ukraine's energy grid", "Talks between Pakistan and Afghanistan stalling"). Focus on what the last 6 hours add to the picture of the last 3 days.

Rules:
- Use only what you are given. No outside knowledge, no background, no predictions of what will happen next.
- Every judgment must rest on the events it cites (ids from that region only), and the cited events together must support it. A trend ("increasing", "withdrawing", "shifting", "escalating") needs at least two cited events pointing the same way, or one event that states the trend itself.
- Carriers and aircraft are context: mention them only together with events, never as the only basis.
- Weigh confidence: events marked single-source or one side's claim are weaker. When a judgment rests on them, say so in the words ("reports suggest", "Russia claims", "unconfirmed reports"). Keep each event's own attribution. Never state a single-source report or a claim as fact.
- Name actors only as the events name them. Don't assign blame or intent the events don't state.
- Plain, neutral language; no drama. Keep numbers exactly as given.
- Never write event ids in the text; they go only in "ids".
- Only regions where something notable is happening; at most 2 judgments per region, most important first. Skip a region rather than write filler.
- "trend" is one of: escalating (more or heavier fighting, strikes, buildup), de-escalating (less fighting, withdrawals, ceasefires, talks advancing), shifting (a change of focus, place or method rather than of level), steady (activity continuing at about the same level, worth noting).

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
        if not recent:
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
        out.append(r)
    return out


def fingerprint(events: list[dict], now) -> str:
    rows = sorted((e["id"], e.get("updated"), e.get("status"), e.get("summary"), e.get("severity"))
                  for e in events if _hours_ago(e, now) <= WINDOW_HOURS)
    return hashlib.sha1(json.dumps(rows, ensure_ascii=False).encode("utf-8")).hexdigest()


# Words that attribute or hedge: a judgment resting on no corroborated event must use one.
_HEDGE = re.compile(r"\b(?:report\w*|claim\w*|say|says|said|stat(?:e|es|ed|ing)|according|alleg\w*|single-source|"
                    r"unconfirmed|unverified|aligned|reportedly|assert\w*|accus\w*|suggest\w*|indicat\w*|appear\w*|"
                    r"possibl\w*|may|might)\b", re.IGNORECASE)
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
    t = {"corroborated": 0, "single_source": 0, "claimed": 0}
    for i in ids:
        s = by_id[i].get("status")
        t["corroborated" if s == "corroborated" else "claimed" if s == "claimed" else "single_source"] += 1
    return t


def confidence(t: dict) -> str:
    return "higher" if t["corroborated"] >= CORROBORATED_HIGH else "moderate" if t["corroborated"] else "low"


def validate(reply, shown: list[dict], events: list[dict]) -> list[dict] | None:
    """Regions with their checked judgments, in the order the regions were shown."""
    if not isinstance(reply, dict) or not isinstance(reply.get("regions"), list):
        return None
    by_id = {e["id"]: e for e in events}
    allowed = {r["region"]: {f["id"] for f in r["events"]} for r in shown}
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
            if not ids or not headline or trend not in TRENDS or not all(i in allowed[rid] for i in ids):
                continue
            t = tally(ids, by_id)
            if not t["corroborated"] and not _HEDGE.search(f"{headline} {text}"):
                continue  # resting on single-source reports or claims, stated as fact
            got.setdefault(rid, []).append({"headline": headline, "trend": trend, "text": text, "ids": ids[:12],
                                            "tally": t, "confidence": confidence(t)})
            total += 1
    if not got:
        return None
    return [{"theater": rid, "name": names[rid], "judgments": got[rid]} for rid in allowed if rid in got]


def update(state: dict, events: list[dict], theaters: list[dict], carriers: list[dict], flights: dict | None,
           settings: dict, now, ask, remaining: int, share: int) -> None:
    """Write a new analysis into state["analysis"] when it is due; otherwise keep the previous one."""
    tried = parse_time(state.get("analysis_attempt"))
    if tried and now - tried < MIN_INTERVAL:
        return
    fp = fingerprint(events, now)
    if state.get("analysis") and state.get("analysis_fp") == fp:
        return
    shown = regions(events, theaters, carriers, flights, now)
    if not shown:
        state["analysis"] = {"generated_at": iso(now), "window_hours": WINDOW_HOURS, "version": VERSION, "regions": []}
        state["analysis_fp"] = fp
        return
    if remaining < int(settings.get("brief_min_calls", 5)) or share <= 0:
        log(f"[analyst] skipped: {remaining} model calls left today, {share} in its share")
        return
    state["analysis_attempt"] = iso(now)
    payload = {"now": iso(now), "window_hours": WINDOW_HOURS, "context_days": ANALYSIS_DAYS, "regions": shown}
    reply = ask(PROMPT, json.dumps(payload, ensure_ascii=False), state, settings, now, max_tokens=3000, purpose="analysis")
    out = validate(reply, shown, events)
    if out is None:
        log("[analyst] no usable analysis from the model; keeping the previous one")
        return
    state["analysis"] = {"generated_at": iso(now), "window_hours": WINDOW_HOURS, "context_days": ANALYSIS_DAYS,
                         "version": VERSION, "regions": out}
    state["analysis_fp"] = fp
    log(f"[analyst] written: {sum(len(r['judgments']) for r in out)} judgments for {len(out)} regions")
