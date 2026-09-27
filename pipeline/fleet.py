"""Track US Navy aircraft carriers.

Two inputs:
  1. USNI News' weekly Fleet and Marine Tracker. When a new edition appears in the USNI
     feed, the article is read once and the model lists every carrier's location.
  2. Carrier mentions in ordinary posts and news (departures, arrivals, transits), picked
     up during normal extraction.

Each carrier keeps its latest reported position, where it was before (so the map can
animate the move), a stated destination if any, and a short track of past positions.
Positions are never extrapolated: the map shows the last report and its date.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from common import clean_text, haversine_km, iso, log

CARRIERS = {
    "CVN-68": ("USS Nimitz", "Nimitz"),
    "CVN-69": ("USS Dwight D. Eisenhower", "Eisenhower"),
    "CVN-70": ("USS Carl Vinson", "Vinson"),
    "CVN-71": ("USS Theodore Roosevelt", "Roosevelt"),
    "CVN-72": ("USS Abraham Lincoln", "Lincoln"),
    "CVN-73": ("USS George Washington", "Washington"),
    "CVN-74": ("USS John C. Stennis", "Stennis"),
    "CVN-75": ("USS Harry S. Truman", "Truman"),
    "CVN-76": ("USS Ronald Reagan", "Reagan"),
    "CVN-77": ("USS George H.W. Bush", "Bush"),
    "CVN-78": ("USS Gerald R. Ford", "Ford"),
    "CVN-79": ("USS John F. Kennedy", "Kennedy"),
}
MOVE_KM = 150          # smaller changes are treated as the same position
TRACK_LEN = 12
SHOW_DAYS = 45         # hide carriers with no report for this long

TRACKER_PROMPT = """You read a USNI News Fleet and Marine Tracker article and list every US Navy aircraft carrier (hull CVN-##) it mentions, with its current location. Reply with one JSON object and nothing else:
{"as_of": "YYYY-MM-DD", "carriers": [{"hull": "CVN-78", "status": "deployed" | "underway" | "operating" | "in port" | "in maintenance", "place": "<sea area or port, as stated>", "lat": <number>, "lon": <number>, "heading_to": {"place": "...", "lat": <number>, "lon": <number>} or null}]}
Rules: use only what the article says. "deployed" = on a deployment away from home waters; "underway" = at sea for training or transit; "operating" = on station in a named area; "in port" = pierside; "in maintenance" = in a shipyard or major maintenance. Give your best coordinate estimate for each named place (a sea area's center is fine). heading_to only if the article states a destination."""


def _hull(v) -> str | None:
    m = re.search(r"(\d{2})", str(v or ""))
    hull = f"CVN-{m.group(1)}" if m else None
    return hull if hull in CARRIERS else None


def update(state: dict, reports: list[dict]) -> int:
    """Apply position reports (oldest first). Returns how many carriers changed."""
    fleet = state.setdefault("fleet", {})
    changed = 0
    for r in sorted(reports, key=lambda x: x["time"]):
        hull = _hull(r.get("hull"))
        if not hull:
            continue
        name, short = CARRIERS[hull]
        c = fleet.setdefault(hull, {"hull": hull, "name": name, "short": short, "track": []})
        if c.get("as_of") and r["time"] < c["as_of"]:
            continue  # older than what we already know
        moved = c.get("lat") is not None and haversine_km(c["lat"], c["lon"], r["lat"], r["lon"]) > MOVE_KM
        if moved:
            c["prev"] = {"lat": c["lat"], "lon": c["lon"], "place": c.get("place"), "as_of": c.get("as_of")}
            c["moved_at"] = r["time"]
        heading = r.get("heading_to")
        if heading is None and r["status"] in ("departed", "underway") and c.get("heading_to"):
            heading = c["heading_to"]  # a transit update without a destination keeps the old one
        if heading and haversine_km(heading["lat"], heading["lon"], r["lat"], r["lon"]) < MOVE_KM:
            heading = None  # arrived
        c.update(lat=r["lat"], lon=r["lon"], place=r.get("place"), status=r["status"], as_of=r["time"],
                 source=r.get("source"), url=r.get("url"), heading_to=heading)
        if r["status"] == "departed":
            c["departed_at"] = r["time"]
        track = c["track"]
        if not track or haversine_km(track[-1]["lat"], track[-1]["lon"], r["lat"], r["lon"]) > 50:
            track.append({"lat": r["lat"], "lon": r["lon"], "place": r.get("place"), "time": r["time"]})
        c["track"] = track[-TRACK_LEN:]
        changed += 1
    return changed


def _article_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|nav|header|footer|aside)[^>]*>.*?</\1>", " ", html)
    text = clean_text(html)
    start = text.lower().find("fleet and marine tracker")
    return text[start if start >= 0 else 0:][:14000]


def read_weekly_tracker(state: dict, items: list[dict], session, settings: dict, now: datetime, ask_json) -> list[dict]:
    """If USNI published a new Fleet and Marine Tracker, turn it into position reports (one model call)."""
    meta = state.setdefault("fleet_meta", {})
    editions = [it for it in items if it["platform"] == "rss" and "fleet and marine tracker" in it["text"][:160].lower()]
    if not editions:
        return []
    latest = max(editions, key=lambda it: it["time"])
    if latest["url"] == meta.get("tracker_url"):
        return []
    try:
        r = session.get(latest["url"], timeout=30)
        r.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        log(f"[fleet] could not open the tracker article: {exc}")
        return []
    out = ask_json(TRACKER_PROMPT, _article_text(r.text), state, settings, now)
    if not isinstance(out, dict) or not isinstance(out.get("carriers"), list):
        log("[fleet] tracker article could not be read this run; will retry")
        return []
    meta["tracker_url"] = latest["url"]
    meta["tracker_time"] = latest["time"]
    status_map = {"deployed": "operating", "in maintenance": "in port"}
    reports = []
    for c in out["carriers"]:
        if not isinstance(c, dict):
            continue
        try:
            lat, lon = float(c["lat"]), float(c["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        heading = c.get("heading_to") if isinstance(c.get("heading_to"), dict) else None
        if heading:
            try:
                heading = {"place": heading.get("place"), "lat": float(heading["lat"]), "lon": float(heading["lon"])}
            except (KeyError, TypeError, ValueError):
                heading = None
        raw_status = c.get("status", "operating")
        reports.append({
            "hull": c.get("hull"), "status": status_map.get(raw_status, raw_status),
            "deployed": raw_status == "deployed", "maintenance": raw_status == "in maintenance",
            "place": c.get("place"), "lat": lat, "lon": lon, "heading_to": heading,
            "time": latest["time"], "source": "USNI News Fleet and Marine Tracker", "url": latest["url"],
        })
    log(f"[fleet] weekly tracker: {len(reports)} carriers")
    return reports


def public(state: dict, now: datetime) -> list[dict]:
    cutoff = iso(now - timedelta(days=SHOW_DAYS))
    out = []
    for c in (state.get("fleet") or {}).values():
        if c.get("lat") is None or (c.get("as_of") or "") < cutoff:
            continue
        out.append({k: c.get(k) for k in ("hull", "name", "short", "lat", "lon", "place", "status", "as_of",
                                          "source", "url", "heading_to", "prev", "moved_at", "departed_at", "track",
                                          "deployed", "maintenance")})
    return sorted(out, key=lambda c: c["hull"])


def mark_deployment(state: dict, reports: list[dict]) -> None:
    """Remember whether the weekly tracker last listed each carrier as deployed or in maintenance."""
    fleet = state.setdefault("fleet", {})
    for r in reports:
        hull = _hull(r.get("hull"))
        if hull in fleet and ("deployed" in r or "maintenance" in r):
            fleet[hull]["deployed"] = bool(r.get("deployed"))
            fleet[hull]["maintenance"] = bool(r.get("maintenance"))
