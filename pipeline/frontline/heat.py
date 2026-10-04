"""Heat agent: satellite fire detections around tracked settlements, the free stand-in for the
satellite imagery front-line mappers use (Black Bird Group: "commercial satellite imagery"; The
Economist and Bellingcat track fighting with NASA's fire data). No model calls.

NASA FIRMS lists the thermal anomalies its VIIRS instruments detect (375 m pixels, two satellites,
each passing at least twice a day): shelling, burning vehicles and positions show up, and so do
farm, bush and forest fires. So heat is never evidence of who holds a place, and on its own not even
of fighting. What it is used for: the reviewer sees, for each settlement it checks, how many
detections fell within the conflict's area_km over the last WINDOW days and the WINDOW before
("satellite_heat"), and is told what that can and can't mean. A town reported contested with heat
around it is better supported than one without; a quiet town is not thereby held.

Data: the FIRMS area API (https://firms.modaps.eosdis.nasa.gov/api/), NASA open data, free with a
MAP_KEY (an email address at https://firms.modaps.eosdis.nasa.gov/api/map_key/; the repository secret
FIRMS_MAP_KEY). Without the key this agent does nothing. One request per conflict and satellite at
most every FETCH_EVERY, for the conflict's box (center, radius_km), the last DAY_RANGE days; only
the daily counts near tracked settlements are kept (`state["frontline"]["heat"]`), not the points.
"""
from __future__ import annotations

import csv
import io
import math
import os
from datetime import timedelta

from common import haversine_km, iso, log, parse_time

API = "https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/{source}/{box}/{days}"
SOURCES = ["VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT"]
FETCH_EVERY = timedelta(hours=6)
DAY_RANGE = 2          # days per request (each fetch overlaps the last; counts are per day, so no double count)
WINDOW = 7             # days compared: the last WINDOW and the WINDOW before
KEEP_DAYS = 2 * WINDOW + 1
TIMEOUT = 60


def _box(conflict: dict) -> str:
    lat, lon = conflict["center"]
    r = conflict["radius_km"]
    dlat = r / 111.0
    dlon = r / (111.0 * max(0.2, math.cos(math.radians(lat))))
    return ",".join(f"{v:.3f}" for v in (max(-180, lon - dlon), max(-90, lat - dlat), min(180, lon + dlon), min(90, lat + dlat)))


def _rows(text: str) -> list[dict]:
    try:
        return list(csv.DictReader(io.StringIO(text)))
    except csv.Error:
        return []


def update(conflicts: list[dict], fl: dict, session, now, env=os.environ) -> int:
    """Fetch new detections (at most every FETCH_EVERY) and count them by day near each tracked,
    placed settlement. Returns how many detections were read."""
    key = (env.get("FIRMS_MAP_KEY") or "").strip()
    st = fl.setdefault("heat", {"checked": None, "places": {}})
    if not key:
        return 0
    checked = parse_time(st.get("checked"))
    if checked and now - checked < FETCH_EVERY:
        return 0
    st["checked"] = iso(now)
    places = [(k, p) for k, p in fl["places"].items() if p.get("lat") is not None]
    read = 0
    for c in conflicts:
        mine = [(k, p) for k, p in places if p["conflict"] == c["id"]]
        if not mine:
            continue
        radius = float(c.get("area_km", 10))
        days: dict[str, dict[str, set]] = {}
        for source in SOURCES:
            try:
                r = session.get(API.format(key=key, source=source, box=_box(c), days=DAY_RANGE), timeout=TIMEOUT)
                r.raise_for_status()
            except Exception as exc:  # noqa: BLE001 - counts stay as they were; tried again later
                log(f"[frontline] heat: FIRMS {source} for {c['id']} failed: {str(exc).replace(key, '***')[:160]}")
                return read
            if not r.text.lstrip().lower().startswith("latitude"):   # an error message, not data
                log(f"[frontline] heat: FIRMS answered without data for {c['id']}: {r.text[:120].replace(key, '***')}")
                return read
            for row in _rows(r.text):
                try:
                    lat, lon, day = float(row["latitude"]), float(row["longitude"]), row["acq_date"]
                except (KeyError, ValueError):
                    continue
                if (row.get("confidence") or "").lower() in ("l", "low"):
                    continue
                read += 1
                for k, p in mine:
                    if abs(p["lat"] - lat) * 111 > radius or haversine_km(p["lat"], p["lon"], lat, lon) > radius:
                        continue
                    # a detection is one pixel and pass: both satellites see the same fire, so a
                    # place's day counts distinct ~1 km cells, not raw rows
                    days.setdefault(k, {}).setdefault(day, set()).add((round(lat, 2), round(lon, 2)))
        for k, by_day in days.items():
            have = st["places"].setdefault(k, {})
            for day, cells in by_day.items():
                have[day] = max(have.get(day, 0), len(cells))
    cutoff = (now - timedelta(days=KEEP_DAYS)).date().isoformat()
    st["places"] = {k: {d: n for d, n in v.items() if d >= cutoff} for k, v in st["places"].items() if k in fl["places"]}
    st["places"] = {k: v for k, v in st["places"].items() if v}
    log(f"[frontline] heat: {read} satellite fire detections read; heat near {len(st['places'])} tracked settlements")
    return read


def near(fl: dict, key: str, now) -> dict | None:
    """What the reviewer is told about heat around one settlement, or None without data."""
    st = fl.get("heat") or {}
    if not st.get("checked"):
        return None
    by_day = (st.get("places") or {}).get(key, {})
    today = now.date()
    last = sum(n for d, n in by_day.items() if (today - timedelta(days=WINDOW)).isoformat() < d <= today.isoformat())
    before = sum(n for d, n in by_day.items() if (today - timedelta(days=2 * WINDOW)).isoformat() < d <= (today - timedelta(days=WINDOW)).isoformat())
    return {f"last_{WINDOW}_days": last, f"previous_{WINDOW}_days": before}
