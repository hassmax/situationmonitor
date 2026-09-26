"""Place extracted events on the map.

The model gives a place name and a rough coordinate. OpenStreetMap's Nominatim
geocoder (free, 1 request/second, results cached) confirms it. If the two disagree
by more than 300 km, the model's estimate is kept and the event is marked approximate.
"""
from __future__ import annotations

import time

from common import haversine_km, log

NOMINATIM = "https://nominatim.openstreetmap.org/search"

# ISO alpha-2 -> ISO numeric (the ids used by the globe's country shapes).
ISO_NUMERIC = {
    "UA": "804", "RU": "643", "BY": "112", "MD": "498",
    "IL": "376", "PS": "275", "LB": "422", "SY": "760", "IQ": "368", "IR": "364", "YE": "887",
    "SA": "682", "JO": "400", "KW": "414", "BH": "048", "QA": "634", "AE": "784", "OM": "512",
    "SD": "729", "SS": "728", "ET": "231", "ER": "232", "SO": "706", "DJ": "262",
    "CD": "180", "RW": "646", "BI": "108", "UG": "800", "ML": "466", "BF": "854", "NE": "562",
    "NG": "566", "TD": "148", "MR": "478",
    "CN": "156", "TW": "158", "JP": "392", "KP": "408", "KR": "410", "PH": "608", "VN": "704",
    "MM": "104", "TH": "764", "KH": "116", "IN": "356", "PK": "586",
}


def _inside(lat, lon, box):
    s, w, n, e = box
    return s <= lat <= n and w <= lon <= e


def theater_for_iso(lat: float, lon: float, country: str | None, theaters: list[dict]) -> str | None:
    for th in theaters:
        if country and country in th.get("iso2", []):
            boxes = th.get("bbox")
            if not boxes or any(_inside(lat, lon, b) for b in boxes):
                return th["id"]
    for th in theaters:
        if any(_inside(lat, lon, b) for b in th.get("sea", [])):
            return th["id"]
    return None


class Geocoder:
    def __init__(self, cache: dict, session, budget: int):
        self.cache = cache
        self.session = session
        self.budget = budget
        self.calls = 0

    def _query(self, q: str, country: str | None):
        params = {"q": q, "format": "jsonv2", "limit": 5, "accept-language": "en"}
        if country:
            params["countrycodes"] = country.lower()
        try:
            r = self.session.get(NOMINATIM, params=params, timeout=20)
            time.sleep(1.1)  # Nominatim usage policy: max 1 request per second
            self.calls += 1
            if r.status_code != 200:
                return None
            return [[round(float(x["lat"]), 4), round(float(x["lon"]), 4)] for x in r.json()]
        except Exception as exc:  # noqa: BLE001
            log(f"[geo] {q}: {exc}")
            return None

    def candidates(self, place: str, admin1: str | None, country: str | None):
        key = "|".join([place, admin1 or "", country or ""]).lower()
        if key in self.cache:
            return self.cache[key]
        if self.budget <= 0:
            return None
        self.budget -= 1
        q = ", ".join(x for x in (place, admin1) if x)
        found = self._query(q, country)
        if found == [] and admin1 and self.budget > 0:
            self.budget -= 1
            found = self._query(place, country)
        if found is None:
            return None
        self.cache[key] = found
        while len(self.cache) > 6000:
            self.cache.pop(next(iter(self.cache)))
        return found

    def locate(self, place, admin1, country, hint):
        cands = self.candidates(place, admin1, country)
        if not cands:
            return None
        if hint:
            best = min(cands, key=lambda c: haversine_km(c[0], c[1], hint[0], hint[1]))
            if haversine_km(best[0], best[1], hint[0], hint[1]) > 300:
                return None
            return best
        return cands[0]


def place_record(rec: dict, geocoder: Geocoder, theaters: list[dict]) -> dict | None:
    """Turn an extracted record into an event candidate with coordinates, or None."""
    item = rec["item"]
    hint = (rec["lat"], rec["lon"]) if rec["lat"] is not None and rec["lon"] is not None else None
    lat = lon = None
    approx = False
    if rec["place"]:
        hit = geocoder.locate(rec["place"], rec["admin1"], rec["country"], hint)
        if hit:
            lat, lon = hit
        elif hint:
            lat, lon = hint
            approx = True
    elif hint and rec["severity"] >= 2:
        lat, lon = hint
        approx = True
    if lat is None:
        return None

    ids = {t["id"] for t in theaters}
    theater = rec["theater"] if rec["theater"] in ids else None
    if theater is None:
        theater = theater_for_iso(lat, lon, rec["country"], theaters)
    if theater is None:
        if not (rec["us"] or rec["cn"]):
            return None
        theater = "other"

    origin = None
    if rec["origin_lat"] is not None and rec["origin_lon"] is not None:
        if haversine_km(rec["origin_lat"], rec["origin_lon"], lat, lon) > 25:
            origin = {"lat": rec["origin_lat"], "lon": rec["origin_lon"], "place": rec["origin_place"]}

    return {
        "theater": theater,
        "type": rec["type"],
        "summary": rec["summary"],
        "place": rec["place"] or rec["admin1"],
        "country": rec["country"],
        "lat": lat,
        "lon": lon,
        "approx": approx,
        "origin": origin,
        "severity": rec["severity"],
        "us": rec["us"],
        "cn": rec["cn"],
        "killed": rec["killed"],
        "injured": rec["injured"],
        "time": item["time"],
        "report": {
            "source": item["source"],
            "platform": item["platform"],
            "kind": item["kind"],
            "side": item.get("side"),
            "group": item["group"],
            "weight": item.get("weight", 1),
            "claim": rec["claim"],
            "url": item["url"],
            "time": item["time"],
            "summary": rec["summary"],
        },
    }
