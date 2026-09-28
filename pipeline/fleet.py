"""Track US Navy aircraft carriers.

Two inputs:
  1. USNI News' weekly Fleet and Marine Tracker. When a new edition appears in the USNI
     feed, the article is read once and the model lists every carrier's location.
  2. Carrier mentions in ordinary posts and news (departures, arrivals, transits), picked
     up during normal extraction.

Each carrier keeps its latest reported position, where it was before (so the map can
animate the move), a stated destination if any, and a short track of past positions.
Positions are never extrapolated: the map shows the last report and its date.

News reports are checked before they move a carrier (the weekly tracker is trusted):
  - a vague place ("Middle East", "the Pacific") is not a position; it is ignored
  - a carrier reported at another carrier's home port (Ford "departing San Diego") is almost
    always a report about the other carrier; it is ignored
  - a move faster than a carrier can sail since its last report is held until a second,
    different report puts it in the same area (one misread article can't teleport it)
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

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
USNI_FEED = "https://news.usni.org/feed"
MOVE_KM = 150          # smaller changes are treated as the same position
TRACK_LEN = 12
FLEET_VERSION = 2      # bump to re-check stored positions against the rules below
MAX_KM_PER_DAY = 1300  # about 30 knots, flat out
SLACK_KM = 400         # rough coordinates for sea areas and ports
HOME_KM = 60           # "at" a home port
CONFIRM_KM = 500       # a second report this close confirms a held move
HOLD = timedelta(hours=72)
TRACKER_MAX_AGE = timedelta(days=14)  # older editions can't say who is home
VAGUE = {"middle east", "the middle east", "indo-pacific", "the indo-pacific", "pacific", "the pacific",
         "pacific ocean", "atlantic", "the atlantic", "atlantic ocean", "europe", "asia", "africa", "at sea",
         "overseas", "the region", "region", "gulf region", "central command", "centcom", "5th fleet",
         "6th fleet", "7th fleet", "2nd fleet", "3rd fleet", "unknown", "undisclosed", "deployment"}

TRACKER_PROMPT = """You read a USNI News Fleet and Marine Tracker article and list every US Navy aircraft carrier (hull CVN-##) it mentions, with its current location. Reply with one JSON object and nothing else:
{"as_of": "YYYY-MM-DD", "carriers": [{"hull": "CVN-78", "deployed": <true if on a deployment, including one returning home>, "status": "underway" | "operating" | "in port" | "in maintenance", "place": "<sea area or port, as stated>", "lat": <number>, "lon": <number>, "heading_to": {"place": "...", "lat": <number>, "lon": <number>} or null}]}
Rules: use only what the article says. "underway" = at sea in transit; "operating" = on station in a named area; "in port" = pierside; "in maintenance" = in a shipyard or major maintenance. Give your best coordinate estimate for each named place (a sea area's center is fine). heading_to only if the article states a destination; a carrier "returning from deployment" is heading to its home port if the article names it."""


def _hull(v) -> str | None:
    m = re.search(r"(\d{2})", str(v or ""))
    hull = f"CVN-{m.group(1)}" if m else None
    return hull if hull in CARRIERS else None


def _vague(place) -> bool:
    p = re.sub(r"\s*\(.*?\)", "", str(place or "")).strip().lower()
    return not p or p in VAGUE


def _known(c: dict) -> bool:
    return c.get("lat") is not None and not str(c.get("as_of") or "1970").startswith("1970")


def _days(a: str, b: str) -> float:
    ta, tb = datetime.fromisoformat(a.replace("Z", "+00:00")), datetime.fromisoformat(b.replace("Z", "+00:00"))
    return abs((tb - ta).total_seconds()) / 86400


def _too_fast(a: dict, b: dict) -> bool:
    """Could a carrier have sailed from a (lat, lon, as_of/time) to b in the time between?"""
    ta, tb = a.get("as_of") or a.get("time"), b.get("as_of") or b.get("time")
    if not ta or not tb or str(ta).startswith("1970"):
        return False
    return haversine_km(a["lat"], a["lon"], b["lat"], b["lon"]) > MAX_KM_PER_DAY * _days(ta, tb) + SLACK_KM


def _other_home(hull: str, lat: float, lon: float) -> str | None:
    """The home port this spot belongs to, if it is another carrier's and not this one's."""
    own = HOME.get(hull)
    if own and haversine_km(own[1], own[2], lat, lon) < HOME_KM:
        return None
    for place, hlat, hlon in set(HOME.values()):
        if haversine_km(hlat, hlon, lat, lon) < HOME_KM:
            return place
    return None


def _check(c: dict, r: dict, hull: str) -> str | None:
    """Why a news report can't move this carrier now (None: it can)."""
    if _vague(r.get("place")):
        return f"'{r.get('place')}' is not a position"
    port = _other_home(hull, r["lat"], r["lon"])
    if port and not (c.get("lat") is not None and haversine_km(c["lat"], c["lon"], r["lat"], r["lon"]) < MOVE_KM):
        return f"{port} is another carrier's home port; the report is probably about that carrier"
    if _known(c) and _too_fast(c, r):
        held = [h for h in c.get("held", []) if _days(h["time"], r["time"]) * 86400 <= HOLD.total_seconds()]
        if any(h["url"] != r.get("url") and haversine_km(h["lat"], h["lon"], r["lat"], r["lon"]) < CONFIRM_KM
               for h in held):
            c.pop("held", None)
            return None  # a second report confirms the move
        held.append({"lat": r["lat"], "lon": r["lon"], "place": r.get("place"), "time": r["time"], "url": r.get("url")})
        c["held"] = held[-5:]
        return "hold"
    return None


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
        if not r.get("trusted"):
            why = _check(c, r, hull)
            if why == "hold":
                log(f"[fleet] {name}: {r.get('place')} is too far to have sailed since {c.get('place')}; "
                    "waiting for a second report")
                continue
            if why:
                log(f"[fleet] {name}: report ignored ({why})")
                continue
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
    """Newest Fleet and Marine Tracker from USNI's feeds (the Fleet Tracker category, then the main
    feed), with the article text the feed carries; else a tracker headline seen in the news."""
    import feedparser
    best = None
    for feed_url in (FLEET_TRACKER_FEED, USNI_FEED):
        try:
            # the query string asks caches along the way for a fresh copy
            r = session.get(f"{feed_url}?t={int(datetime.now().timestamp()) // 900}", timeout=25)
            r.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            log(f"[fleet] tracker feed {feed_url}: {exc}")
            continue
        for e in feedparser.parse(r.content).entries[:20]:
            title = e.get("title", "")
            st = e.get("published_parsed") or e.get("updated_parsed")
            if "tracker" not in title.lower() or not st or not e.get("link"):
                continue
            t = iso(datetime(*st[:6], tzinfo=UTC))
            if best is None or t > best["time"]:
                content = " ".join(c.get("value", "") for c in e.get("content") or [])
                best = {"url": e["link"], "time": t, "title": title, "html": content}
    if best:
        return best
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
    # USNI refuses GitHub's servers (HTTP 403) for article pages, so the text the feed carries is
    # used first; the page is opened only when the feed has none.
    html = latest.get("html") or ""
    if len(clean_text(html)) < 1500:
        try:
            r = session.get(latest["url"], timeout=30)
            r.raise_for_status()
            html = r.text
        except Exception as exc:  # noqa: BLE001
            log(f"[fleet] could not open the tracker article ({latest['time'][:10]}) and the feed has no text: {exc}")
            return []
    log(f"[fleet] reading the Fleet and Marine Tracker of {latest['time'][:10]}")
    out = ask_json(TRACKER_PROMPT, _article_text(html), state, settings, now)
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
            "trusted": True,
        })
    log(f"[fleet] weekly tracker: {len(reports)} carriers")
    return reports


def apply_home_baseline(state: dict, now: datetime | None = None) -> None:
    """Carriers the latest weekly tracker does not list as deployed are shown at home port.

    USNI's tracker lists every deployed carrier strike group, so absence means the carrier is
    in home waters (in port, in maintenance, or training locally). A carrier reported somewhere
    by news more recently than the tracker keeps that newer position.
    """
    fleet = state.setdefault("fleet", {})
    meta = state.get("fleet_meta") or {}
    listed = set(meta.get("tracker_hulls") or [])
    tracker_time = meta.get("tracker_time")
    if tracker_time and now and now - datetime.fromisoformat(tracker_time.replace("Z", "+00:00")) > TRACKER_MAX_AGE:
        listed, tracker_time = set(), None  # too old to say who is home now
    reports = []
    for hull, (place, lat, lon) in HOME.items():
        c = fleet.get(hull)
        if hull in listed:
            continue
        if c and c.get("lat") is not None:
            newer_news = tracker_time and (c.get("as_of") or "") > tracker_time and not c.get("at_home")
            # a carrier that just departed its home port is not "at home"
            at_home = haversine_km(c["lat"], c["lon"], lat, lon) < MOVE_KM and c.get("status") not in ("departed", "underway")
            if newer_news or at_home or not tracker_time:
                c["at_home"] = at_home
                continue
        reports.append({"hull": hull, "status": "home", "place": place, "lat": lat, "lon": lon, "heading_to": None,
                        "trusted": True, "time": tracker_time or "1970-01-01T00:00:00Z",
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


def repair(state: dict) -> None:
    """Once per FLEET_VERSION: re-check stored positions against the rules for news reports.
    A carrier whose position fails them goes back to its home-port baseline; a previous position
    that is vague or couldn't have been sailed from is dropped (so no line is drawn from it)."""
    if state.get("fleet_version", 1) >= FLEET_VERSION:
        return
    fleet = state.setdefault("fleet", {})
    for hull, c in list(fleet.items()):
        if hull not in CARRIERS or not _known(c) or str(c.get("source", "")).startswith(("USNI News Fleet", "Home port")):
            continue
        if _vague(c.get("place")) or _other_home(hull, c["lat"], c["lon"]):
            log(f"[fleet] {CARRIERS[hull][0]}: stored position {c.get('place')!r} fails the checks; back to home port")
            fleet.pop(hull)
            continue
        p = c.get("prev")
        if p and (_vague(p.get("place")) or _too_fast(p, c)):
            for k in ("prev", "moved_at"):
                c.pop(k, None)
        c["track"] = [t for t in c.get("track", []) if not _vague(t.get("place")) and not str(t.get("time")).startswith("1970")][-1:]
        c.pop("held", None)
    state["fleet_version"] = FLEET_VERSION


def mark_deployment(state: dict, reports: list[dict]) -> None:
    """Remember whether the weekly tracker last listed each carrier as deployed or in maintenance."""
    fleet = state.setdefault("fleet", {})
    for r in reports:
        hull = _hull(r.get("hull"))
        if hull in fleet and ("deployed" in r or "maintenance" in r):
            fleet[hull]["deployed"] = bool(r.get("deployed"))
            fleet[hull]["maintenance"] = bool(r.get("maintenance"))
