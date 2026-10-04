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
would bury everything else), except airborne command posts and hijack-code flights (US_KEEP). Each aircraft's reported positions are kept for
TRACK_HOURS (`state["flights"]`); the map shows aircraft seen in the last LIVE_MINUTES at their last
reported position with its time, never a position estimated between reports.

Movements: when MIN_GROUP aircraft of one role take off from, or land at, a watched base
(`pipeline/config/flights.yaml`, within BASE_KM, below LOW_FT) within GROUP_HOURS, the agent writes a
plain report of what the transponders showed ("3 KC-135 tankers took off from Al Udeid Air Base
between 09:12 and 10:40 UTC") into the extraction queue, credited to "Flight tracking (adsb.lol)",
its own unaligned source group. A group that grows by another MIN_GROUP is reported again. The
model then files it like any other report (a deployment, or forces moved), so these use the
ordinary extraction budget: a few posts a day.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta

from common import UTC, haversine_km, health_fail, health_ok, iso, log, make_item, parse_time

API = "https://api.adsb.lol/v2/mil"
HIJACK = "https://api.adsb.lol/v2/sqk/7500"
MAP_LINK = "https://adsb.lol/?icao={hexes}"
SOURCE = {"name": "Flight tracking (adsb.lol)", "kind": "osint", "group": "adsb", "weight": 3, "prefilter": False}
SOURCE_ID = "flights"
ATTRIBUTION = "Flight data: adsb.lol contributors, open data under the ODbL 1.0 licence"
LICENSE_URL = "https://opendatacommons.org/licenses/odbl/1-0/"

TRACK_HOURS = 12          # positions kept per aircraft
LIVE_MINUTES = 90         # shown on the map while seen this recently
SORTIE_GAP = timedelta(hours=2)   # a longer silence starts a new flight
MAX_POSITION_AGE = 120    # seconds: older positions in a reply are not used
MIN_STEP_KM = 3           # a new position is kept when it moved this far (or 10 minutes passed)
MAX_PUBLISHED = 120
TRACK_POINTS = 40         # positions published per aircraft (thinned)
BASE_KM = 40              # "at" a watched base
LOW_FT = 12000            # below this near a base: taking off or landing
GROUP_HOURS = 3
MIN_GROUP = {"bomber": 2, "command": 1, "government": 1}   # other roles: DEFAULT_GROUP
DEFAULT_GROUP = 3

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
ROLE_WORDS = {"bomber": "bombers", "tanker": "tankers", "surveillance": "surveillance aircraft", "airlift": "transport aircraft",
              "command": "airborne command posts", "government": "government aircraft"}


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


def update(state: dict, session, health: dict, now: datetime, bases: list[dict]) -> list[dict]:
    """Read the current military aircraft and the hijack code, keep the notable ones' positions,
    and return reports of group departures and arrivals at watched bases."""
    st = state.setdefault("flights", {"aircraft": {}, "moves": {}})
    try:
        mil, stamp = _fetch(session, API)
    except Exception as exc:  # noqa: BLE001 - positions stay as they were; tried again next run
        log(f"[flights] adsb.lol failed: {exc}")
        health[SOURCE_ID] = health_fail(SOURCE["name"], "adsb", exc, health.get(SOURCE_ID))
        return []
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
    items = movements(st, now, bases)
    latest = datetime.fromtimestamp(stamp, UTC)
    health[SOURCE_ID] = health_ok(SOURCE["name"], "adsb", latest, kept, health.get(SOURCE_ID))
    log(f"[flights] {len(mil)} military aircraft listed, {len(hijack)} on the hijack code; {kept} notable kept, "
        f"{len(st['aircraft'])} tracked; {len(items)} group movement reports")
    return items


def _sortie(pts: list) -> list:
    """The current flight: positions since the last silence longer than SORTIE_GAP."""
    out = []
    for p in pts:
        if out and p[3] - out[-1][3] > SORTIE_GAP.total_seconds():
            out = []
        out.append(p)
    return out


def _nearest_base(lat: float, lon: float, bases: list[dict]) -> dict | None:
    best, best_km = None, BASE_KM
    for b in bases:
        d = haversine_km(lat, lon, b["lat"], b["lon"])
        if d <= best_km:
            best, best_km = b, d
    return best


def _low(p) -> bool:
    return p[2] is not None and p[2] < LOW_FT


def movements(st: dict, now: datetime, bases: list[dict]) -> list[dict]:
    """Group departures and arrivals at watched bases, as reports for the extraction queue."""
    seen: dict[tuple, list] = {}
    for hexid, rec in st["aircraft"].items():
        if rec.get("role") in (None, "emergency"):
            continue
        pts = _sortie(rec["pts"])
        if len(pts) < 2:
            continue
        first, last = pts[0], pts[-1]
        # took off: first seen low at a base, and later seen away from it
        b = _low(first) and _nearest_base(first[0], first[1], bases)
        if b and any(haversine_km(p[0], p[1], b["lat"], b["lon"]) > BASE_KM for p in pts[1:]):
            seen.setdefault((b["name"], rec["role"], "took off from"), []).append((first[3], hexid, rec, last))
        # landed: last seen low at a base, after being seen away from it
        b = _low(last) and _nearest_base(last[0], last[1], bases)
        if b and any(haversine_km(p[0], p[1], b["lat"], b["lon"]) > BASE_KM for p in pts[:-1]):
            seen.setdefault((b["name"], rec["role"], "landed at"), []).append((last[3], hexid, rec, last))
    items, moves = [], st.setdefault("moves", {})
    by_name = {b["name"]: b for b in bases}
    for (base, role, verb), group in seen.items():
        group.sort()
        # aircraft within GROUP_HOURS of the latest one
        group = [g for g in group if group[-1][0] - g[0] <= GROUP_HOURS * 3600]
        need = MIN_GROUP.get(role, DEFAULT_GROUP)
        key = f"{base}|{role}|{verb}|{group[0][0] // (GROUP_HOURS * 3600)}"
        reported = moves.get(key, {}).get("n", 0)
        if len(group) < need or len(group) < reported + need:
            continue
        moves[key] = {"n": len(group), "at": iso(now)}
        items.append(_report(by_name[base], role, verb, group))
    cutoff = iso(now - timedelta(days=2))
    st["moves"] = {k: v for k, v in moves.items() if v.get("at", "") >= cutoff}
    return items


def _hhmm(ts: int) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%H:%M")


def _report(base: dict, role: str, verb: str, group: list) -> dict:
    labels = sorted({g[2]["label"] for g in group})
    kind = labels[0].split(" (")[0] if len(labels) == 1 else None
    what = f"{len(group)} {kind + 's' if kind and len(group) > 1 else kind or ROLE_WORDS.get(role, 'military aircraft')}"
    ops = sorted({g[2]["op"] for g in group if g[2].get("op")})
    signs = ", ".join(sorted(g[2]["callsign"] or g[2]["reg"] or g[1] for g in group))
    t0, t1 = group[0][0], group[-1][0]
    when = f"at {_hhmm(t0)} UTC" if t0 == t1 else f"between {_hhmm(t0)} and {_hhmm(t1)} UTC"
    text = (f"Flight tracking (ADS-B transponder data, adsb.lol): {what} {verb} {base['name']}, {base['country_name']}, {when}"
            f"{' (' + ', '.join(ops) + ')' if ops else ''}. Callsigns or registrations: {signs}.")
    if verb == "took off from":
        far = [g[3] for g in group if haversine_km(g[3][0], g[3][1], base["lat"], base["lon"]) > BASE_KM]
        if far:
            text += f" Last reported {len(far)} of them {round(sum(haversine_km(p[0], p[1], base['lat'], base['lon']) for p in far) / len(far))} km from the base on average."
    url = MAP_LINK.format(hexes=",".join(g[1] for g in group))
    when_dt = datetime.fromtimestamp(t1, UTC)
    return make_item(SOURCE, "adsb", SOURCE_ID, url, text, when_dt, uid=f"{base['name']}|{role}|{verb}|{t0}|{len(group)}")


def public(state: dict, now: datetime) -> dict:
    """Aircraft seen in the last LIVE_MINUTES, most notable first, with their current flight's track."""
    st = state.get("flights") or {}
    live = now.timestamp() - LIVE_MINUTES * 60
    out = []
    for hexid, rec in (st.get("aircraft") or {}).items():
        pts = _sortie(rec.get("pts") or [])
        if not pts or pts[-1][3] < live:
            continue
        step = max(1, len(pts) // TRACK_POINTS + (1 if len(pts) % TRACK_POINTS else 0))
        track = pts[::step]
        if track[-1] is not pts[-1]:
            track.append(pts[-1])
        lat, lon, alt, ts = pts[-1]
        out.append({"hex": hexid, "callsign": rec.get("callsign") or "", "reg": rec.get("reg") or "", "type": rec.get("type") or "",
                    "role": rec.get("role"), "label": rec.get("label"), "op": rec.get("op"),
                    "lat": lat, "lon": lon, "alt": alt, "gs": rec.get("gs"), "heading": rec.get("heading"),
                    "seen": iso(datetime.fromtimestamp(ts, UTC)), "since": iso(datetime.fromtimestamp(pts[0][3], UTC)),
                    "track": [[p[0], p[1]] for p in track]})
    out.sort(key=lambda f: (-IMPORTANCE.get(f["role"], 0), -parse_time(f["seen"]).timestamp()))
    out = out[:MAX_PUBLISHED]
    checked = parse_time(st.get("checked"))
    return {"as_of": iso(checked) if checked else None, "attribution": ATTRIBUTION, "license_url": LICENSE_URL,
            "link": "https://adsb.lol/", "aircraft": out}
