"""Merge reports of the same incident and decide how confident the map should look.

Confidence rules (what the colors on the globe mean):
  corroborated  two or more independent sources, and at least one of them is not aligned
                with a party to the conflict (or sources from opposing sides agree)
  unconfirmed   a single unaligned source so far
  claimed       only sources aligned with one side (e.g. a ministry and friendly bloggers)
Nearby news coverage picked up by GDELT (3+ distinct outlets) counts as one unaligned source.

Attack waves: missile, drone, and interception reports with a known attacker are grouped
into one event per direction per day (e.g. Russia -> Ukraine on 26 September), listing every
location hit and every launch area named. Days run 09:00 to 09:00 UTC so an overnight
attack stays in one wave.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from common import haversine_km, iso, parse_time, short_hash
from sources.gdelt import CellIndex, news_domains_near

FAMILY = {
    "airstrike": "strike", "missile_drone": "strike", "air_defense": "strike", "explosion": "strike",
    "artillery": "ground", "ground": "ground", "territory": "ground",
    "naval": "naval", "deployment": "deployment", "diplomacy": "diplomacy", "ceasefire": "diplomacy",
    "hybrid": "hybrid", "incursion": "incursion", "arms_transfer": "transfer", "legal": "legal",
}
RADIUS_KM = {"strike": 30, "ground": 30, "naval": 150, "deployment": 120, "diplomacy": 400,
             "hybrid": 50, "incursion": 150, "transfer": 0, "legal": 400}
TRANSFER_WINDOW = timedelta(hours=72)  # repeated flights or sailings on one route become one "bridge"
WINDOW = timedelta(hours=12)

WAVE_TYPES = {"missile_drone", "air_defense", "explosion"}
TARGET_MERGE_KM = 15
MAX_TARGETS = 60
MAX_REPORTS = 80
ADJECTIVE = {"RU": "Russian", "UA": "Ukrainian", "IR": "Iranian", "IL": "Israeli", "YE": "Houthi",
             "LB": "Hezbollah", "US": "US", "BY": "Belarusian", "PK": "Pakistani", "IN": "Indian"}


def wave_day(t: str) -> str:
    return (parse_time(t) - timedelta(hours=9)).strftime("%Y-%m-%d")


def _is_wave(c: dict) -> bool:
    return (c["type"] in WAVE_TYPES and bool(c.get("attacker")) and bool(c.get("country"))
            and c["attacker"] != c["country"])


def _find_transfer(events: list[dict], cand: dict) -> dict | None:
    t = cand.get("transfer") or {}
    ct = parse_time(cand["time"])
    for e in events:
        et = e.get("transfer") or {}
        if (e["type"] == "arms_transfer" and et.get("supplier") == t.get("supplier")
                and et.get("recipient") == t.get("recipient") and ct - parse_time(e["updated"]) <= TRANSFER_WINDOW):
            return e
    return None


def _find_match(events: list[dict], cand: dict) -> dict | None:
    fam = FAMILY.get(cand["type"], "strike")
    if fam == "transfer":
        return _find_transfer(events, cand) if cand.get("transfer") else None
    ct = parse_time(cand["time"])
    best, best_d = None, float("inf")
    for e in events:
        if e.get("wave") or e["theater"] != cand["theater"] or FAMILY.get(e["type"], "strike") != fam:
            continue
        et, eu = parse_time(e["time"]), parse_time(e["updated"])
        if abs(ct - et) > WINDOW and abs(ct - eu) > WINDOW:
            continue
        d = haversine_km(e["lat"], e["lon"], cand["lat"], cand["lon"])
        radius = RADIUS_KM[fam] * (2 if (e.get("approx") or cand["approx"]) else 1)
        if d <= radius and d < best_d:
            best, best_d = e, d
    return best


def _max_or_none(*vals):
    vals = [v for v in vals if v is not None]
    return max(vals) if vals else None


def _add_origins(event: dict, origins: list[dict]) -> None:
    kept = event.setdefault("origins", [])
    for o in origins or []:
        if not any(haversine_km(o["lat"], o["lon"], k["lat"], k["lon"]) <= 30 for k in kept):
            kept.append(o)
    event["origins"] = kept[:8]


def _add_target(wave: dict, cand: dict) -> None:
    if cand["approx"]:
        return  # country-level statements add counts and sources, not a map location
    for t in wave["targets"]:
        if haversine_km(t["lat"], t["lon"], cand["lat"], cand["lon"]) <= TARGET_MERGE_KM:
            t["reports"] += 1
            t["severity"] = max(t["severity"], cand["severity"])
            t["time"] = min(t["time"], cand["time"])
            t["killed"] = _max_or_none(t.get("killed"), cand["killed"])
            t["injured"] = _max_or_none(t.get("injured"), cand["injured"])
            return
    if len(wave["targets"]) < MAX_TARGETS:
        wave["targets"].append({
            "place": cand["place"], "lat": cand["lat"], "lon": cand["lon"], "reports": 1,
            "severity": cand["severity"], "time": cand["time"],
            "killed": cand["killed"], "injured": cand["injured"],
        })


def _merge_wave(events: list[dict], cand: dict) -> None:
    key = "|".join([cand["theater"], cand["attacker"], cand["country"], wave_day(cand["time"])])
    wave = next((e for e in events if e.get("wave_key") == key), None)
    rep = cand["report"]
    if wave is None:
        wave = {
            "id": short_hash("wave", key), "wave": True, "wave_key": key,
            "theater": cand["theater"], "type": "missile_drone",
            "attacker": cand["attacker"], "country": cand["country"],
            "summary": cand["summary"], "place": cand["place"],
            "lat": cand["lat"], "lon": cand["lon"], "approx": cand["approx"],
            "origins": [], "targets": [], "severity": cand["severity"],
            "killed": None, "injured": None, "launched": None, "intercepted": None,
            "time": cand["time"], "updated": cand["time"], "reports": [],
        }
        events.append(wave)
    if any(r["url"] == rep["url"] for r in wave["reports"]):
        return
    wave["reports"].append(rep)
    wave["time"] = min(wave["time"], cand["time"])
    wave["updated"] = max(wave["updated"], cand["time"])
    wave["severity"] = max(wave["severity"], cand["severity"])
    wave["launched"] = _max_or_none(wave.get("launched"), cand.get("launched"))
    wave["intercepted"] = _max_or_none(wave.get("intercepted"), cand.get("intercepted"))
    _add_target(wave, cand)
    _add_origins(wave, cand.get("origins"))


def merge(events: list[dict], candidates: list[dict]) -> list[dict]:
    for cand in sorted(candidates, key=lambda c: c["time"]):
        if _is_wave(cand):
            _merge_wave(events, cand)
            continue
        rep = cand["report"]
        match = _find_match(events, cand)
        if match is None:
            events.append({
                "id": short_hash("event", rep["url"]),
                "theater": cand["theater"], "type": cand["type"], "summary": cand["summary"],
                "place": cand["place"], "country": cand["country"], "attacker": cand.get("attacker"),
                "lat": cand["lat"], "lon": cand["lon"], "approx": cand["approx"],
                "origins": list(cand.get("origins") or []),
                "transfer": cand.get("transfer"), "legal_basis": cand.get("legal_basis"),
                "severity": cand["severity"],
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
        match["attacker"] = match.get("attacker") or cand.get("attacker")
        match["legal_basis"] = match.get("legal_basis") or cand.get("legal_basis")
        if match.get("transfer") and cand.get("transfer"):
            mt, ct_ = match["transfer"], cand["transfer"]
            mt["flights"] = _max_or_none(mt.get("flights"), ct_.get("flights"))
            mt["what"] = mt.get("what") or ct_.get("what")
            if mt.get("mode") == "unspecified":
                mt["mode"] = ct_.get("mode")
        for k in ("killed", "injured"):
            match[k] = _max_or_none(match.get(k), cand[k])
        if match.get("approx") and not cand["approx"]:
            match.update(lat=cand["lat"], lon=cand["lon"], place=cand["place"], approx=False)
        _add_origins(match, cand.get("origins"))
    return events


def _headline(event: dict) -> dict:
    """Prefer an unaligned source, then higher weight, then the earliest report."""
    return sorted(
        event["reports"],
        key=lambda r: (r.get("side") is not None, -int(r.get("weight", 1)), r["time"]),
    )[0]


def _finish_wave(e: dict) -> None:
    targets = sorted(e["targets"], key=lambda t: (-t["severity"], -t["reports"], t["time"]))
    e["targets"] = targets
    if targets:
        main = targets[0]
        e.update(place=main["place"], lat=main["lat"], lon=main["lon"], approx=False)
    killed = [t["killed"] for t in targets if t.get("killed") is not None]
    injured = [t["injured"] for t in targets if t.get("injured") is not None]
    e["killed"] = sum(killed) if killed else None
    e["injured"] = sum(injured) if injured else None
    if (e.get("launched") or 0) >= 50 or len(targets) >= 8:
        e["severity"] = 3
    counted = [r for r in e["reports"] if r.get("launched")]
    if counted:
        e["summary"] = max(counted, key=lambda r: r["launched"])["summary"]
    elif len(targets) >= 2:
        names = [t["place"] for t in targets if t.get("place")][:3]
        adj = ADJECTIVE.get(e["attacker"], "")
        lead = f"{adj} drone and missile attack" if adj else "Drone and missile attack"
        listed = ", ".join(names[:-1]) + (" and " + names[-1] if len(names) > 1 else names[0] if names else "")
        e["summary"] = f"{lead}: strikes reported in {len(targets)} places, including {listed}."
    else:
        e["summary"] = _headline(e)["summary"]


def apply_status(events: list[dict], cells: list[dict]) -> None:
    index = CellIndex(cells)
    for e in events:
        if e.get("wave"):
            _finish_wave(e)
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
        if not e.get("wave"):
            e["summary"] = _headline(e)["summary"]


def prune(events: list[dict], now: datetime, retention_days: int, max_events: int) -> list[dict]:
    cutoff = iso(now - timedelta(days=retention_days))
    kept = [e for e in events if e["updated"] >= cutoff]
    kept.sort(key=lambda e: e["updated"], reverse=True)
    return kept[:max_events]


def public_event(e: dict) -> dict:
    """Strip internal fields before publishing."""
    out = {k: v for k, v in e.items() if k not in ("reports", "us", "cn", "wave_key", "origin")}
    if e.get("origin") and not e.get("origins"):
        out["origins"] = [e["origin"]]  # events stored before multi-origin support
    reports = sorted(e["reports"], key=lambda r: r["time"])[-MAX_REPORTS:]
    out["reports"] = [
        {k: r.get(k) for k in ("source", "platform", "kind", "side", "claim", "url", "time", "summary")}
        for r in reports
    ]
    return out
