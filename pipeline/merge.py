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

Alerts: real-time warnings that drones or missiles are in flight ("a drone is heading toward
Poltava") with nothing reported hit. An air force can post dozens a night, so all alerts about
one country on one day (same 09:00 UTC days) become a single event listing the places named.
They never join an attack wave, whose locations are places actually hit. Over NATO's eastern
flank every airspace alert is news in itself, so those stay separate events.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

from common import haversine_km, iso, log, parse_time, short_hash
from geo import sea_exact
from sources.gdelt import CellIndex, news_domains_near

FAMILY = {
    "airstrike": "strike", "missile_drone": "strike", "air_defense": "strike", "explosion": "strike",
    "artillery": "ground", "ground": "ground", "territory": "ground",
    "naval": "naval", "deployment": "deployment", "diplomacy": "diplomacy", "ceasefire": "diplomacy",
    "hybrid": "hybrid", "incursion": "incursion", "arms_transfer": "transfer", "legal": "legal",
    "production": "production",
}
RADIUS_KM = {"strike": 30, "ground": 30, "naval": 150, "deployment": 120, "diplomacy": 400,
             "hybrid": 50, "incursion": 150, "transfer": 0, "legal": 400, "production": 100}
TRANSFER_WINDOW = timedelta(hours=72)  # repeated flights or sailings on one route become one "bridge"
ROUTE_KM = 250  # the same route: both ends within this distance (a base and the town beside it)
WINDOW = timedelta(hours=18)  # measured from when the event first happened, never from later reports
BUILDUP_OVERLAP = 0.25  # share of words two deployment summaries need in common (see _same_buildup)
# Families whose reports are often pinned to a whole country or sea ("England", "South China Sea")
# while others name the exact spot; see _same_story. Strikes and fighting are left out: their
# country-level reports are grouped as attack waves or alerts instead.
LOOSE_FAMILIES = {"deployment", "hybrid", "naval", "incursion"}
STORY_OVERLAP = 0.25
# Short summaries can pass a share test on one word: "Four personnel injured in a mishap aboard the
# carrier USS Dwight D. Eisenhower" and "An Iranian aircraft is seized in Istanbul" share only
# "aircraft", a quarter of the shorter one, and the carrier ended up pinned to Istanbul.
MIN_SHARED = 2
STORY_MAX_KM = 2500
STRIKE_FOLD_OVERLAP = 0.6  # stored strikes/fighting at one place fold only with wording this close
# Meetings, visits, statements and legal steps. Their theater is a judgement call (a German
# minister at the ICC was filed under both the NATO flank and Ukraine), and a statement can be
# pinned to the capital that made it or the city it is about, so these match across theaters,
# across the two types, and at any distance when the parties are the same and the wording close.
TALKS = {"diplomacy", "legal"}
TALKS_FAR_OVERLAP = 0.6
TALKS_SUBSET_OVERLAP = 0.55  # parties one within the other ([PK] and [PK, SA, YE]): wording this close
TALKS_FOLD_OVERLAP = 0.5  # stored talks fold only with wording this close (see consolidate)
_SEA = re.compile(r"\b(?:sea|ocean|gulf|strait|straits|bay|channel)\b", re.IGNORECASE)
_REGIONS = {"england", "scotland", "wales", "northern ireland", "uk", "britain", "great britain", "us", "usa",
            "united states", "america", "europe", "middle east", "gaza strip", "west bank", "sahel",
            "horn of africa", "indo-pacific", "caribbean", "baltic", "black sea region", "persian gulf region"}


def _country_names() -> set[str]:
    try:
        topo = json.loads((Path(__file__).resolve().parents[1] / "site/assets/countries-110m.json").read_text(encoding="utf-8"))
        return {str(g["properties"]["name"]).lower() for g in topo["objects"]["countries"]["geometries"]}
    except (OSError, ValueError, KeyError):
        return set()


_BROAD_NAMES = _REGIONS | _country_names()

WAVE_TYPES = {"missile_drone", "air_defense", "explosion"}
TARGET_MERGE_KM = 15
MAX_TARGETS = 60
MAX_REPORTS = 80
ADJECTIVE = {"RU": "Russian", "UA": "Ukrainian", "IR": "Iranian", "IL": "Israeli", "YE": "Houthi",
             "LB": "Hezbollah", "US": "US", "BY": "Belarusian", "PK": "Pakistani", "IN": "Indian"}

ALERT_TYPES = {"missile_drone", "air_defense", "incursion"}
# Backstop for Ukraine alerts the model doesn't flag, and for events stored before alerts were
# grouped: the summary describes drones on the move and nothing being hit.
_MOVING = re.compile(r"\b(?:heading|headed|moving|flying|tracked|tracks|tracking|approaching|passing|"
                     r"toward|towards|targeting|course|threats?|alerts?|warns?|warning)\b", re.IGNORECASE)
_HIT = re.compile(r"\b(?:struck|hits?|hitting|damag\w*|destroy\w*|kill\w*|injur\w*|wound\w*|dead|deaths?|"
                  r"casualt\w*|intercept\w*|shot down|shoot\w* down|downed|explo\w*|blasts?|fire\w*|impacts?|"
                  r"debris)\b|\b(?:strikes?|attacks?|attacked)\b(?!\s+(?:drones?|uavs?))", re.IGNORECASE)


def wave_day(t: str) -> str:
    return (parse_time(t) - timedelta(hours=9)).strftime("%Y-%m-%d")


_MINE_BLAST = re.compile(r"\b(?:land[ -]?mines?|mine\s+(?:blast|explosion))\b", re.IGNORECASE)
_AIR_WEAPON = re.compile(r"\b(?:missiles?|drones?|uavs?|shaheds?|rockets?)\b", re.IGNORECASE)


def _ground_mine(c: dict) -> bool:
    texts = [c.get("summary") or ""] + [r.get("summary") or "" for r in c.get("reports") or []]
    return any(_MINE_BLAST.search(t) for t in texts) and not any(_AIR_WEAPON.search(t) for t in texts)


def mine_incidents(events: list[dict]) -> None:
    """Repair landmine blasts previously filed as missile/drone waves, preserving identity."""
    for e in events:
        if e.get("wave") and _ground_mine(e):
            e["type"] = "explosion"
            for field in ("wave", "wave_key", "targets", "launched", "intercepted"):
                e.pop(field, None)


def _is_wave(c: dict) -> bool:
    # a launch into a named sea is a wave too, whatever country (or none) the report gives it
    return (c["type"] in WAVE_TYPES and not _ground_mine(c) and bool(c.get("attacker")) and bool(c.get("country") or sea_exact(c.get("place")))
            and c["attacker"] != c.get("country"))


def _same_target(e: dict, cand: dict) -> bool:
    """A wave's target country, or for launches into the sea, the same sea: North Korean missiles
    "toward the Sea of Japan" came in with country JP, and the same launch "into the East Sea"
    from Seoul's outlets with KR or none."""
    if e.get("country") and e.get("country") == cand.get("country"):
        return True
    sea = sea_exact(cand.get("place"))
    return bool(sea) and any(sea_exact(t.get("place")) == sea for t in (e.get("targets") or []) + [e])


def _reads_like_alert(summary: str | None) -> bool:
    return bool(_MOVING.search(summary or "")) and not _HIT.search(summary or "")


def _is_alert(c: dict) -> bool:
    if (c["type"] not in ALERT_TYPES or c["theater"] == "nato_east"
            or c.get("killed") is not None or c.get("injured") is not None):
        return False
    if c.get("alert"):
        return True
    return (c["theater"] == "ukraine" and c.get("country") == "UA" and c.get("attacker") in (None, "RU")
            and _reads_like_alert(c.get("summary")))


def _placed(end) -> bool:
    return isinstance(end, dict) and end.get("lat") is not None and end.get("lon") is not None


def _same_route(e: dict, cand: dict) -> bool:
    """Both ends agree where both reports name them. A country moving its own forces (supplier =
    recipient) must also name the same ends, or be pinned in the same place: "US forces moved" is
    every American deployment, and on 2026-10-04 one such event had absorbed 23 reports, from the
    Iraq withdrawal to B-1 bombers leaving RAF Fairford, under a date that kept them all off the map."""
    et, t = e.get("transfer") or {}, cand.get("transfer") or {}
    own = bool(t.get("supplier")) and t.get("supplier") == t.get("recipient")
    named = 0
    for end in ("from", "to"):
        a, b = et.get(end), t.get(end)
        if _placed(a) and _placed(b):
            if haversine_km(a["lat"], a["lon"], b["lat"], b["lon"]) > ROUTE_KM:
                return False
            named += 1
        elif own and (_placed(a) or _placed(b)):
            return False  # one names an end the other doesn't: not shown to be the same move
    if own and not named:
        return haversine_km(e["lat"], e["lon"], cand["lat"], cand["lon"]) <= ROUTE_KM
    return True


def _find_transfer(events: list[dict], cand: dict) -> dict | None:
    """The same supplier, recipient and kind, on the same route, within TRANSFER_WINDOW of when the
    event began (never of its latest report, which let one event grow for days)."""
    t = cand.get("transfer") or {}
    ct = parse_time(cand["time"])
    for e in events:
        et = e.get("transfer") or {}
        if (e["type"] == "arms_transfer" and et.get("supplier") == t.get("supplier")
                and et.get("recipient") == t.get("recipient") and et.get("kind") == t.get("kind")
                and abs(ct - parse_time(e["time"])) <= TRANSFER_WINDOW and _same_route(e, cand)):
            return e
    return None


TRANSFER_SPLIT_VERSION = 1


def split_overgrown_transfers(events: list[dict], state: dict, now) -> list[dict]:
    """Once (per TRANSFER_SPLIT_VERSION): a stored arms transfer that grew past TRANSFER_WINDOW under
    the old rule keeps its first report; the others are read again by the model, each from its own
    summary, credited to its original source, so the fixed rule files them. Returns the items for
    the extraction queue."""
    if state.get("transfer_split_version", 0) >= TRANSFER_SPLIT_VERSION:
        return []
    state["transfer_split_version"] = TRANSFER_SPLIT_VERSION
    items = []
    for e in events:
        if e.get("type") != "arms_transfer" or not e.get("reports"):
            continue
        start = parse_time(e["time"])
        if all(parse_time(r["time"]) - start <= TRANSFER_WINDOW for r in e["reports"]):
            continue
        # grown under the old rule, so even its early reports may be other moves: keep the first,
        # and let the fixed rule sort the rest
        ordered = sorted(e["reports"], key=lambda r: r["time"])
        keep, out = ordered[:1], ordered[1:]
        e["reports"] = keep
        e["updated"] = max(r["time"] for r in keep)
        e.pop("headline", None)
        for r in out:
            items.append({"id": short_hash("resplit", r.get("url"), r.get("summary")), "source_id": r.get("source_id") or "resplit",
                          "source": r.get("source"), "platform": r.get("platform"), "kind": r.get("kind", "news"),
                          "side": r.get("side"), "group": r.get("group") or r.get("source"), "weight": int(r.get("weight", 2)),
                          "prefilter": False, "url": r.get("url"), "text": r.get("summary") or "", "time": r["time"],
                          "max_age_h": 24 * 30})
    if items:
        log(f"[merge] {len(items)} reports taken out of arms transfers they joined days late, to be read again")
    return items


_WORD_STOP = set("""a an the of in on at to for and or by with as is are was were be been its it this that
from after over into amid near during against about says said say claims claimed claim reports reported
report according officials official state states stated new following""".split())


# Spellings of one name that outlets use interchangeably ("the Mecca pact", "the Makkah pact").
_SPELLINGS = {"makkah": "mecca"}


def _words(text: str) -> set[str]:
    words = (w.replace("-", "") for w in re.findall(r"[a-z][a-z'-]+", (text or "").lower()) if w not in _WORD_STOP)
    return {_SPELLINGS.get(w, w).rstrip("s") for w in words if w}


def _broad(e: dict) -> bool:
    """Pinned to a whole country, region, or sea rather than a spot."""
    place = re.sub(r"\s*\(.*?\)", "", str(e.get("place") or "")).strip().lower()
    return bool(e.get("approx")) or not place or place in _BROAD_NAMES or bool(_SEA.search(place))


def _same_story(e: dict, cand: dict) -> bool:
    """One of the two is pinned to a whole country, region, or sea; they are in the same country
    (a sea counts for any), the acting side doesn't conflict, and the wording is similar. For
    hybrid attacks the suspected culprit often differs between reports, so it is not compared."""
    if not (_broad(e) or _broad(cand)):
        return False
    if haversine_km(e["lat"], e["lon"], cand["lat"], cand["lon"]) > STORY_MAX_KM:
        return False
    a, b = e.get("country"), cand.get("country")
    sea = any(_SEA.search(str(x.get("place") or "")) for x in (e, cand) if _broad(x))
    if a and b and a != b and not sea:
        return False
    x, y = e.get("attacker"), cand.get("attacker")
    if FAMILY.get(cand["type"]) != "hybrid" and x and y and x != y:
        return False
    return _similar(e, cand)


def _same_broad_hit(e: dict, cand: dict) -> bool:
    """Strikes and fighting: a report pinned only to a whole country or region ("Fighting in Ethiopia
    intensifies") is the same story as one pinned to a spot in that country (the same outlet's other
    article, pinned to Addis Ababa) when the wording matches closely. Country-level reports hold many
    separate incidents, so close wording is required, not just similar."""
    if not (_broad(e) or _broad(cand)) or not e.get("country") or e.get("country") != cand.get("country"):
        return False
    if haversine_km(e["lat"], e["lon"], cand["lat"], cand["lon"]) > STORY_MAX_KM:
        return False
    x, y = e.get("attacker"), cand.get("attacker")
    if x and y and x != y:
        return False
    # The same template can describe different places ("Russian forces took control of Maryino" /
    # "... of Petropavlivka and Lozova"): the names in the shorter summary must be in the other.
    a, b = sorted((_names(e["summary"]), _names(cand["summary"])), key=len)
    return a <= b and _similar(e, cand, STRIKE_FOLD_OVERLAP)


def _names(text: str) -> set[str]:
    """Capitalized words other than the first ("Maryino", "Sumy"), lowercased, without a final s."""
    words = re.findall(r"[A-Za-z][\w'-]*", text or "")
    return {w.lower().rstrip("s").removesuffix("'") for w in words[1:] if w[0].isupper()}


def _similar(e: dict, cand: dict, threshold: float = 0.0) -> bool:
    """Similar wording, not counting the place names themselves ("Strait of Hormuz" is in every
    report from there)."""
    places = _words(" ".join(str(x.get("place") or "") for x in (e, cand)))
    a, b = _words(e["summary"]) - places, _words(cand["summary"]) - places
    return (bool(a and b) and len(a & b) >= MIN_SHARED
            and len(a & b) / min(len(a), len(b)) >= (threshold or STORY_OVERLAP))


def _overlap(a: str, b: str) -> float:
    wa, wb = _words(a), _words(b)
    if not (wa and wb) or len(wa & wb) < MIN_SHARED:
        return 0.0
    return len(wa & wb) / min(len(wa), len(wb))


def _same_talks(e: dict, cand: dict) -> bool:
    """Diplomacy and legal steps merge by who takes part, not only by place: separate talks
    often happen in the same region on the same day (Netanyahu in Abu Dhabi, an Iranian
    proposal on Hormuz)."""
    a, b = set(e.get("parties") or []), set(cand.get("parties") or [])
    if a and b:
        if len(a & b) >= 2:
            return True
        # the same parties, or one list within the other with closer wording: "Pakistan announces Mecca
        # pact talks on the Houthis" came in with [PK] from one outlet and [PK, SA, YE] from another
        # (a Saudi cabinet meeting, [SA], and a Saudi-UAE meeting, [AE, SA], shared half their words)
        if a == b:
            return _overlap(e["summary"], cand["summary"]) >= 0.4
        return (a <= b or b <= a) and _overlap(e["summary"], cand["summary"]) >= TALKS_SUBSET_OVERLAP
    return _overlap(e["summary"], cand["summary"]) >= 0.5


def _same_statement(e: dict, cand: dict) -> bool:
    """The same parties and closely matching wording: one meeting or statement pinned to different
    cities (the capital that spoke, the city it is about, the venue)."""
    a, b = set(e.get("parties") or []), set(cand.get("parties") or [])
    return bool(a) and a == b and _overlap(e["summary"], cand["summary"]) >= TALKS_FAR_OVERLAP


def _same_buildup(e: dict, cand: dict) -> bool:
    """Reports of one country's forces preparing around another country ("the US military is laying
    groundwork for action around Cuba") are placed wherever each report points: the country's
    centre, its capital, or the capital of the country acting. The same acting country, the same
    country concerned, and similar wording make them one story, however far apart the pins are."""
    return (bool(e.get("attacker")) and e.get("attacker") == cand.get("attacker")
            and bool(e.get("country")) and e.get("country") == cand.get("country")
            and _overlap(e["summary"], cand["summary"]) >= BUILDUP_OVERLAP)


def _find_match(events: list[dict], cand: dict) -> dict | None:
    fam = FAMILY.get(cand["type"], "strike")
    if fam == "transfer":
        return _find_transfer(events, cand) if cand.get("transfer") else None
    ct = parse_time(cand["time"])
    talks = fam in TALKS
    best, best_d = None, float("inf")
    for e in events:
        if e.get("wave") or e.get("alert"):
            continue
        if talks:
            if FAMILY.get(e["type"]) not in TALKS or not _same_talks(e, cand):
                continue
        elif e["theater"] != cand["theater"] or FAMILY.get(e["type"], "strike") != fam:
            continue
        if abs(ct - parse_time(e["time"])) > WINDOW:
            continue
        d = haversine_km(e["lat"], e["lon"], cand["lat"], cand["lon"])
        radius = RADIUS_KM[fam] * (2 if (e.get("approx") or cand["approx"]) else 1)
        if d <= radius and fam in LOOSE_FAMILIES and _broad(e) and _broad(cand) and not _similar(e, cand):
            continue  # two reports that only share a region or sea pin need similar wording too
        if d > radius:
            far = ((fam == "deployment" and _same_buildup(e, cand)) or (fam in LOOSE_FAMILIES and _same_story(e, cand))
                   or (talks and _same_statement(e, cand))
                   or (fam in ("strike", "ground") and _same_broad_hit(e, cand)))
            if not far:
                continue
            d += 100_000  # a match, but ranked after any event that is actually nearby
        if d < best_d:
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
            # the latest report naming the place: lists of an alert group's places go newest first
            t["last"] = max(t.get("last") or t["time"], cand.get("last") or cand["time"])
            t["killed"] = _max_or_none(t.get("killed"), cand["killed"])
            t["injured"] = _max_or_none(t.get("injured"), cand["injured"])
            return
    if len(wave["targets"]) < MAX_TARGETS:
        wave["targets"].append({
            "place": cand["place"], "lat": cand["lat"], "lon": cand["lon"], "reports": 1,
            "severity": cand["severity"], "time": cand["time"], "last": cand.get("last") or cand["time"],
            "killed": cand["killed"], "injured": cand["injured"],
        })


def _wave_for(events: list[dict], cand: dict, attacker: str | None) -> dict | None:
    """The attack wave a report belongs to: same attacker and target country, within WINDOW of
    when the wave began (a fixed day boundary used to split one night's attack in two)."""
    ct = parse_time(cand["time"])
    best, best_dt = None, WINDOW
    for e in events:
        if (e.get("wave") and e["theater"] == cand["theater"] and _same_target(e, cand)
                and (attacker is None or e.get("attacker") == attacker)):
            dt = abs(ct - parse_time(e["time"]))
            if dt <= best_dt:
                best, best_dt = e, dt
    return best


def _hit_in_wave(events: list[dict], cand: dict) -> dict | None:
    """A strike report that names no attacker ("drones hit Erbil") joins an attack wave on the
    same country when it hit one of the wave's places."""
    if cand["type"] not in WAVE_TYPES or _ground_mine(cand) or not cand.get("country") or cand.get("attacker") or cand.get("approx"):
        return None
    wave = _wave_for(events, cand, None)
    if wave and any(haversine_km(t["lat"], t["lon"], cand["lat"], cand["lon"]) <= TARGET_MERGE_KM
                    for t in wave["targets"] + [{"lat": wave["lat"], "lon": wave["lon"]}]):
        return wave
    return None


def _merge_wave(events: list[dict], cand: dict, wave: dict | None = None) -> None:
    key = "|".join([cand["theater"], cand.get("attacker") or "", cand.get("country") or "", wave_day(cand["time"])])
    wave = wave or _wave_for(events, cand, cand.get("attacker"))
    rep = cand["report"]
    if wave is None:
        wave = {
            "id": short_hash("wave", key) if not any(e.get("wave_key") == key for e in events)
            else short_hash("wave", key, cand["time"]), "wave": True, "wave_key": key,
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
    wave["updated"] = max(wave["updated"], cand["time"], rep["time"])
    wave["severity"] = max(wave["severity"], cand["severity"])
    wave["launched"] = _max_or_none(wave.get("launched"), cand.get("launched"))
    wave["intercepted"] = _max_or_none(wave.get("intercepted"), cand.get("intercepted"))
    _add_target(wave, cand)
    _add_origins(wave, cand.get("origins"))


def _merge_alert(events: list[dict], cand: dict) -> None:
    key = "|".join(["alert", cand["theater"], cand.get("country") or "", wave_day(cand["time"])])
    group = next((e for e in events if e.get("alert_key") == key), None)
    rep = cand["report"]
    if group is None:
        group = {
            "id": short_hash("alert", key), "alert": True, "alert_key": key,
            "theater": cand["theater"], "type": "missile_drone",
            "attacker": None, "country": cand.get("country"),  # alerts never add attribution
            "summary": cand["summary"], "place": cand["place"],
            "lat": cand["lat"], "lon": cand["lon"], "approx": cand["approx"],
            "origins": [], "targets": [], "severity": 1,
            "killed": None, "injured": None, "launched": None, "intercepted": None,
            "time": cand["time"], "updated": cand["time"], "reports": [],
        }
        events.append(group)
    if any(r["url"] == rep["url"] for r in group["reports"]):
        return
    group["reports"].append(rep)
    group["time"] = min(group["time"], cand["time"])
    group["updated"] = max(group["updated"], cand.get("updated") or cand["time"])
    _add_target(group, cand)


def _fold_stored_alerts(events: list[dict]) -> list[dict]:
    """Events stored before alerts were grouped: fold each "drone heading toward X" warning
    into its day's alert group. Every stored event is checked once."""
    folded = set()
    for e in list(events):
        if "alert" in e or e.get("wave"):
            continue
        if _is_alert(e) and all(_reads_like_alert(r.get("summary")) for r in e["reports"]):
            for r in e["reports"]:
                _merge_alert(events, {**e, "report": r})
            folded.add(id(e))
        else:
            e["alert"] = False
    return [e for e in events if id(e) not in folded]


# Deployments into another country are drawn as supply routes (the owner, 2026-10-07: "Deployments
# between countries (especially allied ones) should always be supply route"): the model now files
# them as the deploying country moving its own forces (arms_transfer, supplier = recipient; see
# extract.TYPES). Until then port calls, visits and exercises abroad were turned into markers here
# (own_force_visits, removed). Deployments stored under the old rule are read again once.
REREAD_DEPLOYMENTS_VERSION = 1
REREAD_DAYS = 3


def reread_foreign_deployments(events: list[dict], state: dict, now) -> tuple[list[dict], list[dict]]:
    """Once (per REREAD_DEPLOYMENTS_VERSION): recent deployments that involve a second country (the
    acting country differs from where they are, or another country is a party) are taken off the
    map and their reports read again by the model, each from its own summary, credited to its
    original source, so the new rule files them. Returns (events, items for the extraction queue)."""
    if state.get("reread_deployments_version", 0) >= REREAD_DEPLOYMENTS_VERSION:
        return events, []
    state["reread_deployments_version"] = REREAD_DEPLOYMENTS_VERSION
    since = now - timedelta(days=REREAD_DAYS)
    keep, items, n = [], [], 0
    for e in events:
        dest = e.get("country")
        foreign = bool(dest) and ((e.get("attacker") and e["attacker"] != dest)
                                  or any(p and p != dest for p in e.get("parties") or []))
        if e.get("type") != "deployment" or e.get("alert") or not foreign or parse_time(e["time"]) < since:
            keep.append(e)
            continue
        n += 1
        for r in e.get("reports") or []:
            items.append({"id": short_hash("redeploy", r.get("url"), r.get("summary")), "source_id": r.get("source_id") or "redeploy",
                          "source": r.get("source"), "platform": r.get("platform"), "kind": r.get("kind", "news"),
                          "side": r.get("side"), "group": r.get("group") or r.get("source"), "weight": int(r.get("weight", 2)),
                          "prefilter": False, "url": r.get("url"), "text": r.get("summary") or "", "time": r["time"],
                          "max_age_h": 24 * 30})
    if n:
        log(f"[merge] {n} recent deployments involving another country ({len(items)} reports) to be read again under the route rule")
    return keep, items


# An aircraft carrier's own movement is shown by its own track (fleet.py), not as a supply route as
# well (the owner, 2026-10-07: "USS Carrier has a supply route, don't need that in addition to its
# path"): "A US aircraft carrier arrived in Thailand" and the Lincoln strike group "set to return to
# San Diego" were drawn as routes from "the Middle East".
def _carrier_re():
    from fleet import CARRIERS
    names = sorted({n.removeprefix("U.S.S. ") for n, _ in CARRIERS.values()}
                   | {n.removeprefix("U.S.S. ").split()[-1] for n, _ in CARRIERS.values()}, key=len, reverse=True)
    return re.compile(r"\b(?:aircraft carriers?|carrier strike groups?|carrier air wing)\b|\b(?:USS|U\.S\.S\.)\s+(?:"
                      + "|".join(re.escape(n) for n in names) + r")\b", re.I)


CARRIER_RE = _carrier_re()


def carrier_moves(events: list[dict]) -> int:
    """Turn a carrier's own move filed as a movement of a country's forces into a deployment where it went."""
    changed = 0
    for e in events:
        t = e.get("transfer") or {}
        if e.get("type") != "arms_transfer" or not t.get("supplier") or t.get("supplier") != t.get("recipient"):
            continue
        if not CARRIER_RE.search(f"{e.get('summary') or ''} {t.get('what') or ''}"):
            continue
        to = t.get("to") or {}
        e["type"], e["transfer"] = "deployment", None
        if to.get("lat") is not None and not to.get("region"):
            e.update(place=to.get("place") or e.get("place"), lat=to["lat"], lon=to["lon"])
        changed += 1
    if changed:
        log(f"[merge] {changed} aircraft carrier moves shown by the carrier's own track, not as supply routes")
    return changed


# Drone and missile attacks filed as airstrikes get no launch lines (2026-10-07: "Russian drone strikes
# in Ukraine not showing launch paths": "Ukraine's air defense intercepted 5 ballistic missiles and 117
# drones" was an airstrike). An airstrike whose words are about drones or missiles, and not about
# aircraft, glide bombs or front-line FPV drones, is a drone and missile attack.
DRONE_MISSILE_RE = re.compile(r"\b(?:drones?|missiles?|ballistic|cruise|shaheds?|gerans?|iskanders?|kinzhals?|kalibrs?|"
                              r"kh-\d+\w*|UAVs?|loitering munitions?)\b", re.I)
CREWED_RE = re.compile(r"\b(?:aircraft|jets?|warplanes?|planes?|helicopters?|bombers?|fighter|guided (?:aerial )?bombs?|"
                       r"glide bombs?|KABs?|FAB-\d+|FPV|drone operators?|drone units?|drone crews?)\b", re.I)


ATTACK_RE = re.compile(r"\b(?:attacks?|attacked|strikes?|struck|hit|launch\w*|fired|targeted|targeting)\b", re.I)


def drone_strikes(events: list[dict]) -> int:
    """Retype airstrikes, and explosions with a named attacker, that are drone or missile attacks
    (new reports before merging, so they join the attacker's wave, and stored events every run):
    "The Houthis claim fresh missile and drone attacks targeting Saudi Arabia" was an explosion."""
    changed = 0
    for e in events:
        text = e.get("summary") or ""
        kind = e.get("type")
        if kind == "explosion" and not (e.get("attacker") and ATTACK_RE.search(text)):
            continue
        if kind in ("airstrike", "explosion") and DRONE_MISSILE_RE.search(text) and not CREWED_RE.search(text):
            e["type"] = "missile_drone"
            changed += 1
    if changed:
        log(f"[merge] {changed} drone or missile attacks filed as airstrikes retyped")
    return changed


# A country's purchases, contracts, approvals or production with its own industry, filed as its
# own forces "moving" with nowhere to move between ("Taiwan announces plans to build more anti-ship
# missiles"): arms production, its own kind of event, not an arms or forces movement. (A deal
# between two countries is diplomacy; the prompt says so, but a stored one filed as a country's
# own forces can't be told apart here.)
PROCURE_RE = re.compile(r"\b(?:contracts?|procur\w*|purchas\w*|buys?|buying|orders?|ordered|production|produc\w+|"
                        r"build|builds|building|manufactur\w*|budget|approv\w*|receiv\w*)\b", re.I)


def own_procurement(events: list[dict]) -> int:
    """Turn a country's own purchases or production (no movement between named places) into production."""
    changed = 0
    for e in events:
        t = e.get("transfer") or {}
        if e.get("type") != "arms_transfer" or not t.get("supplier") or t.get("supplier") != t.get("recipient"):
            continue
        if t.get("from") and t.get("to"):
            continue  # a movement between two named places
        if t.get("to") and e.get("country") and e["country"] != t["supplier"]:
            continue  # forces sent into another country ("US troops receive orders for Poland")
        if not PROCURE_RE.search(f"{e.get('summary') or ''} {t.get('what') or ''}"):
            continue
        e["type"], e["transfer"] = "production", None
        e["country"] = e.get("country") or t["supplier"]
        changed += 1
        log(f"[merge] own purchase or production, filed as arms production: {e.get('summary', '')[:80]!r}")
    return changed


def _new_id(events: list[dict], url: str, summary: str) -> str:
    """An event's id comes from its first report's link; a second event from the same article
    (a roundup of two meetings) gets one from the link and its own summary."""
    eid = short_hash("event", url)
    return eid if all(e["id"] != eid for e in events) else short_hash("event", url, summary)


def unique_ids(events: list[dict]) -> int:
    """Stored events that share an id (made before _new_id) get their own: the one with the most
    reports keeps it. Returns how many were given a new id."""
    by_id: dict[str, list[dict]] = {}
    for e in events:
        by_id.setdefault(e["id"], []).append(e)
    changed = 0
    for eid, same in by_id.items():
        if len(same) < 2:
            continue
        same.sort(key=lambda e: -len(e.get("reports") or []))
        for e in same[1:]:
            first = (e.get("reports") or [{}])[0].get("url") or eid
            e["id"] = short_hash("event", first, e.get("summary"), e.get("time"))
            changed += 1
            log(f"[merge] shared id {eid}: {e.get('summary', '')[:60]!r} now {e['id']}")
    return changed


def merge(events: list[dict], candidates: list[dict]) -> list[dict]:
    events = _fold_stored_alerts(events)
    for cand in sorted(candidates, key=lambda c: c["time"]):
        if _is_alert(cand):
            _merge_alert(events, cand)
            continue
        if _is_wave(cand):
            _merge_wave(events, cand)
            continue
        wave = _hit_in_wave(events, cand)
        if wave is not None:
            _merge_wave(events, cand, wave)
            continue
        rep = cand["report"]
        match = _find_match(events, cand)
        if match is None:
            events.append({
                "id": _new_id(events, rep["url"], cand["summary"]), "alert": False,
                "theater": cand["theater"], "type": cand["type"], "summary": cand["summary"],
                "place": cand["place"], "country": cand["country"], "attacker": cand.get("attacker"),
                "lat": cand["lat"], "lon": cand["lon"], "approx": cand["approx"],
                "origins": list(cand.get("origins") or []),
                "parties": list(cand.get("parties") or []),
                "transfer": cand.get("transfer"), "legal_basis": cand.get("legal_basis"),
                "severity": cand["severity"],
                "killed": cand["killed"], "injured": cand["injured"],
                "time": cand["time"], "updated": max(cand["time"], rep["time"]), "reports": [rep],
            })
            continue
        if any(r["url"] == rep["url"] for r in match["reports"]):
            continue
        match["reports"].append(rep)
        match.pop("headline", None)  # a new report may change the facts (a rising death toll)
        _absorb(match, cand)
    return events


def _fold_into_wave(wave: dict, e: dict) -> None:
    """Add a stored event (another wave, or a strike that named no attacker) to a wave."""
    for t in e.get("targets") or ([] if e.get("approx") else [{**e, "reports": 1}]):
        _add_target(wave, {"approx": False, "place": t.get("place"), "lat": t["lat"], "lon": t["lon"],
                           "severity": t.get("severity") or e["severity"], "time": t.get("time") or e["time"],
                           "last": t.get("last") or t.get("time") or e.get("updated") or e["time"],
                           "killed": t.get("killed"), "injured": t.get("injured")})
    wave["launched"] = _max_or_none(wave.get("launched"), e.get("launched"))
    wave["intercepted"] = _max_or_none(wave.get("intercepted"), e.get("intercepted"))


def consolidate(events: list[dict], skip: set[str]) -> tuple[list[dict], list[dict]]:
    """Fold stored events into each other when today's matching rules join them: events stored
    before a rule existed, reports pinned to a whole country or sea next to ones naming the spot,
    and attack waves split by the old day boundary. Deployments, hybrid attacks, naval incidents,
    incursions, strikes and ground fighting, diplomacy and legal steps are folded, and waves and
    the unattributed strikes on their targets; arms transfers keep their own rules. The earliest event keeps its id.
    Events in `skip` (hidden by a correction) are left alone. Returns (events, the events folded away)."""
    kept: list[dict] = []
    folded: list[dict] = []
    for e in sorted(events, key=lambda e: e["time"]):
        pool = [k for k in kept if k["id"] not in skip]
        match = None
        if e["id"] in skip or e.get("alert"):
            pass
        elif e.get("wave"):
            match = _wave_for(pool, e, e.get("attacker"))
        elif FAMILY.get(e["type"]) in LOOSE_FAMILIES:
            match = _find_match(pool, e)
        elif FAMILY.get(e["type"]) in ("strike", "ground"):
            # same place, same kind, within the window AND closely matching wording: one incident
            # split when its reports arrived out of order. Place alone isn't enough: broad pins like
            # "Gaza" or "Sudan" hold many separate strikes.
            match = _hit_in_wave(pool, e)
            if match is None:
                m = _find_match(pool, e)
                match = m if m is not None and _similar(m, e, STRIKE_FOLD_OVERLAP) else None
        elif FAMILY.get(e["type"]) in TALKS:
            # the same parties and closely matching wording, whatever theater or type each was
            # filed under; looser pairs (a meeting and a reaction to it) are left to the dedupe check
            m = _find_match(pool, e)
            match = m if m is not None and _overlap(m["summary"], e["summary"]) >= TALKS_FOLD_OVERLAP else None
        else:
            match = None
        if match is None:
            kept.append(e)
            continue
        urls = {r["url"] for r in match["reports"]}
        match["reports"] += [r for r in e["reports"] if r["url"] not in urls]
        if match.get("wave"):
            _fold_into_wave(match, e)
            match["time"] = min(match["time"], e["time"])
            match["severity"] = max(match["severity"], e["severity"])
            _add_origins(match, e.get("origins"))
        else:
            _absorb(match, e)
        match["updated"] = max(match["updated"], e["updated"])
        folded.append(e)
    if folded:
        log(f"[merge] folded {len(folded)} events into the stories they belong to")
    gone = {id(e) for e in folded}
    return [e for e in events if id(e) not in gone], folded


# "North Korea fired a ballistic missile from Wonsan": the place a report gives is where the missile
# was launched, in the attacker's own country, not where it went.
LAUNCH_FROM_RE = re.compile(r"\b(?:fir(?:ed|es|ing)|launch(?:ed|es|ing)?|test-fir\w*)\b[^.]*\bfrom\b", re.I)


_DENIAL_RE = re.compile(r"\b(?:den(?:y|ies|ied)|rejects?|false|fake|no missiles?)\b", re.I)


def _launch_report(e: dict) -> bool:
    """A launch from the place the event is pinned to ("fired ... from Wonsan", pinned at Wonsan);
    not a denial ("a military source denies a missile was launched from Iran", pinned at Tehran)."""
    text = e.get("summary") or ""
    m = LAUNCH_FROM_RE.search(text)
    place = (e.get("place") or "").split(",")[0].strip().lower()
    return (e.get("type") == "missile_drone" and not e.get("wave") and not e.get("alert")
            and bool(e.get("attacker")) and e.get("country") == e.get("attacker") and bool(m) and len(place) >= 3
            and place in text[m.end():].lower() and not _DENIAL_RE.search(text))


def launch_sites(events: list[dict], skip: set[str]) -> tuple[list[dict], list[dict]]:
    """Fold launch reports ("fired a ballistic missile from Wonsan", pinned at Wonsan) into the
    attacker's attack wave within WINDOW, with their place as a named launch area: the wave's line
    is then drawn from there, not from an assumed one. Returns (events, the events folded away)."""
    folded = []
    for e in events:
        if e["id"] in skip or not _launch_report(e):
            continue
        t = parse_time(e["time"])
        waves = [w for w in events if w.get("wave") and w["id"] not in skip and w.get("attacker") == e["attacker"]
                 and w["theater"] == e["theater"] and abs(parse_time(w["time"]) - t) <= WINDOW]
        if not waves:
            continue
        w = min(waves, key=lambda w: abs(parse_time(w["time"]) - t))
        urls = {r["url"] for r in w["reports"]}
        w["reports"] += [r for r in e["reports"] if r["url"] not in urls]
        _add_origins(w, [{"place": e.get("place"), "lat": e["lat"], "lon": e["lon"]}] + list(e.get("origins") or []))
        w["updated"] = max(w["updated"], e["updated"])
        w["severity"] = max(w["severity"], e["severity"])
        folded.append(e)
    if folded:
        log(f"[merge] {len(folded)} launch reports folded into their attack waves as launch areas")
    gone = {id(e) for e in folded}
    return [e for e in events if id(e) not in gone], folded


def _absorb(match: dict, cand: dict) -> None:
    """Take what a matching report (or event) adds to an event."""
    match["time"] = min(match["time"], cand["time"])
    match["updated"] = max(match["updated"], cand.get("updated") or cand["time"],
                           (cand.get("report") or {}).get("time") or cand["time"])
    match["severity"] = max(match["severity"], cand["severity"])
    match["attacker"] = match.get("attacker") or cand.get("attacker")
    match["parties"] = match.get("parties") or list(cand.get("parties") or [])
    match["legal_basis"] = match.get("legal_basis") or cand.get("legal_basis")
    if match.get("transfer") and cand.get("transfer"):
        mt, ct_ = match["transfer"], cand["transfer"]
        mt["flights"] = _max_or_none(mt.get("flights"), ct_.get("flights"))
        mt["what"] = mt.get("what") or ct_.get("what")
        mt["value_usd"] = _max_or_none(mt.get("value_usd"), ct_.get("value_usd"))
        mt["from"] = mt.get("from") or ct_.get("from")
        mt["to"] = mt.get("to") or ct_.get("to")
        if ct_.get("via") and not mt.get("via"):
            mt["via"] = ct_["via"]
        if mt.get("mode") == "unspecified":
            mt["mode"] = ct_.get("mode")
    for k in ("killed", "injured"):
        match[k] = _max_or_none(match.get(k), cand.get(k))
    if _broad(match) and not _broad(cand):
        match.update(lat=cand["lat"], lon=cand["lon"], place=cand["place"], approx=False)
    _add_origins(match, cand.get("origins"))


# UK Maritime Trade Operations (and the Joint Maritime Information Center) are the primary
# authority on incidents involving merchant ships: their notices give the position and what was
# seen, and they keep "unknown projectile" unknown.
MARITIME_AUTHORITY = re.compile(r"\b(?:UKMTO|UK Maritime Trade Operations|JMIC)\b", re.IGNORECASE)


def _headline(event: dict) -> dict:
    """Prefer an unaligned source, then (for incidents at sea) a report citing UKMTO or JMIC, then
    higher weight, then the earliest report."""
    naval = FAMILY.get(event.get("type")) == "naval"
    return sorted(
        event["reports"],
        key=lambda r: (r.get("side") is not None,
                       naval and not MARITIME_AUTHORITY.search(r.get("summary") or ""),
                       -int(r.get("weight", 1)), r["time"]),
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
        e["summary"] = f"{lead}: strikes reported in {len(targets)} places, including {_join(names)}."
    else:
        e["summary"] = _headline(e)["summary"]


def _join(names: list[str]) -> str:
    return ", ".join(names[:-1]) + " and " + names[-1] if len(names) > 1 else "".join(names)


def _finish_alert(e: dict) -> None:
    """The marker sits on the place named most often; the summary counts the alerts."""
    targets = sorted(e["targets"], key=lambda t: (-t["reports"], t["time"]))
    e["targets"] = targets
    if targets:
        main = targets[0]
        e.update(place=main["place"], lat=main["lat"], lon=main["lon"], approx=False)
    e["alerts"] = len(e["reports"])
    names = [t["place"] for t in targets if t.get("place")][:3]
    lead = f"{e['alerts']} alerts about drones or missiles in flight"
    if e["alerts"] == 1:
        e["summary"] = _headline(e)["summary"]
    elif len(targets) > 1 and names:
        e["summary"] = f"{lead}, naming {len(targets)} places including {_join(names)}."
    elif names:
        e["summary"] = f"{lead}, naming {names[0]}."
    else:
        e["summary"] = f"{lead}."


def apply_status(events: list[dict], cells: list[dict]) -> None:
    index = CellIndex(cells)
    for e in events:
        if e.get("wave"):
            _finish_wave(e)
        elif e.get("alert"):
            _finish_alert(e)
        neutral = {r["group"] for r in e["reports"] if not r.get("side")}
        sided = {r["group"] for r in e["reports"] if r.get("side")}
        sides = {r["side"] for r in e["reports"] if r.get("side")}
        news = set()
        # News of violence near a warning's marker says nothing about the warning itself.
        if FAMILY.get(e["type"]) in ("strike", "ground") and not e.get("alert"):
            news = news_domains_near(index, e)
        e["news_nearby"] = len(news)
        if len(news) >= 3:
            neutral.add("gdelt")
        groups = neutral | sided
        # Google News results from outlets not listed in sources.yaml share one group, as do posts
        # the Bluesky patrol finds from accounts not listed. Much of it is syndicated copy (local
        # sites republishing Reuters, accounts reposting each other), so it only counts when no
        # listed source has reported the event, and together the weak groups count once.
        counted = groups - WEAK_GROUPS if groups - WEAK_GROUPS else set(sorted(groups)[:1])
        if len(counted) >= 2 and ((neutral & counted) or len(sides) >= 2):
            e["status"] = "corroborated"
        elif neutral:
            e["status"] = "unconfirmed"
        else:
            e["status"] = "claimed"
        e["sources_count"] = len(counted)
        if not e.get("wave") and not e.get("alert"):
            # a combined headline written when duplicates were folded (dedupe.py) leads until a new
            # report arrives; then the best single report leads again
            e["summary"] = e.get("headline") or _headline(e)["summary"]


WEAK_GROUPS = {"google-news", "bluesky-search"}

SUPPLY_RETENTION_DAYS = 30  # arms transfers are shown as 30-day flows


def prune(events: list[dict], now: datetime, retention_days: int, max_events: int) -> list[dict]:
    cutoff = iso(now - timedelta(days=retention_days))
    supply_cutoff = iso(now - timedelta(days=max(retention_days, SUPPLY_RETENTION_DAYS)))
    kept = [e for e in events if e["updated"] >= (supply_cutoff if e["type"] == "arms_transfer" else cutoff)]
    kept.sort(key=lambda e: e["updated"], reverse=True)
    return kept[:max_events]


def public_event(e: dict) -> dict:
    """Strip internal fields before publishing."""
    out = {k: v for k, v in e.items() if k not in ("reports", "us", "cn", "wave_key", "alert_key", "origin", "checked", "checks", "headline", "coverage", "dated")}
    if not e.get("alert"):
        out.pop("alert", None)
    if e.get("origin") and not e.get("origins"):
        out["origins"] = [e["origin"]]  # events stored before multi-origin support
    reports = sorted(e["reports"], key=lambda r: r["time"])[-MAX_REPORTS:]
    out["reports"] = [
        {k: r.get(k) for k in ("source", "platform", "kind", "side", "claim", "url", "time", "summary")}
        for r in reports
    ]
    return out


SPLIT_PROMPT = """You get news events from a conflict map. Each event is a list of report summaries that were grouped together by place and time, and some mix different developments (for example a leader's visit and a separate ceasefire proposal).

For each event, split its reports into groups so that each group describes one specific development: the same meeting, visit, statement, vote, proposal, or decision. Reports about the same development in different words, or with small differences in detail, belong in the same group. Use a single group when all reports are about one development.

Reply with one JSON object and nothing else:
{"events": [{"e": <event number>, "groups": [[<report numbers>], ...]}]}"""


def split_mixed_talks(events: list[dict], state: dict, ask, settings: dict, now) -> list[dict]:
    """One-time repair. Diplomacy and legal reports used to merge by place alone, so unrelated
    developments could share one event (a Netanyahu visit inside an Iranian proposal on Hormuz).
    The model groups each suspect event's reports by development; the group holding the event's
    headline stays, and the other reports go back to the model, from their own summaries, to
    become events of their own. If the model is unavailable, the repair waits for the next run."""
    if state.get("talks_split"):
        return events
    suspect = []
    for e in events:
        reps = e.get("reports", [])
        if FAMILY.get(e["type"]) not in ("diplomacy", "legal") or len(reps) < 2:
            continue
        if any(_overlap(a["summary"], b["summary"]) < 0.5 for a in reps[:40] for b in reps[:40]):
            suspect.append(e)
    if suspect:
        payload = [{"e": n, "reports": [{"r": k, "summary": r["summary"]} for k, r in enumerate(e["reports"][:40])]}
                   for n, e in enumerate(suspect)]
        reply = ask(SPLIT_PROMPT, json.dumps({"events": payload}, ensure_ascii=False), state, settings, now, max_tokens=4000,
                    purpose="split")
        if not isinstance(reply, dict) or not isinstance(reply.get("events"), list):
            log("[merge] mixed-talks repair: no model answer; will retry next run")
            return events
        requeue = []
        for res in reply["events"]:
            if not isinstance(res, dict) or not isinstance(res.get("e"), int) or not 0 <= res["e"] < len(suspect):
                continue
            e = suspect[res["e"]]
            n = min(40, len(e["reports"]))
            groups = res.get("groups")
            flat = [k for g in groups for k in g] if isinstance(groups, list) and all(isinstance(g, list) for g in groups) else []
            if len(groups or []) < 2 or not all(isinstance(k, int) for k in flat) or sorted(flat) != list(range(n)):
                continue  # one development, or an answer that doesn't account for every report
            parts = [[e["reports"][k] for k in g] for g in groups]
            kept = next((i for i, g in enumerate(parts) if any(r["summary"] == e["summary"] for r in g)),
                        max(range(len(parts)), key=lambda i: len(parts[i])))
            keep = parts[kept] + e["reports"][40:]
            for i, g in enumerate(parts):
                if i != kept:
                    for r in g:
                        requeue.append({
                            "id": short_hash("resplit", r["url"]), "source_id": r.get("source"), "source": r.get("source"),
                            "platform": r.get("platform"), "kind": r.get("kind"), "side": r.get("side"),
                            "group": r.get("group"), "weight": int(r.get("weight", 1)), "prefilter": False,
                            "url": r["url"], "text": r["summary"], "time": r["time"],
                        })
            e["reports"] = keep
            e["time"] = min(r["time"] for r in keep)
            e["updated"] = max(r["time"] for r in keep)
            log(f"[merge] split {e['summary'][:70]!r}: kept {len(parts[kept])} of {n} reports, sent the rest back")
        state["pending"] = requeue + state.get("pending", [])
    state["talks_split"] = 1
    return events
