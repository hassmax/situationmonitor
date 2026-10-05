"""Wikipedia baseline: no model. A one-time fill-in of held territory from Wikipedia's conflict
"detailed map" modules (the owner's request, 2026-10-05: "Use Wikipedia maps one time to fill in
the gaps"), for wars whose towns changed hands long ago and that outlets rarely describe in a way
the agents can count (Sudan, Myanmar, Somalia, the Sahel).

- What: each module is a list of towns with their coordinates and a coloured dot whose colour the
  map's legend gives to a side ("Location dot red.svg": the Sudanese Armed Forces). frontlines.yaml
  names, per conflict, the module (`wikipedia.page`) and which dots stand for which of our sides
  (`wikipedia.marks`); dots of groups we don't track, terrain (peaks), bases and airports are left
  out. Animated two-colour dots (`80x80-…-anim.gif`) are "contested". `near` keeps a side's dots
  only within `km` of another side's (Ethiopia: the government holds most of the country; only
  the ground near the fighting is of interest).
- When: once (`VERSION`; bump it to read the maps again). A map that fails is tried again next run.
  The snapshot is dropped `MAX_AGE` after it was read, so a stale picture can't linger.
- How it is used (cartographer.py): the dots shade ground like assessed settlements, but only where
  the agents have nothing: a dot within the conflict's `area_km` of a settlement the agents
  published is left out, so the site's own evidence always wins.
- Licence: Wikipedia's text, its modules included, is CC BY-SA 4.0: the source is credited with
  the map's name, link and last-edit date in the Sources list at the bottom of the page (`credits`), and the areas drawn
  partly from it are shared under the same licence.
"""
from __future__ import annotations

import math
import re
from datetime import timedelta
from urllib.parse import quote

from common import iso, log, parse_time

from . import land

API = "https://en.wikipedia.org/w/api.php"
RAW = "https://en.wikipedia.org/w/index.php"
PAGE = "https://en.wikipedia.org/wiki/"
LICENSE = "CC BY-SA 4.0"
LICENSE_URL = "https://creativecommons.org/licenses/by-sa/4.0/"
UA = "GlobalSituationMonitor/1.0 (https://github.com/hassmax/situationmonitor)"
VERSION = 1
MAX_AGE = timedelta(days=120)
COAST_KM = 50            # a coastal town just off the globe's coarse coastline still counts as on land
CONTESTED_RE = re.compile(r"^80x80-[a-z]+-[a-z]+-anim\.gif$")

_ENTRY = re.compile(r"\{[^{}]*?\blat\s*=[^{}]*\}")


def _field(entry: str, name: str) -> str | None:
    m = re.search(rf"\b{name}\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|(-?[\d.]+))", entry)
    return next((g for g in m.groups() if g is not None), None) if m else None


def _name(entry: str) -> str | None:
    label = _field(entry, "label")
    if label:
        m = re.search(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]", label)
        text = (m.group(2) or m.group(1)) if m else label
        text = re.sub(r"<[^>]+>|'''?", "", text).strip()
        if text:
            return text
    link = _field(entry, "link")
    return re.sub(r",.*$", "", link).strip() if link else None


def _key(mark: str) -> str:
    return mark.strip().lower().replace("_", " ")


def parse(text: str, cfg: dict) -> list[dict]:
    """The settlements of a module's source with the side its dot gives, or contested."""
    marks = {_key(k): v for k, v in (cfg.get("marks") or {}).items()}
    out = []
    for m in _ENTRY.finditer(text):
        entry = m.group(0)
        mark = _key(_field(entry, "mark") or "")
        holder = marks.get(mark)
        contested = bool(CONTESTED_RE.match(mark))
        if not holder and not contested:
            continue
        try:
            lat, lon = float(_field(entry, "lat")), float(_field(entry, "long"))
        except (TypeError, ValueError):
            continue
        name = _name(entry)
        if not name:
            continue
        out.append({"name": name, "lat": lat, "lon": lon, "holder": None if contested else holder,
                    "status": "contested" if contested else "assessed"})
    return out


def _km(a: dict, b: dict) -> float:
    kx = 111.32 * math.cos(math.radians((a["lat"] + b["lat"]) / 2))
    return math.hypot((a["lon"] - b["lon"]) * kx, (a["lat"] - b["lat"]) * 110.57)


def _near_only(points: list[dict], rule: dict | None) -> list[dict]:
    """Drop the listed sides' dots further than `km` from any dot of another side (or contested)."""
    if not rule:
        return points
    sides, km = set(rule.get("actors") or []), float(rule.get("km") or 100)
    others = [p for p in points if p["holder"] not in sides]
    return [p for p in points if p["holder"] not in sides or any(_km(p, o) <= km for o in others)]


def _placed(points: list[dict], conflict: dict) -> list[dict]:
    """Points within the conflict's radius, each given the conflict country it lies in."""
    from geo import ISO_NUMERIC
    rings = {c: land._rings().get(ISO_NUMERIC.get(c, ""), []) for c in conflict["countries"]}
    center = {"lat": conflict["center"][0], "lon": conflict["center"][1]}
    out = []
    for p in points:
        if conflict.get("radius_km") and _km(p, center) > conflict["radius_km"]:
            continue
        country = next((c for c, rs in rings.items() if rs and land.inside(rs, p["lon"], p["lat"])), None)
        if not country:   # just off the coarse coastline: the nearest conflict country, if close
            best = min(((min(_km(p, {"lon": x, "lat": y}) for r in rs for x, y in r[::4]), c)
                        for c, rs in rings.items() if rs), default=(None, None))
            country = best[1] if best[0] is not None and best[0] <= COAST_KM else None
        if country:
            out.append({**p, "country": country})
    return out


def _edited(session, title: str) -> str | None:
    r = session.get(API, params={"action": "query", "format": "json", "prop": "revisions", "titles": title,
                                 "rvprop": "timestamp"}, headers={"User-Agent": UA}, timeout=30)
    r.raise_for_status()
    page = next(iter(r.json()["query"]["pages"].values()))
    return (page.get("revisions") or [{}])[0].get("timestamp")


def run(conflicts: list[dict], state: dict, session, now) -> int:
    """Read the configured maps not yet read under VERSION; returns how many were read."""
    fl = state.setdefault("frontline", {})
    base = fl.get("baseline") or {}
    if base.get("version") != VERSION:
        base = {"version": VERSION, "maps": {}}
    for cid in [k for k, m in base["maps"].items() if now - (parse_time(m.get("read")) or now) > MAX_AGE]:
        log(f"[frontline] wikipedia: the {cid} snapshot is over {MAX_AGE.days} days old and no longer used")
        del base["maps"][cid]
        base.setdefault("expired", []).append(cid)
    fl["baseline"] = base
    read = 0
    for c in conflicts:
        cfg = c.get("wikipedia")
        if not cfg or c["id"] in base["maps"] or c["id"] in base.get("expired", []):
            continue
        title = cfg["page"]
        try:
            r = session.get(RAW, params={"title": title, "action": "raw"}, headers={"User-Agent": UA}, timeout=30)
            r.raise_for_status()
            edited = _edited(session, title)
        except Exception as exc:  # noqa: BLE001 - tried again next run
            log(f"[frontline] wikipedia: {title}: {exc}; tried again next run")
            continue
        points = _placed(_near_only(parse(r.text, cfg), cfg.get("near")), c)
        base["maps"][c["id"]] = {"title": title.removeprefix("Module:"), "url": PAGE + quote(title.replace(" ", "_")),
                                 "edited": edited, "read": iso(now), "points": points}
        sides = {}
        for p in points:
            sides[p["holder"] or "contested"] = sides.get(p["holder"] or "contested", 0) + 1
        log(f"[frontline] wikipedia: {title} (last edited {(edited or '?')[:10]}): {len(points)} places {sides}")
        read += 1
    return read


def points(fl: dict, conflicts: list[dict], published: list[dict]) -> list[dict]:
    """The baseline's places, shaped like the cartographer's, minus any within the conflict's
    area_km of a settlement the agents published (their own evidence wins)."""
    maps = (fl.get("baseline") or {}).get("maps") or {}
    out = []
    for c in conflicts:
        m = maps.get(c["id"])
        if not m:
            continue
        area = float(c.get("area_km") or 10)
        mine = [p for p in published if p["conflict"] == c["id"]]
        for p in m["points"]:
            if any(_km(p, q) <= area for q in mine):
                continue
            out.append({"name": p["name"], "region": None, "country": p["country"], "conflict": c["id"],
                        "lat": p["lat"], "lon": p["lon"], "status": p["status"], "holder": p["holder"],
                        "previous": None, "last": m.get("edited") or m["read"], "standing": False,
                        "fills": p["status"] == "assessed", "provinces": [], "baseline": True})
    return out


def credits(fl: dict, conflicts: list[dict]) -> list[dict]:
    """What the map credits: each Wikipedia map in use, with its link and last-edit date."""
    maps = (fl.get("baseline") or {}).get("maps") or {}
    return [{"conflict": c["id"], "name": c["name"], "title": maps[c["id"]]["title"], "url": maps[c["id"]]["url"],
             "edited": (maps[c["id"]].get("edited") or "")[:10], "license": LICENSE, "license_url": LICENSE_URL}
            for c in conflicts if c["id"] in maps and maps[c["id"]]["points"]]
