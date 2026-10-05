"""Flight agent: military and other notable aircraft, from open ADS-B data. No model calls.

The owner asked (2026-10-04) for an agent that tracks military and other important flights, "from
things like OpenSky or Flightradar24". What was checked from GitHub's servers that day:
- adsb.lol: an unfiltered community flight tracker whose API is open data under the ODbL 1.0
  licence (credit "adsb.lol contributors"). `/v2/mil` lists every aircraft its database marks as
  military, worldwide, in one request (about 130 at a time). Used.
- Flightradar24: paid API, and its terms forbid automated reading. Not used.
- airplanes.live: refuses (403) until the project writes to ask. adsb.fi answers but its terms
  weren't checked. OpenSky: no military flag (mostly airliners), and its terms page refuses
  GitHub's servers. Not used.

Each run (one request, plus one for the hijack code 7500) the agent keeps the aircraft whose type
or callsign makes them notable (ROLES, CALLSIGNS): bombers, tankers, surveillance, airlift,
airborne command posts, government VIP flights, and any aircraft squawking 7500. Aircraft over the
contiguous United States are left out (training, domestic airlift and VIP jets between US cities
would bury everything else), except airborne command posts and hijack-code flights (US_KEEP). Each
aircraft's reported positions are kept for TRACK_HOURS (`state["flights"]`).

Flights are not drawn on the map (the owner, 2026-10-04: "Remove flights and only report
analysis"). What the agent hands on is a log of movements at watched bases
(`pipeline/config/flights.yaml`), one per aircraft and flight, kept for LOG_HOURS
(`state["flights"]["log"]`), for the regional analyst (analyst.py) to read and cite:
- took off: first seen within BASE_KM of a base below LOW_FT, or first seen climbing within
  CLIMB_KM below CLIMB_FT (the agent looks every 15 minutes, so a jet is often first seen well
  after take-off), then seen away from it;
- landed: last seen within BASE_KM of a base below LOW_FT, after being seen away from it;
- for a take-off without a landing, the last reported position as a distance and compass direction
  from the base, with the time. Nothing is said about where an aircraft is going unless it was
  seen landing there.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta

import math

from common import UTC, haversine_km, health_fail, health_ok, iso, log, parse_time, short_hash

API = "https://api.adsb.lol/v2/mil"
HIJACK = "https://api.adsb.lol/v2/sqk/7500"
MAP_LINK = "https://adsb.lol/?icao={hexes}"
SOURCE_NAME = "Flight tracking (adsb.lol)"
SOURCE_ID = "flights"
ATTRIBUTION = "Flight data: adsb.lol contributors, open data under the ODbL 1.0 licence"
LICENSE_URL = "https://opendatacommons.org/licenses/odbl/1-0/"

TRACK_HOURS = 12          # positions kept per aircraft
LIVE_MINUTES = 90         # counted as in the air now while seen this recently
SORTIE_GAP = timedelta(hours=2)   # a longer silence starts a new flight
MAX_POSITION_AGE = 120    # seconds: older positions in a reply are not used
MIN_STEP_KM = 3           # a new position is kept when it moved this far (or 10 minutes passed)
BASE_KM = 40              # "at" a watched base
LOW_FT = 12000            # below this near a base: taking off or landing
CLIMB_KM = 150            # first seen this close to a base, below CLIMB_FT and climbing: took off there
CLIMB_FT = 25000
LOG_HOURS = 72            # movements kept for the analyst (its 3 days of context)
REJOIN_HOURS = 8          # a flight lost from view, then seen landing at a base this soon after, landed there

# ICAO type designators (as adsb.lol gives them) -> (role, label). Types not listed (trainers,
# helicopters, light aircraft) are not shown.
ROLES = {
    "B52": ("bomber", "B-52 bomber"), "B1": ("bomber", "B-1B bomber"), "B2": ("bomber", "B-2 stealth bomber"),
    "TU95": ("bomber", "Tu-95 bomber"), "T160": ("bomber", "Tu-160 bomber"), "TU22": ("bomber", "Tu-22M3 bomber"),
    "H6": ("bomber", "H-6 bomber"),
    "K35R": ("tanker", "KC-135 tanker"), "K35E": ("tanker", "KC-135 tanker"), "KC46": ("tanker", "KC-46 tanker"),
    "KC10": ("tanker", "KC-10 tanker"), "A332": ("tanker", "Airbus A330 (military: tanker or transport)"),
    "B762": ("tanker", "Boeing 767 (military: tanker or surveillance)"), "IL78": ("tanker", "Il-78 tanker"),
    "R135": ("surveillance", "RC-135 reconnaissance"), "E3TF": ("surveillance", "E-3 AWACS"), "E3CF": ("surveillance", "E-3 AWACS"),
    "E737": ("surveillance", "E-7 Wedgetail AWACS"), "E2": ("surveillance", "E-2 Hawkeye"), "P8": ("surveillance", "P-8 maritime patrol"),
    "P3": ("surveillance", "P-3 Orion maritime patrol"), "Q4": ("surveillance", "RQ-4 Global Hawk drone"),
    "MQ9": ("surveillance", "MQ-9 Reaper drone"), "U2": ("surveillance", "U-2 reconnaissance"), "IL20": ("surveillance", "Il-20 reconnaissance"),
    "A50": ("surveillance", "A-50 AWACS"), "GLEX": ("surveillance", "Global Express (military: surveillance or VIP)"),
    "E4": ("command", "E-4B airborne command post"), "E6": ("command", "E-6B airborne command post"),
    "C17": ("airlift", "C-17 transport"), "C5M": ("airlift", "C-5 transport"), "C5": ("airlift", "C-5 transport"),
    "A400": ("airlift", "A400M transport"), "IL76": ("airlift", "Il-76 transport"), "A124": ("airlift", "An-124 transport"),
    "C30J": ("airlift", "C-130J transport"), "C130": ("airlift", "C-130 transport"), "Y20": ("airlift", "Y-20 transport"),
    "B742": ("government", "Boeing 747 (military or government)"), "VC25": ("government", "VC-25 (Air Force One aircraft)"),
}
# Callsign prefixes -> who flies them; those marked government make any aircraft a VIP flight.
CALLSIGNS = {
    "RCH": ("US Air Mobility Command", None), "SAM": ("US Air Force Special Air Mission (government VIP)", "government"),
    "SPAR": ("US government VIP flight", "government"), "EXEC": ("US government VIP flight", "government"),
    "AF1": ("Air Force One", "government"), "AF2": ("Air Force Two", "government"), "CNV": ("US Navy", None),
    "PAT": ("US Army", None), "RRR": ("Royal Air Force", None), "ASCOT": ("Royal Air Force transport", None),
    "NATO": ("NATO", None), "QAF": ("Qatar Amiri Flight (government VIP)", "government"), "IAM": ("Italian Air Force", None),
    "GAF": ("German Air Force", None), "CTM": ("French Air and Space Force", None), "FAF": ("French Air and Space Force", None),
    "PLF": ("Polish Air Force", None), "BAF": ("Belgian Air Component", None), "NAF": ("Royal Netherlands Air Force", None),
    "MMF": ("NATO Multinational MRTT Fleet", None), "RSD": ("Russian government (Rossiya special flight detachment)", "government"),
    "FORTE": ("US Air Force RQ-4 Global Hawk", None), "HOMER": ("US Navy maritime patrol", None),
    "UAF": ("UAE Air Force", None), "ASY": ("Royal Australian Air Force", None), "CFC": ("Royal Canadian Air Force", None),
}
# Airframes known by registration: the E-4B (shown by adsb.lol as a Boeing 747) and the VC-25s.
REGISTRATIONS = {
    "73-1676": ("command", "E-4B airborne command post"), "73-1677": ("command", "E-4B airborne command post"),
    "74-0787": ("command", "E-4B airborne command post"), "75-0125": ("command", "E-4B airborne command post"),
    "82-8000": ("government", "VC-25 (Air Force One aircraft)"), "92-9000": ("government", "VC-25 (Air Force One aircraft)"),
}
# Plain names for the jets government flights use, when the type itself isn't one listed above.
TYPE_NAMES = {
    "GLF4": "Gulfstream IV", "GLF5": "Gulfstream V", "GLF6": "Gulfstream G650", "GL7T": "Global 7500", "LJ35": "Learjet 35",
    "B752": "Boeing 757", "B737": "Boeing 737", "B738": "Boeing 737", "A319": "Airbus A319", "A320": "Airbus A320",
    "A321": "Airbus A321", "A333": "Airbus A330", "A359": "Airbus A350", "B77W": "Boeing 777", "B788": "Boeing 787",
    "F900": "Falcon 900", "FA7X": "Falcon 7X", "FA8X": "Falcon 8X", "IL96": "Il-96", "T204": "Tu-204", "T214": "Tu-214",
}
# Over the contiguous United States only these are kept (the rest is training and domestic travel).
US_KEEP = {"command", "emergency"}
IMPORTANCE = {"emergency": 7, "command": 6, "bomber": 5, "government": 4, "surveillance": 3, "tanker": 2, "airlift": 1}
COMPASS = ["north", "northeast", "east", "southeast", "south", "southwest", "west", "northwest"]


def _contiguous_us(lat: float, lon: float) -> bool:
    return 24.5 <= lat <= 49.5 and -125 <= lon <= -66.5


def _callsign(a: dict) -> str:
    c = (a.get("flight") or "").strip().upper()
    return c if len(c) >= 3 else ""   # adsb.lol shows "X" or "" for missing callsigns


def _prefix(callsign: str) -> str:
    m = re.match(r"[A-Z]+", callsign or "")
    return m.group(0) if m else ""


def classify(a: dict, hijack: bool = False) -> tuple[str, str, str | None] | None:
    """(role, label, operator) for a notable aircraft, else None."""
    t = (a.get("t") or "").replace("?", "").strip().upper()
    callsign = _callsign(a)
    op, op_role = CALLSIGNS.get(_prefix(callsign), (a.get("ownOp"), None))
    if hijack:
        return "emergency", "Transponder set to the hijack code 7500 (often set in error)", op
    role, label = REGISTRATIONS.get((a.get("r") or "").strip()) or ROLES.get(t, (None, None))
    if op_role == "government" and role != "command":
        name = label if role == "government" else TYPE_NAMES.get(t) or a.get("desc") or t or "aircraft"
        return "government", label if role == "government" else f"{name} (government VIP flight)", op
    if not role:
        return None
    return role, label, op


def _alt(a: dict):
    alt = a.get("alt_baro")
    return 0 if alt == "ground" else (int(alt) if isinstance(alt, (int, float)) else None)


def _fetch(session, url: str) -> tuple[list[dict], float]:
    r = session.get(url, timeout=30)
    r.raise_for_status()
    j = r.json()
    return j.get("ac") or [], float(j.get("now") or time.time() * 1000) / 1000


def update(state: dict, session, health: dict, now: datetime, bases: list[dict], surge_rules: list[dict] | None = None) -> int:
    """Read the current military aircraft and the hijack code, keep the notable ones' positions,
    log their take-offs and landings at watched bases, and find surges (several aircraft of one kind
    at one base in a short time). Returns how many movements are logged."""
    st = state.setdefault("flights", {"aircraft": {}, "log": {}})
    st.pop("moves", None)  # group reports for the extraction queue, until flights left the map
    try:
        mil, stamp = _fetch(session, API)
    except Exception as exc:  # noqa: BLE001 - positions stay as they were; tried again next run
        log(f"[flights] adsb.lol failed: {exc}")
        health[SOURCE_ID] = health_fail(SOURCE_NAME, "adsb", exc, health.get(SOURCE_ID))
        return 0
    try:
        time.sleep(2)  # adsb.lol answers quick repeats with 429
        hijack, _ = _fetch(session, HIJACK)
    except Exception as exc:  # noqa: BLE001
        log(f"[flights] adsb.lol hijack-code list failed: {exc}")
        hijack = []
    kept = 0
    for a, is_hijack in [(a, False) for a in mil] + [(a, True) for a in hijack]:
        hexid, lat, lon = (a.get("hex") or "").lower(), a.get("lat"), a.get("lon")
        if not hexid or lat is None or lon is None or (a.get("seen_pos") or 0) > MAX_POSITION_AGE:
            continue
        what = classify(a, is_hijack)
        if not what:
            continue
        role, label, op = what
        if _contiguous_us(lat, lon) and role not in US_KEEP:
            continue
        ts = int(stamp - float(a.get("seen_pos") or 0))
        rec = st["aircraft"].setdefault(hexid, {"pts": []})
        rec.update({"callsign": _callsign(a), "reg": a.get("r") or "", "type": (a.get("t") or "").replace("?", "").strip(),
                    "role": role, "label": label, "op": op, "gs": a.get("gs"), "heading": a.get("track")})
        pts, alt = rec["pts"], _alt(a)
        if not pts or ts - pts[-1][3] >= 600 or haversine_km(pts[-1][0], pts[-1][1], lat, lon) >= MIN_STEP_KM:
            if not pts or ts > pts[-1][3]:
                pts.append([round(lat, 4), round(lon, 4), alt, ts])
        kept += 1
    cutoff = now.timestamp() - TRACK_HOURS * 3600
    for hexid in list(st["aircraft"]):
        rec = st["aircraft"][hexid]
        rec["pts"] = [p for p in rec["pts"] if p[3] >= cutoff]
        if not rec["pts"]:
            del st["aircraft"][hexid]
    st["checked"] = iso(now)
    logged = movements(st, now, bases)
    before = set(st.get("surges") or {})
    for sg in surges(st, now, surge_rules):
        if sg["id"] not in before:
            log(f"[flights] surge: {surge_text(sg)}")
    latest = datetime.fromtimestamp(stamp, UTC)
    health[SOURCE_ID] = health_ok(SOURCE_NAME, "adsb", latest, kept, health.get(SOURCE_ID))
    log(f"[flights] {len(mil)} military aircraft listed, {len(hijack)} on the hijack code; {kept} notable kept, "
        f"{len(st['aircraft'])} tracked; {logged} take-offs and landings at watched bases in the last {LOG_HOURS} h")
    return logged


def _sorties(pts: list) -> list[list]:
    """The flights in a track: split at silences longer than SORTIE_GAP."""
    out: list[list] = []
    for p in pts:
        if not out or p[3] - out[-1][-1][3] > SORTIE_GAP.total_seconds():
            out.append([])
        out[-1].append(p)
    return out


def _sortie(pts: list) -> list:
    """The current flight: positions since the last silence longer than SORTIE_GAP."""
    return (_sorties(pts) or [[]])[-1]


def _nearest_base(lat: float, lon: float, bases: list[dict], within: float = BASE_KM) -> dict | None:
    best, best_km = None, within
    for b in bases:
        d = haversine_km(lat, lon, b["lat"], b["lon"])
        if d <= best_km:
            best, best_km = b, d
    return best


def _low(p) -> bool:
    return p[2] is not None and p[2] < LOW_FT


def _took_off(pts: list, bases: list[dict]) -> tuple[dict, float] | None:
    """(base, km from it when first seen) if this flight was first seen taking off from a watched base."""
    first = pts[0]
    if first[2] is None:
        return None
    b = _nearest_base(first[0], first[1], bases, CLIMB_KM)
    if not b:
        return None
    km = haversine_km(first[0], first[1], b["lat"], b["lon"])
    later = pts[1:]
    away = any(haversine_km(p[0], p[1], b["lat"], b["lon"]) > max(BASE_KM, km + 20) for p in later)
    if km <= BASE_KM and first[2] < LOW_FT and away:
        return b, km
    climbing = any(p[2] is not None and p[2] > first[2] + 2000 for p in later)
    if first[2] < CLIMB_FT and climbing and away:
        return b, km
    return None


def _landed(pts: list, bases: list[dict]) -> dict | None:
    last = pts[-1]
    b = _low(last) and _nearest_base(last[0], last[1], bases)
    if b and any(haversine_km(p[0], p[1], b["lat"], b["lon"]) > BASE_KM for p in pts[:-1]):
        return b
    return None


def _bearing(lat1, lon1, lat2, lon2) -> str:
    y = math.sin(math.radians(lon2 - lon1)) * math.cos(math.radians(lat2))
    x = (math.cos(math.radians(lat1)) * math.sin(math.radians(lat2))
         - math.sin(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.cos(math.radians(lon2 - lon1)))
    deg = (math.degrees(math.atan2(y, x)) + 360) % 360
    return COMPASS[int((deg + 22.5) // 45) % 8]


def movements(st: dict, now: datetime, bases: list[dict]) -> int:
    """Log each tracked flight's take-off from and landing at watched bases (`st["log"]`). A flight
    keeps one entry, filled in as it is seen: a take-off, then its last position, then a landing."""
    flog = st.setdefault("log", {})
    for hexid, rec in st["aircraft"].items():
        if rec.get("role") in (None, "emergency"):
            continue
        flown = _sorties(rec["pts"])
        for n, pts in enumerate(flown):
            nxt = flown[n + 1] if n + 1 < len(flown) else None
            _log_flight(flog, hexid, rec, pts, nxt, bases, now)
    cutoff = iso(now - timedelta(hours=LOG_HOURS))
    st["log"] = {k: v for k, v in flog.items() if (v.get("last") or {}).get("time", "") >= cutoff}
    return len(st["log"])


def _log_flight(flog: dict, hexid: str, rec: dict, pts: list, nxt: list | None, bases: list[dict], now) -> None:
    """One flight's entry. A flight lost from view (receivers don't cover deserts and open sea) and
    then seen low at a base within REJOIN_HOURS landed there, unseen."""
    dep, arr = (_took_off(pts, bases), _landed(pts, bases)) if len(pts) >= 2 else (None, None)
    rejoined = False
    if not arr and nxt and nxt[0][3] - pts[-1][3] <= REJOIN_HOURS * 3600 and _low(nxt[0]):
        b = _nearest_base(nxt[0][0], nxt[0][1], bases)
        if b and haversine_km(pts[-1][0], pts[-1][1], b["lat"], b["lon"]) > BASE_KM:
            arr, pts, rejoined = b, pts + [nxt[0]], True
    if len(pts) < 2 or (not dep and not arr):
        return
    key = f"{hexid}|{pts[0][3]}"
    entry = flog.setdefault(key, {"id": "f" + short_hash(hexid, pts[0][3])[:9], "hex": hexid})
    entry.update({k: rec.get(k) for k in ("role", "label", "op", "callsign", "reg", "type")})
    if dep:
        b, km = dep
        entry["from"] = {"base": b["name"], "country": b["country_name"], "lat": b["lat"], "lon": b["lon"],
                         "time": iso(datetime.fromtimestamp(pts[0][3], UTC)), "first_seen_km": round(km)}
    if arr:
        entry["to"] = {"base": arr["name"], "country": arr["country_name"], "lat": arr["lat"], "lon": arr["lon"],
                       "time": iso(datetime.fromtimestamp(pts[-1][3], UTC)), "next_seen": rejoined}
    last = pts[-1]
    entry["last"] = {"lat": last[0], "lon": last[1], "alt": last[2], "time": iso(datetime.fromtimestamp(last[3], UTC))}
    entry["first"] = {"lat": pts[0][0], "lon": pts[0][1], "time": iso(datetime.fromtimestamp(pts[0][3], UTC))}
    entry["updated"] = iso(now)


def _hhmm(value: str) -> str:
    t = parse_time(value)
    return t.strftime("%d %b %H:%M UTC").lstrip("0") if t else ""


def describe(entry: dict) -> str:
    """One plain sentence of what the transponder showed."""
    who = [entry.get("callsign"), entry.get("reg")]
    who = ", ".join(w for w in who if w)
    op = f", {entry['op']}" if entry.get("op") else ""
    text = f"{entry.get('label') or 'Military aircraft'}{' (' + who + op + ')' if who or op else ''}"
    dep, arr, last = entry.get("from"), entry.get("to"), entry.get("last") or {}
    if dep:
        seen = "" if dep.get("first_seen_km", 0) <= BASE_KM else f" (first seen climbing {dep['first_seen_km']} km away)"
        text += f" took off from {dep['base']}, {dep['country']}, at {_hhmm(dep['time'])}{seen}"
        if arr and arr.get("next_seen"):
            text += f"; it was next seen low at {arr['base']}, {arr['country']}, at {_hhmm(arr['time'])}, having landed there unseen"
        elif arr and arr["base"] != dep["base"]:
            text += f" and landed at {arr['base']}, {arr['country']}, at {_hhmm(arr['time'])}"
        elif arr:
            text += f" and landed back there at {_hhmm(arr['time'])}"
        elif last:
            km = round(haversine_km(dep["lat"], dep["lon"], last["lat"], last["lon"]))
            alt = f" at {last['alt']:,} ft" if last.get("alt") else ""
            text += (f"; last reported at {_hhmm(last['time'])}, {km} km {_bearing(dep['lat'], dep['lon'], last['lat'], last['lon'])}"
                     f" of the base{alt}, no landing seen yet")
    elif arr:
        first = entry.get("first") or {}
        landed = "was next seen low at" if arr.get("next_seen") else "landed at"
        text += f" {landed} {arr['base']}, {arr['country']}, at {_hhmm(arr['time'])}"
        if first:
            km = round(haversine_km(arr["lat"], arr["lon"], first["lat"], first["lon"]))
            text += f", arriving from the {_bearing(arr['lat'], arr['lon'], first['lat'], first['lon'])} (first seen {km} km away at {_hhmm(first['time'])})"
    return text + "."


# Surges (the owner, 2026-10-05: "if more than 3 C-17 fly somewhere in close timeframe, and same with
# KC-135, have the flight tracker agent deploy an alert"): this many distinct aircraft of one kind
# landing at, or taking off from, one watched base within `hours`. Set in flights.yaml (`surges`).
SURGE_DEFAULTS = [{"label": "C-17 transports", "types": ["C17"], "min": 4, "hours": 12},
                  {"label": "KC-135 tankers", "types": ["K35R", "K35E"], "min": 4, "hours": 12}]
ALERT_HOURS = 24          # a surge is shown on the dashboard while its last movement is this recent


def surges(st: dict, now: datetime, rules: list[dict] | None = None) -> list[dict]:
    """Find surges in the movement log and keep them (`st["surges"]`, as long as the log). A surge
    keeps its id as more aircraft join it, so it is alerted once."""
    found = st.setdefault("surges", {})
    for rule in rules or SURGE_DEFAULTS:
        types, need = set(rule.get("types") or []), int(rule.get("min", 4))
        span = timedelta(hours=float(rule.get("hours", 12)))
        for way in ("to", "from"):
            by_base: dict[str, list] = {}
            for e in (st.get("log") or {}).values():
                end = e.get(way)
                t = parse_time((end or {}).get("time"))
                if e.get("type") in types and end and t:
                    by_base.setdefault(end["base"], []).append((t, e, end))
            for base, rows in by_base.items():
                rows.sort(key=lambda r: r[0])
                i = 0
                while i < len(rows):
                    start = rows[i][0]
                    group = [r for r in rows[i:] if r[0] - start <= span]
                    hexes = {r[1]["hex"] for r in group}
                    if len(hexes) < need:
                        i += 1
                        continue
                    sid = "s" + short_hash(rule.get("label"), way, base, iso(start))[:9]
                    end = group[0][2]
                    rec = found.get(sid) or {"id": sid, "first_seen": iso(now)}
                    rec.update(label=rule.get("label"), way=way, base=base, country=end.get("country"),
                               lat=end.get("lat"), lon=end.get("lon"), count=len(hexes), first=iso(start), last=iso(group[-1][0]),
                               callsigns=sorted({r[1].get("callsign") for r in group if r[1].get("callsign")})[:12],
                               hexes=sorted(hexes), updated=iso(now))
                    if way == "from":
                        rec["onward"] = _onward([r[1] for r in group])
                    found[sid] = rec
                    i += len(group)
    cutoff = iso(now - timedelta(hours=LOG_HOURS))
    st["surges"] = {k: v for k, v in found.items() if v.get("last", "") >= cutoff}
    return list(st["surges"].values())


def _onward(entries: list[dict]) -> dict:
    """Where aircraft that took off together went: landings seen, else the way most were heading."""
    landed: dict[str, int] = {}
    ways: dict[str, int] = {}
    for e in entries:
        if e.get("to") and e["to"]["base"] != e["from"]["base"]:
            landed[e["to"]["base"]] = landed.get(e["to"]["base"], 0) + 1
        elif e.get("last") and haversine_km(e["from"]["lat"], e["from"]["lon"], e["last"]["lat"], e["last"]["lon"]) > 100:
            w = _bearing(e["from"]["lat"], e["from"]["lon"], e["last"]["lat"], e["last"]["lon"])
            ways[w] = ways.get(w, 0) + 1
    return {"landed": landed, "heading": ways}


def surge_text(s: dict) -> str:
    """One plain sentence: what the transponders showed, and nothing about why."""
    first, last = _hhmm(s["first"]), _hhmm(s["last"])
    when = f"between {first} and {last}" if s["first"] != s["last"] else f"at {first}"
    if s["way"] == "to":
        text = f"{s['count']} {s['label']} landed at {s['base']}, {s['country']}, {when}"
    else:
        text = f"{s['count']} {s['label']} took off from {s['base']}, {s['country']}, {when}"
        onward = s.get("onward") or {}
        bits = [f"{n} later landed at {b}" for b, n in sorted(onward.get("landed", {}).items(), key=lambda kv: -kv[1])]
        heading = onward.get("heading") or {}
        if heading:
            way, n = max(heading.items(), key=lambda kv: kv[1])
            bits.append(f"{n} last seen heading {way}")
        if bits:
            text += "; " + ", ".join(bits)
    return text + (f" ({', '.join(s['callsigns'][:6])})" if s.get("callsigns") else "") + "."


def alerts(state: dict, now: datetime) -> list[dict]:
    """Surges whose latest movement is within ALERT_HOURS, newest first, for the dashboard and alerts."""
    cutoff = iso(now - timedelta(hours=ALERT_HOURS))
    out = [{"id": s["id"], "text": surge_text(s), "time": s["last"], "count": s["count"], "label": s["label"],
            "base": s["base"], "way": s["way"], "lat": s.get("lat"), "lon": s.get("lon"),
            "url": MAP_LINK.format(hexes=",".join(s["hexes"]))}
           for s in ((state.get("flights") or {}).get("surges") or {}).values() if s.get("last", "") >= cutoff]
    return sorted(out, key=lambda a: a["time"], reverse=True)


# Whose aircraft: by callsign prefix, else by the ICAO address block the airframe is registered in.
OPERATOR_CALLSIGNS = {"RCH": "US", "CNV": "US", "PAT": "US", "SAM": "US", "SPAR": "US", "RRR": "GB", "ASCOT": "GB",
                      "ASY": "AU", "CFC": "CA", "QAF": "QA", "UAF": "AE", "GAF": "DE", "CTM": "FR", "FAF": "FR", "IAM": "IT", "PLF": "PL"}
OPERATOR_HEX = [("43c", "GB"), ("43d", "GB"), ("43e", "GB"), ("43f", "GB"), ("c0", "CA"), ("7c", "AU"), ("a", "US")]


def _operator(e: dict) -> str | None:
    cs = _prefix(e.get("callsign") or "")
    if cs in OPERATOR_CALLSIGNS:
        return OPERATOR_CALLSIGNS[cs]
    return next((c for p, c in OPERATOR_HEX if (e.get("hex") or "").startswith(p)), None)


def _most(values: list):
    counts: dict = {}
    for v in values:
        if v is not None:
            counts[v] = counts.get(v, 0) + 1
    return max(counts.items(), key=lambda kv: kv[1]) if counts else (None, 0)


def surge_records(state: dict, now: datetime, bases: list[dict], theaters: list[dict], item_maker) -> list[dict]:
    """Surges as air movements on the map (the owner, 2026-10-05: "make them into a supply route"):
    records shaped like the model's, for an arms-transfer event of a country moving its own aircraft,
    written from the transponder data alone (no model). A landing surge runs from the base most of
    its aircraft were seen leaving, else from the operator's country (drawn faint: not named); a
    take-off surge runs to the base most of them were seen landing at, and waits for a landing.
    A surge is sent again only when more aircraft join it."""
    st = state.get("flights") or {}
    log_entries = list((st.get("log") or {}).values())
    by_name = {b["name"]: b for b in bases}
    out = []
    for s in (st.get("surges") or {}).values():
        if s.get("routed", 0) >= s["count"]:
            continue
        group = [e for e in log_entries if e["hex"] in s["hexes"] and (e.get(s["way"]) or {}).get("base") == s["base"]]
        operator, n = _most([_operator(e) for e in group])
        if not operator or n * 2 < len(group):
            continue  # whose aircraft they are is unclear: no route
        here = by_name.get(s["base"]) or {}
        if s["way"] == "to":
            dest = {"place": s["base"], "lat": s["lat"], "lon": s["lon"], "country": here.get("country")}
            start, k = _most([(e.get("from") or {}).get("base") for e in group])
            origin = by_name.get(start) if start and start != s["base"] and k * 2 >= len(group) else None
        else:
            origin = here
            end, k = _most([(e.get("to") or {}).get("base") for e in group if (e.get("to") or {}).get("base") != s["base"]])
            if not end:
                continue  # no landing seen yet: no destination to draw to
            b = by_name.get(end) or {}
            dest = {"place": end, "lat": b.get("lat"), "lon": b.get("lon"), "country": b.get("country")}
            if dest["lat"] is None:
                continue
        theater = _theater(dest["lat"], dest["lon"], dest.get("country"), theaters)
        if not theater:
            continue
        text = surge_text(s)
        item = item_maker({"name": SOURCE_NAME, "kind": "osint", "group": SOURCE_ID, "weight": 2, "prefilter": False},
                          "adsb", SOURCE_ID, MAP_LINK.format(hexes=",".join(s["hexes"])), text, parse_time(s["last"]) or now,
                          uid=f"surge|{s['id']}|{s['count']}")
        out.append({
            "type": "arms_transfer", "happened": s["first"], "summary": text[:240], "place": dest["place"], "admin1": None,
            "country": dest.get("country"), "lat": dest["lat"], "lon": dest["lon"], "origins": [], "attacker": None,
            "parties": [], "launched": None, "intercepted": None, "alert": False, "legal_basis": None,
            "transfer": {"kind": "delivery", "via": [], "value_usd": None, "supplier": operator, "recipient": operator,
                         "mode": "air", "what": s["label"], "flights": s["count"], "money": False,
                         "from": {"place": origin["name"], "lat": origin["lat"], "lon": origin["lon"]} if origin else None,
                         "to": {"place": dest["place"], "lat": dest["lat"], "lon": dest["lon"]}},
            "theater": theater, "severity": 2, "claim": "report", "killed": None, "injured": None, "item": item})
        s["routed"] = s["count"]
    return out


def _theater(lat: float, lon: float, country: str | None, theaters: list[dict]) -> str | None:
    """The theater a base belongs to: by its country, else the nearest theater's camera point."""
    import geo

    th = geo.theater_for_iso(lat, lon, country, theaters)
    if th:
        return th
    near = [(haversine_km(lat, lon, t["camera"]["lat"], t["camera"]["lng"]), t["id"]) for t in theaters
            if t.get("camera") and t.get("listed") is not False]
    return min(near)[1] if near else None


def for_analyst(state: dict, now: datetime) -> dict:
    """What the regional analyst is given: aircraft in the air now (role and position), and the
    logged movements with a sentence each."""
    st = state.get("flights") or {}
    live = now.timestamp() - LIVE_MINUTES * 60
    aircraft = []
    for rec in (st.get("aircraft") or {}).values():
        pts = rec.get("pts") or []
        if pts and pts[-1][3] >= live:
            aircraft.append({"role": rec.get("role"), "lat": pts[-1][0], "lon": pts[-1][1]})
    surging = alerts(state, now)
    in_surge = {h for a in surging for h in a["url"].split("icao=", 1)[1].split(",")}
    moves = []
    for e in (st.get("log") or {}).values():
        ends = [x for x in (e.get("from"), e.get("to")) if x]
        moves.append({"id": e["id"], "role": e.get("role"), "text": describe(e), "time": (e.get("last") or {}).get("time"),
                      "places": [(x["lat"], x["lon"]) for x in ends], "url": MAP_LINK.format(hexes=e["hex"]),
                      "label": f"{(e.get('label') or 'Aircraft').split(' (')[0]} · {(ends[0]['base'])}",
                      **({"surge": True} if e["hex"] in in_surge else {})})
    moves.sort(key=lambda m: m["time"] or "", reverse=True)
    return {"aircraft": aircraft, "movements": moves, "surges": surging,
            "attribution": ATTRIBUTION, "license_url": LICENSE_URL}
