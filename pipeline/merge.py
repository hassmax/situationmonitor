"""Merge reports of the same incident and decide how confident the map should look.

Confidence rules (what the colors on the globe mean):
  corroborated  two or more independent sources, and at least one of them is not aligned
                with a party to the conflict (or sources from opposing sides agree)
  unconfirmed   a single unaligned source so far
  claimed       only sources aligned with one side (e.g. a ministry and friendly bloggers)
Nearby news coverage picked up by GDELT (3+ distinct outlets) counts as one unaligned source.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from common import haversine_km, iso, parse_time, short_hash
from sources.gdelt import CellIndex, news_domains_near

FAMILY = {
    "airstrike": "strike", "missile_drone": "strike", "air_defense": "strike", "explosion": "strike",
    "artillery": "ground", "ground": "ground", "territory": "ground",
    "naval": "naval", "deployment": "deployment", "ceasefire": "ceasefire",
}
RADIUS_KM = {"strike": 30, "ground": 30, "naval": 150, "deployment": 120, "ceasefire": 400}
WINDOW = timedelta(hours=12)


def _find_match(events: list[dict], cand: dict) -> dict | None:
    fam = FAMILY.get(cand["type"], "strike")
    ct = parse_time(cand["time"])
    best, best_d = None, float("inf")
    for e in events:
        if e["theater"] != cand["theater"] or FAMILY.get(e["type"], "strike") != fam:
            continue
        et, eu = parse_time(e["time"]), parse_time(e["updated"])
        if abs(ct - et) > WINDOW and abs(ct - eu) > WINDOW:
            continue
        d = haversine_km(e["lat"], e["lon"], cand["lat"], cand["lon"])
        radius = RADIUS_KM[fam] * (2 if (e.get("approx") or cand["approx"]) else 1)
        if d <= radius and d < best_d:
            best, best_d = e, d
    return best


def merge(events: list[dict], candidates: list[dict]) -> list[dict]:
    for cand in sorted(candidates, key=lambda c: c["time"]):
        rep = cand["report"]
        match = _find_match(events, cand)
        if match is None:
            events.append({
                "id": short_hash("event", rep["url"]),
                "theater": cand["theater"], "type": cand["type"], "summary": cand["summary"],
                "place": cand["place"], "country": cand["country"],
                "lat": cand["lat"], "lon": cand["lon"], "approx": cand["approx"], "origin": cand["origin"],
                "severity": cand["severity"], "us": cand["us"], "cn": cand["cn"],
                "killed": cand["killed"], "injured": cand["injured"],
                "time": cand["time"], "updated": cand["time"], "reports": [rep],
            })
            continue
        if any(r["url"] == rep["url"] for r in match["reports"]):
            continue
        match["reports"].append(rep)
        match["time"] = min(match["time"], cand["time"])
        match["updated"] = max(match["updated"], cand["time"])
        match["severity"] = max(match["severity"], cand["severity"])
        match["us"] = match["us"] or cand["us"]
        match["cn"] = match["cn"] or cand["cn"]
        for k in ("killed", "injured"):
            vals = [v for v in (match.get(k), cand[k]) if v is not None]
            match[k] = max(vals) if vals else None
        if match.get("approx") and not cand["approx"]:
            match.update(lat=cand["lat"], lon=cand["lon"], place=cand["place"], approx=False)
        if not match.get("origin") and cand["origin"]:
            match["origin"] = cand["origin"]
    return events


def _headline(event: dict) -> dict:
    """Prefer an unaligned source, then higher weight, then the earliest report."""
    return sorted(
        event["reports"],
        key=lambda r: (r.get("side") is not None, -int(r.get("weight", 1)), r["time"]),
    )[0]


def apply_status(events: list[dict], cells: list[dict]) -> None:
    index = CellIndex(cells)
    for e in events:
        neutral = {r["group"] for r in e["reports"] if not r.get("side")}
        sided = {r["group"] for r in e["reports"] if r.get("side")}
        sides = {r["side"] for r in e["reports"] if r.get("side")}
        news = set()
        if FAMILY.get(e["type"]) in ("strike", "ground"):
            news = news_domains_near(index, e)
        e["news_nearby"] = len(news)
        if len(news) >= 3:
            neutral.add("gdelt")
        groups = neutral | sided
        if len(groups) >= 2 and (neutral or len(sides) >= 2):
            e["status"] = "corroborated"
        elif neutral:
            e["status"] = "unconfirmed"
        else:
            e["status"] = "claimed"
        e["sources_count"] = len(groups)
        head = _headline(e)
        e["summary"] = head["summary"]


def prune(events: list[dict], now: datetime, retention_days: int, max_events: int) -> list[dict]:
    cutoff = iso(now - timedelta(days=retention_days))
    kept = [e for e in events if e["updated"] >= cutoff]
    kept.sort(key=lambda e: e["updated"], reverse=True)
    return kept[:max_events]


def public_event(e: dict) -> dict:
    """Strip internal fields before publishing."""
    out = {k: v for k, v in e.items() if k != "reports"}
    out["reports"] = [
        {k: r.get(k) for k in ("source", "platform", "kind", "side", "claim", "url", "time", "summary")}
        for r in sorted(e["reports"], key=lambda r: r["time"])
    ]
    return out
