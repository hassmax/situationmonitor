"""GDELT 2.0 event feed: a free, machine-coded news-event database updated every 15 minutes.

GDELT is noisy, so it is not used for individual markers. It feeds two things:
  1. the "news intensity" layer (hexagons on the globe), and
  2. corroboration: a post near a place that several distinct news outlets are
     reporting violence at counts as one extra independent source.
"""
from __future__ import annotations

import io
import re
import zipfile
from datetime import datetime, timedelta
from urllib.parse import urlparse

from common import UTC, haversine_km, health_fail, health_ok, iso, log, parse_time

LAST_UPDATE = "http://data.gdeltproject.org/gdeltv2/lastupdate.txt"
EXPORT_URL = "http://data.gdeltproject.org/gdeltv2/{stamp}.export.CSV.zip"
MAX_FILES_PER_RUN = 8

# CAMEO codes kept: all "fight" (19x) and "mass violence" (20x), plus bombings and assassinations.
VIOLENCE_EXACT = {"183", "1831", "1832", "1833", "1834", "185", "186"}
# Military posture (demonstrations of force, raised alert, mobilisation): kept only in the
# Indo-Pacific and only when a military actor is involved, to catch exercises and deployments.
POSTURE = {"150", "152", "154"}

CAMEO_LABELS = {
    "18": "Bombing or assassination", "19": "Armed clash", "190": "Military force used",
    "191": "Blockade", "192": "Occupation of territory", "193": "Small-arms fighting",
    "194": "Artillery or tank fire", "195": "Aerial attack", "196": "Ceasefire violation",
    "20": "Mass violence", "15": "Show of military force",
}

# Column positions in the GDELT 2.0 export file.
C = dict(a1_country=7, a1_type=12, a2_country=17, a2_type=22, is_root=25, code=26, root=28,
         geo_type=51, geo_name=52, geo_country=53, lat=56, lon=57, feature=58, added=59, url=60)


def _inside(lat, lon, box):
    s, w, n, e = box
    return s <= lat <= n and w <= lon <= e


def theater_for_fips(lat: float, lon: float, fips: str, theaters: list[dict]) -> str | None:
    for th in theaters:
        if fips and fips in th.get("fips", []):
            boxes = th.get("bbox")
            if not boxes or any(_inside(lat, lon, b) for b in boxes):
                return th["id"]
    for th in theaters:
        if any(_inside(lat, lon, b) for b in th.get("sea", [])):
            return th["id"]
    return None


def _stamps_to_fetch(latest: str, last_done: str | None) -> list[str]:
    t = datetime.strptime(latest, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
    out = []
    while len(out) < MAX_FILES_PER_RUN:
        stamp = t.strftime("%Y%m%d%H%M%S")
        if last_done and stamp <= last_done:
            break
        out.append(stamp)
        t -= timedelta(minutes=15)
    return list(reversed(out))


def _parse_row(cols: list[str], theaters: list[dict]) -> dict | None:
    if len(cols) < 61 or cols[C["is_root"]] != "1":
        return None
    code, root = cols[C["code"]], cols[C["root"]]
    mil = "MIL" in (cols[C["a1_type"]], cols[C["a2_type"]])
    violent = root in ("19", "20") or code in VIOLENCE_EXACT
    posture = code in POSTURE and mil
    if not (violent or posture):
        return None
    if cols[C["geo_type"]] in ("", "1"):  # country-level geocodes are too coarse
        return None
    try:
        lat, lon = float(cols[C["lat"]]), float(cols[C["lon"]])
    except ValueError:
        return None
    theater = theater_for_fips(lat, lon, cols[C["geo_country"]], theaters)
    required = next((t.get("gdelt_require_actor") for t in theaters if t["id"] == theater), None)
    if required and required not in (cols[C["a1_country"]], cols[C["a2_country"]]):
        theater = None
    if theater is None:
        return None
    if posture and theater != "indopac":
        return None
    url = cols[C["url"]]
    domain = urlparse(url).netloc.lower().removeprefix("www.")
    try:
        added = datetime.strptime(cols[C["added"]], "%Y%m%d%H%M%S").replace(tzinfo=UTC)
    except ValueError:
        return None
    label = CAMEO_LABELS.get(code) or CAMEO_LABELS.get(code[:3]) or CAMEO_LABELS.get(root, "Reported violence")
    return {
        "lat": round(lat, 3), "lon": round(lon, 3), "name": cols[C["geo_name"]],
        "feature": cols[C["feature"]] or f"{lat:.1f},{lon:.1f}", "theater": theater,
        "label": label, "url": url, "domain": domain, "time": iso(added),
    }


def fetch(state: dict, session, theaters: list[dict], health: dict) -> list[dict]:
    sid = "gdelt"
    rows: list[dict] = []
    try:
        txt = session.get(LAST_UPDATE, timeout=20).text
        m = re.search(r"/(\d{14})\.export\.CSV\.zip", txt)
        if not m:
            raise ValueError("could not read lastupdate.txt")
        latest = m.group(1)
        stamps = _stamps_to_fetch(latest, state.get("gdelt_last"))
        for stamp in stamps:
            r = session.get(EXPORT_URL.format(stamp=stamp), timeout=60)
            if r.status_code == 404:
                continue
            r.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
                with zf.open(zf.namelist()[0]) as fh:
                    for line in io.TextIOWrapper(fh, encoding="utf-8", errors="replace"):
                        row = _parse_row(line.rstrip("\n").split("\t"), theaters)
                        if row:
                            rows.append(row)
        state["gdelt_last"] = latest
        latest_dt = datetime.strptime(latest, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
        health[sid] = health_ok("GDELT news events", "gdelt", latest_dt, len(rows), health.get(sid))
    except Exception as exc:  # noqa: BLE001
        log(f"[gdelt] {exc}")
        health[sid] = health_fail("GDELT news events", "gdelt", exc, health.get(sid))
    return rows


def update_cells(cells: list[dict], rows: list[dict], now: datetime, retention_hours: int,
                 max_cells: int) -> list[dict]:
    """Aggregate GDELT rows into per-place cells (one per GDELT feature and theater)."""
    by_key = {c["key"]: c for c in cells}
    for r in rows:
        key = f"{r['theater']}:{r['feature']}"
        c = by_key.get(key)
        if c is None:
            c = by_key[key] = {
                "key": key, "lat": r["lat"], "lon": r["lon"], "name": r["name"],
                "theater": r["theater"], "first": r["time"], "last": r["time"],
                "events": 0, "domains": [], "urls": [], "labels": {},
            }
        c["events"] += 1
        c["first"] = min(c["first"], r["time"])
        c["last"] = max(c["last"], r["time"])
        if r["domain"] and r["domain"] not in c["domains"]:
            c["domains"].append(r["domain"])
            c["domains"] = c["domains"][-25:]
        if r["url"] not in c["urls"]:
            c["urls"] = ([r["url"]] + c["urls"])[:5]
        c["labels"][r["label"]] = c["labels"].get(r["label"], 0) + 1
    cutoff = iso(now - timedelta(hours=retention_hours))
    kept = [c for c in by_key.values() if c["last"] >= cutoff]
    kept.sort(key=lambda c: (len(c["domains"]), c["last"]), reverse=True)
    return kept[:max_cells]


class CellIndex:
    """Grid index so each event only checks nearby GDELT cells."""

    def __init__(self, cells: list[dict]):
        self.grid: dict[tuple[int, int], list[dict]] = {}
        for c in cells:
            self.grid.setdefault((int(c["lat"] // 1), int(c["lon"] // 1)), []).append(c)

    def near(self, lat: float, lon: float, radius_km: float):
        la, lo = int(lat // 1), int(lon // 1)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                for c in self.grid.get((la + dy, lo + dx), []):
                    if haversine_km(lat, lon, c["lat"], c["lon"]) <= radius_km:
                        yield c


def news_domains_near(index: CellIndex, event: dict, radius_km: float = 30.0) -> set[str]:
    """Distinct outlets GDELT saw reporting violence near this event around the same time."""
    start = parse_time(event["time"]) - timedelta(hours=6)
    end = parse_time(event["updated"]) + timedelta(hours=12)
    radius = radius_km * (2 if event.get("approx") else 1)
    domains: set[str] = set()
    for c in index.near(event["lat"], event["lon"], radius):
        first, last = parse_time(c["first"]), parse_time(c["last"])
        if last >= start and first <= end:
            domains.update(c["domains"])
    return domains
