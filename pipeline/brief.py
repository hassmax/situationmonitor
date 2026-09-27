"""Situation brief: a short, machine-written account of what changed in the last 6 hours.

Written from the dashboard's own events only, at most once an hour and only when those events
changed. One model call, from the shared daily budget; skipped when fewer than brief_min_calls
calls are left. Every bullet must cite the events it is based on, and bullets citing events that
don't exist are dropped. If the call fails or nothing valid is left, the previous brief stays,
with its original timestamp.
"""
from __future__ import annotations

import hashlib
import json
from datetime import timedelta

from common import iso, log, parse_time

WINDOW_HOURS = 6
MIN_INTERVAL = timedelta(hours=1)
MAX_EVENTS = 80
MAX_BULLETS = 6

TYPES = {
    "airstrike": "airstrike", "missile_drone": "drone or missile attack", "artillery": "shelling",
    "ground": "ground fighting", "territory": "territorial change", "air_defense": "air defense",
    "naval": "naval incident", "explosion": "explosion", "deployment": "deployment or exercise",
    "diplomacy": "diplomacy", "ceasefire": "diplomacy", "hybrid": "sabotage or hybrid attack",
    "incursion": "airspace or border incursion", "arms_transfer": "arms transfer", "legal": "legal step",
}
CONFIDENCE = {"corroborated": "corroborated by independent sources", "unconfirmed": "single-source report",
              "claimed": "claimed only by sources aligned with one side"}
NOTHING = "No new events on the map in the last 6 hours."

PROMPT = """You write a short situation brief for a live armed-conflict map. You get the map's own events from the last 6 hours. Use only these events.

Rules:
- Use only the supplied events. No outside knowledge, no background, no predictions, no speculation about intent or what may happen next.
- Keep confidence explicit. Each event has a "confidence" field. Never state a single-source report or a one-sided claim as fact: write "a single-source report says...", "Russia's MoD claims...", "Ukrainian sources report...". Only events marked corroborated may be stated plainly, and even then keep the wording close to the summary.
- Neutral, plain language. No adjectives that add drama. Keep numbers exactly as given.
- Every bullet and every theater line must cite the ids of the events it is based on, and only ids from the input.
- At most 6 overall bullets, most significant first (severity, corroboration, scale). At most one line per theater that had activity.
- If nothing significant happened, return a single bullet saying so, with the ids of the events it covers (or [] if there are none).

Reply with one JSON object and nothing else:
{"bullets": [{"text": "<one sentence>", "ids": ["<event id>", ...]}], "theaters": [{"id": "<theater id>", "text": "<one sentence>", "ids": ["<event id>", ...]}]}"""


def _facts(e: dict, theater_names: dict) -> dict:
    f = {"id": e["id"], "time": e.get("time"), "theater": e.get("theater"),
         "theater_name": theater_names.get(e.get("theater"), e.get("theater")),
         "type": "drone and missile alerts" if e.get("alert") else TYPES.get(e.get("type"), e.get("type")),
         "place": e.get("place"), "summary": e.get("summary"),
         "confidence": CONFIDENCE.get(e.get("status"), e.get("status")),
         "severity": e.get("severity"), "sources": e.get("sources_count")}
    if e.get("status") == "claimed":
        f["aligned_with"] = sorted({r["side"] for r in e.get("reports", []) if r.get("side")})
    if e.get("wave"):
        f["attack_wave"] = {"attacker": e.get("attacker"), "target_country": e.get("country"),
                            "launched": e.get("launched"), "intercepted": e.get("intercepted"),
                            "locations": len(e.get("targets") or [])}
    if e.get("alert"):
        f["alerts"] = e.get("alerts")
    t = e.get("transfer")
    if t:
        f["transfer"] = {k: t.get(k) for k in ("kind", "supplier", "recipient", "what", "flights", "mode")}
    return f


def window_events(events: list[dict], now) -> list[dict]:
    cutoff = now - timedelta(hours=WINDOW_HOURS)
    out = [e for e in events if (parse_time(e.get("time")) or cutoff) > cutoff]
    out.sort(key=lambda e: e.get("time") or "", reverse=True)       # newest first...
    out.sort(key=lambda e: -(e.get("severity") or 1))                 # ...within severity
    return out[:MAX_EVENTS]


def fingerprint(events: list[dict]) -> str:
    rows = sorted((e["id"], e.get("updated"), e.get("status"), e.get("summary"), e.get("severity"),
                   e.get("sources_count")) for e in events)
    return hashlib.sha1(json.dumps(rows, ensure_ascii=False).encode("utf-8")).hexdigest()


def _ids(v) -> list[str] | None:
    if not isinstance(v, list) or not all(isinstance(i, str) for i in v):
        return None
    return list(dict.fromkeys(v))


def validate(reply, events: list[dict]) -> dict | None:
    """Keep only bullets and theater lines whose cited ids all exist among the input events."""
    if not isinstance(reply, dict):
        return None
    by_id = {e["id"]: e for e in events}
    bullets, uncited = [], []
    for b in reply.get("bullets") or []:
        text = str(b.get("text") or "").strip() if isinstance(b, dict) else ""
        ids = _ids(b.get("ids")) if isinstance(b, dict) else None
        if not text or ids is None:
            continue
        if not ids:
            uncited.append({"text": text[:300], "ids": []})
        elif all(i in by_id for i in ids):
            bullets.append({"text": text[:300], "ids": ids[:10]})
    theaters, done = [], set()
    for t in reply.get("theaters") or []:
        if not isinstance(t, dict):
            continue
        tid, text, ids = t.get("id"), str(t.get("text") or "").strip(), _ids(t.get("ids"))
        if not text or not ids or tid in done or not all(i in by_id and by_id[i].get("theater") == tid for i in ids):
            continue
        done.add(tid)
        theaters.append({"id": tid, "text": text[:300], "ids": ids[:10]})
    bullets = bullets[:MAX_BULLETS]
    if not bullets and not theaters:
        # A single "nothing significant" line may stand without citations.
        return {"bullets": uncited[:1], "theaters": []} if len(uncited) == 1 else None
    return {"bullets": bullets, "theaters": theaters}


def update(state: dict, events: list[dict], theater_names: dict, settings: dict, now, ask, remaining: int) -> None:
    """Write a new brief into state["brief"] when it is due; otherwise leave the previous one."""
    prev = state.get("brief")
    tried = parse_time(state.get("brief_attempt"))
    if tried and now - tried < MIN_INTERVAL:
        return  # at most one attempt an hour, successful or not
    recent = window_events(events, now)
    fp = fingerprint(recent)
    if prev and state.get("brief_fp") == fp:
        return
    if not recent:
        new = {"bullets": [{"text": NOTHING, "ids": []}], "theaters": []}
    else:
        if remaining < int(settings.get("brief_min_calls", 5)):
            log(f"[brief] skipped: only {remaining} model calls left today")
            return
        state["brief_attempt"] = iso(now)
        payload = {"now": iso(now), "window_hours": WINDOW_HOURS,
                   "theaters": sorted({e["theater"] for e in recent}),
                   "events": [_facts(e, theater_names) for e in recent]}
        reply = ask(PROMPT, json.dumps(payload, ensure_ascii=False), state, settings, now, max_tokens=1500)
        new = validate(reply, recent)
        if new is None:
            log("[brief] no usable brief from the model; keeping the previous one")
            return
    state["brief_attempt"] = iso(now)
    state["brief"] = {"generated_at": iso(now), "window_hours": WINDOW_HOURS, **new}
    state["brief_fp"] = fp
    log(f"[brief] written: {len(new['bullets'])} bullets, {len(new['theaters'])} theater lines")
