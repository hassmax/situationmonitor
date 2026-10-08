"""Place extracted events on the map.

The model gives a place name and a rough coordinate. OpenStreetMap's Nominatim
geocoder (free, 1 request/second, results cached) confirms it. If the two disagree
by more than 300 km, the model's estimate is kept and the event is marked approximate,
unless the model's coordinates are not even in the country it named (a reverse lookup):
the model once put Baidoa, Somalia 20 degrees east, in the Indian Ocean, while the lookup
had found Baidoa itself. Then the lookup wins. Stored approximate events are re-checked
the same way once per REPAIR_VERSION, a few per run. For strikes, fighting and naval incidents,
neither the lookup nor the model's coordinates are used when they are far outside the theater, and
an event placed in a named sea is pinned near that sea (SEA_KM), stored events included.
"""
from __future__ import annotations

import re
import time

from common import haversine_km, log

NOMINATIM = "https://nominatim.openstreetmap.org/search"
NOMINATIM_REVERSE = "https://nominatim.openstreetmap.org/reverse"
REPAIR_VERSION = 1
REPAIR_PER_RUN = 10

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

    def country_at(self, lat: float, lon: float) -> str | None:
        """ISO alpha-2 code of the country at a point ("" at sea), or None if it can't be looked up."""
        key = f"rev|{lat:.1f}|{lon:.1f}"
        if key in self.cache:
            return self.cache[key]
        if self.budget <= 0:
            return None
        self.budget -= 1
        params = {"lat": lat, "lon": lon, "format": "jsonv2", "zoom": 3, "accept-language": "en"}
        try:
            r = self.session.get(NOMINATIM_REVERSE, params=params, timeout=20)
            time.sleep(1.1)
            self.calls += 1
            if r.status_code != 200:
                return None
            code = str(((r.json() or {}).get("address") or {}).get("country_code") or "").upper()
        except Exception as exc:  # noqa: BLE001
            log(f"[geo] reverse {lat},{lon}: {exc}")
            return None
        self.cache[key] = code
        return code

    def locate(self, place, admin1, country, hint):
        cands = self.candidates(place, admin1, country)
        if not cands:
            return None
        if hint:
            best = min(cands, key=lambda c: haversine_km(c[0], c[1], hint[0], hint[1]))
            if haversine_km(best[0], best[1], hint[0], hint[1]) > 300:
                # The lookup was limited to the named country; if the model's coordinates are
                # outside it (or at sea), they are wrong and the lookup's best match is used.
                if country and self.country_at(hint[0], hint[1]) not in (None, country.upper()):
                    log(f"[geo] {place}: the model's coordinates {hint} are outside {country}; using the map lookup")
                    return cands[0]
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
    "philippine sea": (20.0, 130.0), "east sea": (40.0, 135.0), "yellow sea": (36.0, 123.5), "west sea": (36.0, 123.5), "caribbean sea": (15.0, -75.0), "eastern mediterranean": (33.5, 33.5),
}


def _sea(name: str | None):
    low = (name or "").lower()
    return next((v for k, v in SEAS.items() if k in low), None)


def sea_exact(place: str | None):
    """The anchor when the place is just a sea or strait's name ("Sea of Japan", "the Red Sea").
    Such names are not looked up: a lookup limited to the country named finds something on land
    that carries the name (the Sea of Japan in Kawasaki, the Taiwan Strait in Kaohsiung)."""
    low = re.sub(r"^the\s+", "", (place or "").strip().lower())
    return SEAS.get(low)


# An event placed in a named sea is pinned within SEA_KM of it. The model once gave the Caribbean
# Sea as 15, 75 instead of 15, -75 (southern India); the lookup, limited to Cuba, found nothing, so
# its coordinates were used unchecked.
SEA_KM = 2000


def _off_sea(place, lat, lon):
    """The named sea's anchor when (lat, lon) is far from the sea the place names, else None."""
    sea = _sea(place)
    return sea if sea and lat is not None and lon is not None and haversine_km(lat, lon, *sea) > SEA_KM else None


# US combatant commands name a region, not a place: "six F-16s moved from Aviano to CENTCOM" was
# pinned to CENTCOM's headquarters in Tampa and its route drawn to the middle of the US. Events
# placed at a command, and transfers sent to one, go to the region instead, marked approximate
# and labeled as the command's area (routes to a region are drawn faint, like country-level ones).
# The anchors are over open water or a region's middle, never a particular base.
COMMANDS = [
    (re.compile(r"\bCENTCOM\b|\bCentral Command\b", re.IGNORECASE), "Middle East (CENTCOM area)", 27.0, 51.0),
    (re.compile(r"\bEUCOM\b|\bEuropean Command\b", re.IGNORECASE), "Europe (EUCOM area)", 50.0, 15.0),
    (re.compile(r"\bAFRICOM\b|\bAfrica Command\b", re.IGNORECASE), "Africa (AFRICOM area)", 5.0, 20.0),
    (re.compile(r"\bINDOPACOM\b|\bIndo-Pacific Command\b|\bPACOM\b", re.IGNORECASE),
     "Indo-Pacific (INDOPACOM area)", 15.0, 135.0),
    (re.compile(r"\bSOUTHCOM\b|\bSouthern Command\b", re.IGNORECASE), "Latin America (SOUTHCOM area)", 15.0, -75.0),
]


_FROM_COMMAND = re.compile(r"\bfrom\s+(?:the\s+)?(?:US\s+|U\.S\.\s+)?(CENTCOM|EUCOM|AFRICOM|INDOPACOM|PACOM|SOUTHCOM|"
                           r"(?:Central|European|Africa|Indo-Pacific|Southern)\s+Command)", re.IGNORECASE)
_AIRCRAFT = re.compile(r"\b(?:aircraft|jets?|planes?|tankers?|bombers?|fighters?|KC-\d+s?|F-\d+s?|B-\d+s?|C-\d+s?)\b", re.IGNORECASE)


# Region names are not places to look up: "Middle East" went to a Baltimore neighborhood of that
# name. Events placed at a region go to a labeled anchor, marked approximate.
REGIONS = {
    "middle east": (29.0, 45.0), "the middle east": (29.0, 45.0), "levant": (33.5, 36.0),
    "persian gulf": (27.0, 51.5), "gulf region": (27.0, 51.5), "horn of africa": (8.0, 44.0),
    "sahel": (15.0, 2.0), "the sahel": (15.0, 2.0), "africa": (5.0, 20.0), "north africa": (28.0, 10.0),
    "europe": (50.0, 10.0), "eastern europe": (50.0, 28.0), "western europe": (48.0, 5.0),
    "balkans": (43.0, 20.0), "the balkans": (43.0, 20.0), "caucasus": (42.0, 45.0), "central asia": (42.0, 65.0),
    "east asia": (35.0, 118.0), "southeast asia": (10.0, 108.0), "indo-pacific": (15.0, 135.0),
    "asia-pacific": (15.0, 135.0), "latin america": (5.0, -70.0), "caribbean": (15.0, -75.0),
    "scandinavia": (62.0, 15.0), "arctic": (78.0, 20.0), "nato's eastern flank": (54.0, 23.0),
}


def region_anchor(place: str | None):
    return REGIONS.get(re.sub(r"\s+", " ", str(place or "")).strip().lower())


# Strikes, fighting and incidents at sea happen in their theater: a lookup that lands farther than
# this from the theater's center is a different place with the same name.
IN_THEATER_TYPES = {"airstrike", "missile_drone", "air_defense", "explosion", "artillery", "ground", "territory", "naval"}
THEATER_KM = 5000  # a theater's `reach_km` in theaters.yaml overrides it (the Indo-Pacific spans Karachi to Tokyo)
_far_logged: set[str] = set()  # one log line per place and run


def _command(text: str | None):
    return next(((label, lat, lon) for rx, label, lat, lon in COMMANDS if rx.search(text or "")), None)


def pin_commands(events: list[dict]) -> int:
    """Move events placed at a US command to its region, and point transfers sent to a command at
    it. Runs every run over all events (cheap, and a no-op once done). Returns how many changed."""
    changed = 0
    for e in events:
        if e.get("wave"):
            # a wave takes its pin from its main target (merge._finish_wave)
            for t in e.get("targets") or []:
                r = region_anchor(t.get("place")) or sea_exact(t.get("place"))
                if r and (t.get("lat"), t.get("lon")) != r:
                    t.update(lat=r[0], lon=r[1])
                    changed += 1
            continue
        if e.get("alert"):
            continue
        c = _command(e.get("place"))
        if c and (e.get("lat"), e.get("lon")) != (c[1], c[2]):
            e.update(place=c[0], lat=c[1], lon=c[2], approx=True)
            changed += 1
        r = region_anchor(e.get("place")) or sea_exact(e.get("place"))
        if r and (e.get("lat"), e.get("lon")) != r:
            e.update(lat=r[0], lon=r[1], approx=True)
            changed += 1
        sea = _off_sea(e.get("place"), e.get("lat"), e.get("lon"))
        if sea:
            log(f"[geo] {e['summary'][:60]!r} was pinned at {e['lat']},{e['lon']}, far from the {e['place']}; moved there")
            e.update(lat=sea[0], lon=sea[1], approx=True)
            changed += 1
        # forces leaving a command's area ("KC-135s returning home from CENTCOM bases", stored as a
        # deployment at the home base) become a movement out of that region to where they went
        src = _FROM_COMMAND.search(e.get("summary") or "")
        if e.get("type") == "deployment" and src and e.get("country") and not _command(e.get("place")):
            origin = _command(src.group(1))
            e["type"] = "arms_transfer"
            e["transfer"] = {"kind": "delivery", "supplier": e["country"], "recipient": e["country"],
                             "mode": "air" if _AIRCRAFT.search(e.get("summary") or "") else "unspecified",
                             "from": {"place": origin[0], "lat": origin[1], "lon": origin[2], "region": True},
                             "to": {"place": e.get("place"), "lat": e["lat"], "lon": e["lon"]},
                             "via": [], "what": None, "flights": None, "value_usd": None, "money": False}
            changed += 1
        t = e.get("transfer")
        if e.get("type") == "arms_transfer" and isinstance(t, dict):
            to = t.get("to") or {}
            dest = _command(to.get("place")) or (None if to.get("place") else _command(e.get("place")) or _command(e.get("summary")))
            if dest and to.get("place") != dest[0]:
                t["to"] = {"place": dest[0], "lat": dest[1], "lon": dest[2], "region": True}
                changed += 1
            # a route that starts at a command or a whole region starts in that region, drawn faint and
            # labelled ("CENTCOM" was drawn from Nepal: the model gave its Tampa headquarters with the
            # longitude's sign flipped; "Africa" from the continent's middle, in Chad; 2026-10-07)
            src = t.get("from") or {}
            if src.get("place") and not src.get("region"):
                c = _command(src["place"])
                r = None if c else region_anchor(src["place"])
                if c:
                    t["from"] = {"place": c[0], "lat": c[1], "lon": c[2], "region": True}
                    changed += 1
                elif r:
                    t["from"] = {"place": src["place"], "lat": r[0], "lon": r[1], "region": True}
                    changed += 1
            if to.get("place") and not to.get("region") and t.get("to") is to and region_anchor(to["place"]):
                r = region_anchor(to["place"])
                t["to"] = {"place": to["place"], "lat": r[0], "lon": r[1], "region": True}
                changed += 1
    return changed


def repair(events: list[dict], geocoder: Geocoder, state: dict) -> int:
    """Re-check stored approximate events (a few per run, once per REPAIR_VERSION) with today's
    rule. Returns how many were moved."""
    st = state.setdefault("geo_repair", {})
    if st.get("version") != REPAIR_VERSION:
        st.clear()
        st.update(version=REPAIR_VERSION, done=[])
    done = set(st["done"])
    todo = [e for e in events if e.get("approx") and e.get("place") and e.get("country") and e["id"] not in done
            and not e.get("wave") and not e.get("alert") and not _sea(e["place"])]
    moved = 0
    for e in todo[:REPAIR_PER_RUN]:
        if geocoder.budget <= 1:
            break
        if geocoder.candidates(e["place"], None, e["country"]) is None:
            break  # no lookup possible now (budget or network): try again next run
        done.add(e["id"])
        hit = geocoder.locate(e["place"], None, e["country"], (e["lat"], e["lon"]))
        if hit and haversine_km(hit[0], hit[1], e["lat"], e["lon"]) > 1:
            log(f"[geo] moved {e['summary'][:60]!r} from {e['lat']},{e['lon']} to {hit[0]},{hit[1]} ({e['place']})")
            e.update(lat=hit[0], lon=hit[1], approx=False)
            moved += 1
    st["done"] = sorted(done & {e["id"] for e in events})
    return moved


def place_record(rec: dict, geocoder: Geocoder, theaters: list[dict]) -> dict | None:
    """Turn an extracted record into an event candidate with coordinates, or None."""
    item = rec["item"]
    hint = (rec["lat"], rec["lon"]) if rec["lat"] is not None and rec["lon"] is not None else None
    lat = lon = None
    approx = False
    th = next((t for t in theaters if t["id"] == rec.get("theater")), {})
    center, reach = th.get("camera"), float(th.get("reach_km") or THEATER_KM)
    far = lambda p: bool(center and rec.get("type") in IN_THEATER_TYPES  # noqa: E731
                         and haversine_km(p[0], p[1], center["lat"], center["lng"]) > reach)
    region = region_anchor(rec["place"]) or sea_exact(rec["place"])
    if region:
        lat, lon = region
        approx = True
    elif rec["place"]:
        hit = geocoder.locate(rec["place"], rec["admin1"], rec["country"], hint)
        if hit and far(hit):
            if rec["place"] not in _far_logged:
                _far_logged.add(rec["place"])
                log(f"[geo] {rec['place']}: the lookup's match {hit} is far outside the {rec['theater']} theater; not used")
            hit = None
        # The model's own coordinates get the same check, also when the lookup found nothing (a
        # sea, searched inside the country named, finds nothing; the Caribbean came back as 15, 75).
        if hint and far(hint):
            if not hit and rec["place"] not in _far_logged:
                _far_logged.add(rec["place"])
                log(f"[geo] {rec['place']}: the model's coordinates {hint} are far outside the {rec['theater']} theater; not used")
            hint = None
        if hit:
            lat, lon = hit
        elif hint:
            lat, lon = hint
            approx = True
    elif hint and rec["severity"] >= 2 and not far(hint):
        lat, lon = hint
        approx = True
    sea = _off_sea(rec["place"], lat, lon)
    if sea:
        lat, lon = sea
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
        "parties": rec.get("parties") or [],
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
            **({"claim_source": rec["claim_source"]} if "claim_source" in rec else {}),
            "launched": rec.get("launched"),
            "url": item["url"],
            "time": item["time"],
            "summary": rec["summary"],
            # a news item's own headline, for the old-story checks (datecheck, recency); internal,
            # never published (merge.public_event keeps only the listed report fields)
            **({"title": (item.get("text") or "").split("\n", 1)[0][:200]} if item["platform"] == "rss" else {}),
        },
    }
