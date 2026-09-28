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
from datetime import datetime

from common import UTC, clean_text, haversine_km, iso, log

# Active carriers as of late September 2026. CVN-79 (Kennedy) is not due to commission until 2027;
# add it here when it does. Nimitz shifted home port to Norfolk in July 2026 ahead of inactivation.
CARRIERS = {
    "CVN-68": ("U.S.S. Nimitz", "U.S.S. Nimitz"),
    "CVN-69": ("U.S.S. Dwight D. Eisenhower", "U.S.S. Eisenhower"),
    "CVN-70": ("U.S.S. Carl Vinson", "U.S.S. Vinson"),
    "CVN-71": ("U.S.S. Theodore Roosevelt", "U.S.S. Roosevelt"),
    "CVN-72": ("U.S.S. Abraham Lincoln", "U.S.S. Lincoln"),
    "CVN-73": ("U.S.S. George Washington", "U.S.S. Washington"),
    "CVN-74": ("U.S.S. John C. Stennis", "U.S.S. Stennis"),
    "CVN-75": ("U.S.S. Harry S. Truman", "U.S.S. Truman"),
    "CVN-76": ("U.S.S. Ronald Reagan", "U.S.S. Reagan"),
    "CVN-77": ("U.S.S. George H.W. Bush", "U.S.S. Bush"),
    "CVN-78": ("U.S.S. Gerald R. Ford", "U.S.S. Ford"),
}
NORFOLK = ("Norfolk, Va.", 36.95, -76.33)
SAN_DIEGO = ("San Diego (North Island)", 32.70, -117.19)
HOME = {
    "CVN-68": NORFOLK,
    "CVN-69": NORFOLK,
    "CVN-70": SAN_DIEGO,
    "CVN-71": SAN_DIEGO,
    "CVN-72": SAN_DIEGO,
    "CVN-73": ("Yokosuka, Japan", 35.29, 139.67),
    "CVN-74": ("Newport News shipyard (refueling overhaul)", 36.98, -76.43),
    "CVN-75": NORFOLK,
    "CVN-76": ("Bremerton, Wash.", 47.56, -122.63),
    "CVN-77": NORFOLK,
    "CVN-78": NORFOLK,
}
FLEET_TRACKER_FEED = "https://news.usni.org/category/fleet-tracker/feed"
MOVE_KM = 150          # smaller changes are treated as the same position
TRACK_LEN = 12

TRACKER_PROMPT = """You read a USNI News Fleet and Marine Tracker article and list every US Navy aircraft carrier (hull CVN-##) it mentions, with its current location. Reply with one JSON object and nothing else:
{"as_of": "YYYY-MM-DD", "carriers": [{"hull": "CVN-78", "deployed": <true if on a deployment, including one returning home>, "status": "underway" | "operating" | "in port" | "in maintenance", "place": "<sea area or port, as stated>", "lat": <number>, "lon": <number>, "heading_to": {"place": "...", "lat": <number>, "lon": <number>} or null}]}
Rules: use only what the article says. "underway" = at sea in transit; "operating" = on station in a named area; "in port" = pierside; "in maintenance" = in a shipyard or major maintenance. Give your best coordinate estimate for each named place (a sea area's center is fine). heading_to only if the article states a destination; a carrier "returning from deployment" is heading to its home port if the article names it."""


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
        if r["status"] != "home":
            c["at_home"] = False
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
    html = re.sub(r"(?is)<(head|script|style|nav|header|footer|aside|form)[^>]*>.*?</\1>", " ", html)
    text = clean_text(html)
    low = text.lower()
    start = low.find("these are the approximate positions")
    if start < 0:
        start = max(0, low.find("fleet and marine tracker"))
    end = low.find("in addition to these major formations", start)
    return text[start:(end if end > 0 else start + 24000)][:24000]


def _latest_edition(items: list[dict], session) -> dict | None:
    """Newest Fleet and Marine Tracker: from USNI's Fleet Tracker category feed, else the main feed."""
    try:
        import feedparser
        from datetime import datetime as _dt
        r = session.get(FLEET_TRACKER_FEED, timeout=25)
        r.raise_for_status()
        best = None
        for e in feedparser.parse(r.content).entries[:10]:
            title = e.get("title", "")
            st = e.get("published_parsed") or e.get("updated_parsed")
            if "tracker" not in title.lower() or not st or not e.get("link"):
                continue
            t = iso(_dt(*st[:6], tzinfo=UTC))
            if best is None or t > best["time"]:
                best = {"url": e["link"], "time": t, "title": title}
        if best:
            return best
    except Exception as exc:  # noqa: BLE001
        log(f"[fleet] tracker feed: {exc}")
    editions = [it for it in items if it["platform"] == "rss" and "fleet and marine tracker" in it["text"][:160].lower()]
    if not editions:
        return None
    it = max(editions, key=lambda x: x["time"])
    return {"url": it["url"], "time": it["time"], "title": it["text"][:120]}


def read_weekly_tracker(state: dict, items: list[dict], session, settings: dict, now: datetime, ask_json) -> list[dict]:
    """If USNI published a new Fleet and Marine Tracker, turn it into position reports (one model call)."""
    meta = state.setdefault("fleet_meta", {})
    latest = _latest_edition(items, session)
    if not latest:
        return []
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
    meta["tracker_hulls"] = sorted({h for h in (_hull(c.get("hull")) for c in out["carriers"] if isinstance(c, dict)) if h})
    status_map = {"deployed": "operating", "in maintenance": "in port"}  # older model replies may still say "deployed"
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
            "deployed": bool(c.get("deployed", raw_status == "deployed")), "maintenance": raw_status == "in maintenance",
            "place": c.get("place"), "lat": lat, "lon": lon, "heading_to": heading,
            "time": latest["time"], "source": "USNI News Fleet and Marine Tracker", "url": latest["url"],
        })
    log(f"[fleet] weekly tracker: {len(reports)} carriers")
    return reports


def apply_home_baseline(state: dict) -> None:
    """Carriers the latest weekly tracker does not list as deployed are shown at home port.

    USNI's tracker lists every deployed carrier strike group, so absence means the carrier is
    in home waters (in port, in maintenance, or training locally). A carrier reported somewhere
    by news more recently than the tracker keeps that newer position.
    """
    fleet = state.setdefault("fleet", {})
    meta = state.get("fleet_meta") or {}
    listed = set(meta.get("tracker_hulls") or [])
    tracker_time = meta.get("tracker_time")
    reports = []
    for hull, (place, lat, lon) in HOME.items():
        c = fleet.get(hull)
        if hull in listed:
            continue
        if c and c.get("lat") is not None:
            newer_news = tracker_time and (c.get("as_of") or "") > tracker_time and not c.get("at_home")
            at_home = haversine_km(c["lat"], c["lon"], lat, lon) < MOVE_KM
            if newer_news or at_home or not tracker_time:
                if at_home:
                    c["at_home"] = True
                continue
        reports.append({"hull": hull, "status": "home", "place": place, "lat": lat, "lon": lon, "heading_to": None,
                        "time": tracker_time or "1970-01-01T00:00:00Z",
                        "source": "Home port (not listed as deployed in USNI's latest Fleet Tracker)" if tracker_time
                        else "Home port (no position reports yet)",
                        "url": meta.get("tracker_url")})
    update(state, reports)
    for r in reports:
        c = fleet.get(r["hull"])
        if c:
            c["at_home"] = True
            c["deployed"] = False
    for hull in listed:
        if hull in fleet:
            fleet[hull]["at_home"] = False


def public(state: dict, now: datetime) -> list[dict]:
    out = []
    for c in (state.get("fleet") or {}).values():
        if c.get("lat") is None or c.get("hull") not in CARRIERS:
            continue
        row = {k: c.get(k) for k in ("hull", "name", "short", "lat", "lon", "place", "status", "as_of",
                                     "source", "url", "heading_to", "prev", "moved_at", "departed_at", "track",
                                     "deployed", "maintenance", "at_home")}
        row["name"], row["short"] = CARRIERS[c["hull"]]  # names always from the list above
        out.append(row)
    return sorted(out, key=lambda c: c["hull"])


def mark_deployment(state: dict, reports: list[dict]) -> None:
    """Remember whether the weekly tracker last listed each carrier as deployed or in maintenance."""
    fleet = state.setdefault("fleet", {})
    for r in reports:
        hull = _hull(r.get("hull"))
        if hull in fleet and ("deployed" in r or "maintenance" in r):
            fleet[hull]["deployed"] = bool(r.get("deployed"))
            fleet[hull]["maintenance"] = bool(r.get("maintenance"))
