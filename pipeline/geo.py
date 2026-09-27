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
    "EE": "233", "LV": "428", "LT": "440", "FI": "246", "PL": "616", "RO": "642",
    "IL": "376", "PS": "275", "LB": "422", "SY": "760", "IQ": "368", "IR": "364", "YE": "887",
    "SA": "682", "JO": "400", "KW": "414", "BH": "048", "QA": "634", "AE": "784", "OM": "512",
    "SD": "729", "SS": "728", "ET": "231", "ER": "232", "SO": "706", "DJ": "262",
    "CD": "180", "RW": "646", "BI": "108", "UG": "800", "ML": "466", "BF": "854", "NE": "562",
    "NG": "566", "TD": "148", "MR": "478",
    "CN": "156", "TW": "158", "JP": "392", "KP": "408", "KR": "410", "PH": "608", "VN": "704",
    "MM": "104", "TH": "764", "KH": "116", "IN": "356", "PK": "586",
    "CU": "192", "VE": "862", "CO": "170", "EC": "218", "HT": "332", "MX": "484",
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


# Last-resort coordinates for events placed only by a named sea or strait.
SEAS = {
    "strait of hormuz": (26.57, 56.25), "hormuz": (26.57, 56.25), "gulf of oman": (24.5, 58.5),
    "persian gulf": (27.0, 51.5), "arabian gulf": (27.0, 51.5), "red sea": (20.0, 38.5),
    "bab el-mandeb": (12.6, 43.4), "bab al-mandab": (12.6, 43.4), "gulf of aden": (12.5, 47.5),
    "arabian sea": (16.0, 63.0), "black sea": (43.5, 34.0), "sea of azov": (46.0, 36.5),
    "baltic sea": (57.0, 19.0), "gulf of finland": (59.8, 25.5), "south china sea": (12.0, 114.0),
    "taiwan strait": (24.4, 119.6), "east china sea": (29.0, 125.0), "sea of japan": (40.0, 135.0),
    "philippine sea": (20.0, 130.0), "caribbean sea": (15.0, -75.0), "eastern mediterranean": (33.5, 33.5),
}


def _sea(name: str | None):
    low = (name or "").lower()
    return next((v for k, v in SEAS.items() if k in low), None)


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
        sea = _sea(rec["place"]) or _sea(rec["admin1"])
        if not sea:
            return None
        lat, lon = sea
        approx = True

    ids = {t["id"] for t in theaters}
    theater = rec["theater"] if rec["theater"] in ids else None
    if theater is None:
        theater = theater_for_iso(lat, lon, rec["country"], theaters)
    if theater is None:
        return None

    origins = [o for o in rec.get("origins", []) if haversine_km(o["lat"], o["lon"], lat, lon) > 25]

    return {
        "theater": theater,
        "type": rec["type"],
        "summary": rec["summary"],
        "place": rec["place"] or rec["admin1"],
        "country": rec["country"],
        "lat": lat,
        "lon": lon,
        "approx": approx,
        "origins": origins,
        "attacker": rec.get("attacker"),
        "launched": rec.get("launched"),
        "intercepted": rec.get("intercepted"),
        "alert": rec.get("alert", False),
        "transfer": rec.get("transfer"),
        "legal_basis": rec.get("legal_basis"),
        "severity": rec["severity"],
        "killed": rec["killed"],
        "injured": rec["injured"],
        "time": rec.get("happened") or item["time"],
        "report": {
            "source": item["source"],
            "platform": item["platform"],
            "kind": item["kind"],
            "side": item.get("side"),
            "group": item["group"],
            "weight": item.get("weight", 1),
            "claim": rec["claim"],
            "launched": rec.get("launched"),
            "url": item["url"],
            "time": item["time"],
            "summary": rec["summary"],
        },
    }
