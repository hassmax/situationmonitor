"""Tension needles: one reading per region, how tense the last 24 hours were against that region's
own last 30 days (the owner, 2026-11-17: "Add a few NYT-like needles for tension in each region. It
goes up or goes down based on threats, activities and escalation").

No model calls. A region's score for a 24-hour window is the sum of its events' points:

  points = kind weight x severity weight x confidence weight

Attacks and fighting count most; buildups, incursions, weapons tests and threats less; ceasefires,
talks, signed deals and reported pullbacks of forces count against (they bring the needle down).
Diplomacy is read from the event's own summary (the model's words): calming words (ceasefire,
truce, peace talks or deal, de-escalation, prisoner exchange) count against unless the summary
says the deal failed or was rejected; hostile words (expels, sanctions, threatens, ultimatum)
count for. A pullback counts against only when the summary names forces (troops, ships, jets) and reports
it happening, not a demand, call or plan for one.
Plain meetings and talks count for nothing either way.

The reading is where that score falls among the same 24-hour windows over the 30 days before
(a percentile: 0.5 is a usual day for that region), then nudged by the regional analyst's current
judgments (escalating up, de-escalating down). The level words mark bands of that reading, so
"Severe" means busier than nearly every day of the region's last month, not a danger rating
comparable across regions.

The 30 days come from the archive (archive/<day>.json on the data branch) plus the published
events. A region needs MIN_DAYS days of archive to get a needle; until then it is "learning".
"""
from __future__ import annotations

import json
import re
from datetime import timedelta
from pathlib import Path

from common import iso, parse_time

WINDOW_H = 24
BASELINE_DAYS = 30
MIN_DAYS = 10
ANALYST_STEP = 0.06     # one escalating or de-escalating judgment moves the reading this much
ANALYST_CAP = 0.12      # all of them together at most this much
ANALYST_MAX_AGE_H = 12  # an older analysis is not used
REASONS = 5

# what happened: attacks and fighting 1, posture and threats less, calming steps negative
KIND_W = {
    "airstrike": 1.0, "missile_drone": 1.0, "artillery": 1.0, "ground": 1.0, "territory": 1.0,
    "explosion": 0.8, "naval": 1.0, "hybrid": 0.9, "air_defense": 0.7,
    "incursion": 0.8, "weapons_test": 0.6, "deployment": 0.5,
    "legal": 0.4, "arms_transfer": 0.3, "production": 0.1,
}
HOSTILE_DIPLO_W = 0.6
CALMING_W = -1.0
SEV_W = {1: 1.0, 2: 2.5, 3: 6.0}
CONF_W = {"corroborated": 1.0, "unconfirmed": 0.75, "claimed": 0.5}
OLD_W = 0.3  # possibly an old story

# (lowest reading, word): a usual day sits around 0.5, "Guarded"
LEVELS = [(0.0, "Low"), (0.25, "Guarded"), (0.6, "Elevated"), (0.85, "High"), (0.97, "Severe")]

CALM_RE = re.compile(r"\b(cease-?fires?|truces?|armistice|peace (?:talks?|deal|agreement|plan|process|summit|accord|efforts?|push|negotiations?)|"
                     r"de-?escalat\w*|prisoner (?:exchange|swap)s?|humanitarian (?:pause|corridor)s?)\b", re.I)
FAILED_RE = re.compile(r"\b(violat\w*|breach\w*|collaps\w*|broke|broken|breaks?|reject\w*|walk(?:s|ed)? out|"
                       r"suspend\w*|fail\w*|stall\w*|end(?:s|ed)? (?:the |a )?(?:cease-?fire|truce)|abandon\w*|"
                       r"withdraw\w* from (?:[\w-]+ ){0,4}(?:treaty|agreement|deal|accord|talks))\b", re.I)
HOSTILE_RE = re.compile(r"\b(expel\w*|expulsion|sanction\w*|sever\w*|recall\w* (?:its |the )?ambassador|ultimatum|"
                        r"threat\w*|warn\w*|mobili[sz]\w*|declares? war|blockade)\b", re.I)
PULLBACK_RE = re.compile(r"\b(withdraw\w*|pull(?:s|ed|ing)? (?:back|out)|pullback|pull-out|pullout|redeploy\w* (?:home|back)|"
                         r"demobili[sz]\w*|stand(?:s|ing)? down)\b", re.I)
FORCES_RE = re.compile(r"\b(troops?|forces|soldiers|units?|brigades?|battalions?|divisions?|warships?|ships|vessels|fleet|carriers?|"
                       r"tanks|armou?r|military|army|navy|aircraft|jets|batteries|missiles|personnel)\b", re.I)
DEMAND_RE = re.compile(r"\b(call(?:s|ed|ing)? (?:on|for)|demand\w*|urg\w*|plan\w*|potential|propos\w*|would|could|may|threaten\w*)\b", re.I)
TREATY_RE = re.compile(r"(?:withdraw\w*|pull(?:s|ed|ing)? out) (?:from|of) (?:[\w-]+ ){0,4}(?:treaty|agreement|deal|accord|talks|pact|alliance|convention)", re.I)


def kind(e: dict) -> tuple[float, str]:
    """(kind weight, word for the reasons) for one event."""
    t = e.get("type") or ""
    text = str(e.get("summary") or "")
    # a reported pullback of forces calms (not a battlefield retreat: territory and ground keep their weight)
    if (t in ("deployment", "incursion", "diplomacy") and PULLBACK_RE.search(text) and FORCES_RE.search(text)
            and not TREATY_RE.search(text) and not DEMAND_RE.search(text)):
        return CALMING_W, "pullback"
    if t == "diplomacy":
        if HOSTILE_RE.search(text) or TREATY_RE.search(text):
            return HOSTILE_DIPLO_W, "hostile step"
        if CALM_RE.search(text) and not FAILED_RE.search(text):
            return CALMING_W, "calming step"
        if CALM_RE.search(text):
            return HOSTILE_DIPLO_W, "failed deal"
        return 0.0, ""
    w = KIND_W.get(t, 0.0)
    return w, t.replace("_", " ") if w else ""


def points(e: dict) -> float:
    w, _ = kind(e)
    if not w:
        return 0.0
    p = w * SEV_W.get(int(e.get("severity") or 1), 1.0) * CONF_W.get(e.get("status"), 0.5)
    return p * (OLD_W if e.get("possibly_old") else 1.0)


def _archived(root: Path, now, days: int) -> tuple[list[dict], set[str]]:
    """Archived events from the last `days` days, and the days that have an archive file."""
    folder, out, have = Path(root) / "archive", [], set()
    for k in range(days + 2):
        day = (now - timedelta(days=k)).strftime("%Y-%m-%d")
        try:
            out += json.loads((folder / f"{day}.json").read_text(encoding="utf-8")).get("events", [])
            have.add(day)
        except (OSError, ValueError):
            continue
    return out, have


def _window(events: list[tuple[float, dict]], end: float) -> float:
    start = end - WINDOW_H * 3600
    return sum(p for t, (p, e) in events if start < t <= end) if events else 0.0


def percentile(x: float, base: list[float]) -> float:
    if not base:
        return 0.5
    below = sum(1 for b in base if b < x - 1e-9)
    same = sum(1 for b in base if abs(b - x) <= 1e-9)
    return (below + 0.5 * same) / len(base)


def level(reading: float) -> str:
    word = LEVELS[0][1]
    for floor, w in LEVELS:
        if reading >= floor:
            word = w
    return word


def _analyst(state: dict, now) -> dict[str, list[dict]]:
    a = state.get("analysis") or {}
    at = parse_time(a.get("generated_at"))
    if not at or now - at > timedelta(hours=ANALYST_MAX_AGE_H):
        return {}
    out: dict[str, list[dict]] = {}
    for r in a.get("regions") or []:
        rid = r.get("theater") or r.get("region")
        for j in r.get("judgments") or []:
            step = {"escalating": ANALYST_STEP, "de-escalating": -ANALYST_STEP}.get(j.get("trend"))
            if step:
                out.setdefault(rid, []).append({"headline": j.get("headline", ""), "trend": j["trend"], "step": step,
                                                "ids": list(j.get("ids") or [])[:6]})
    return out


def compute(published: list[dict], archived: list[dict], have_days: set[str], theaters: list[dict], state: dict, now) -> dict:
    """The public tension block. `archived` and `have_days` from the archive (see _archived)."""
    end = now.timestamp()
    by_id = {e.get("id"): e for e in archived if e.get("id")}
    by_id.update({e["id"]: e for e in published})  # the published version wins
    per: dict[str, list[tuple[float, tuple[float, dict]]]] = {}
    for e in by_id.values():
        t = parse_time(e.get("time") or e.get("updated"))
        if not t or e.get("_archived"):
            continue
        p = points(e)
        if p:
            per.setdefault(e.get("theater"), []).append((t.timestamp(), (p, e)))
    # baseline days: whole 24-hour windows the archive covers (its file for that window's last day exists)
    covered = [k for k in range(1, BASELINE_DAYS + 1) if (now - timedelta(days=k)).strftime("%Y-%m-%d") in have_days]
    judged = _analyst(state, now)
    regions = []
    for th in theaters:
        if th.get("listed") is False:
            continue
        rid = th["id"]
        evs = per.get(rid, [])
        score = _window(evs, end)
        base = [_window(evs, end - k * 86400) for k in covered]
        row = {"theater": rid, "name": th.get("name", rid), "days": len(base)}
        if len(base) < MIN_DAYS:
            regions.append({**row, "level": None})
            continue
        raw = percentile(score, base)
        prev_base = [_window(evs, end - k * 86400) for k in covered if k > 1]
        prev = percentile(_window(evs, end - 86400), prev_base) if len(prev_base) >= MIN_DAYS - 1 else None
        nudges = judged.get(rid, [])
        nudge = max(-ANALYST_CAP, min(ANALYST_CAP, sum(j["step"] for j in nudges)))
        reading = max(0.0, min(1.0, raw + nudge))
        recent = [pe for t, pe in evs if end - WINDOW_H * 3600 < t <= end]
        recent.sort(key=lambda pe: -abs(pe[0]))
        reasons = []
        for p, e in recent[:REASONS]:
            _, word = kind(e)
            reasons.append({"id": e["id"], "points": round(p, 1), "why": word})
        usual = sorted(base)[len(base) // 2]
        regions.append({**row, "level": level(reading), "reading": round(reading, 3), "raw": round(raw, 3), "prev": None if prev is None else round(prev, 3),
                        "score": round(score, 1), "usual": round(usual, 1), "events": len(recent),
                        "reasons": reasons, "analyst": [{k: j[k] for k in ("headline", "trend", "ids")} for j in nudges]})
    return {"computed_at": iso(now), "window_hours": WINDOW_H, "baseline_days": BASELINE_DAYS,
            "levels": [{"from": f, "word": w} for f, w in LEVELS], "regions": regions}


def update(root: Path, published: list[dict], theaters: list[dict], state: dict, now) -> dict:
    archived, have = _archived(root, now, BASELINE_DAYS + 1)
    return compute(published, archived, have, theaters, state, now)
