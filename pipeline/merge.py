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

from common import haversine_km, iso, log, parse_time, short_hash
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
WINDOW = timedelta(hours=18)  # measured from when the event first happened, never from later reports

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


def _is_wave(c: dict) -> bool:
    return (c["type"] in WAVE_TYPES and bool(c.get("attacker")) and bool(c.get("country"))
            and c["attacker"] != c["country"])


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


def _find_transfer(events: list[dict], cand: dict) -> dict | None:
    t = cand.get("transfer") or {}
    ct = parse_time(cand["time"])
    for e in events:
        et = e.get("transfer") or {}
        if (e["type"] == "arms_transfer" and et.get("supplier") == t.get("supplier")
                and et.get("recipient") == t.get("recipient") and et.get("kind") == t.get("kind")
                and abs(ct - parse_time(e["updated"])) <= TRANSFER_WINDOW):
            return e
    return None


_WORD_STOP = set("""a an the of in on at to for and or by with as is are was were be been its it this that
from after over into amid near during against about says said say claims claimed claim reports reported
report according officials official state states stated new following""".split())


def _words(text: str) -> set[str]:
    return {w.rstrip("s") for w in re.findall(r"[a-z][a-z'-]+", (text or "").lower()) if w not in _WORD_STOP}


def _overlap(a: str, b: str) -> float:
    wa, wb = _words(a), _words(b)
    return len(wa & wb) / min(len(wa), len(wb)) if wa and wb else 0.0


def _same_talks(e: dict, cand: dict) -> bool:
    """Diplomacy and legal steps merge by who takes part, not only by place: separate talks
    often happen in the same region on the same day (Netanyahu in Abu Dhabi, an Iranian
    proposal on Hormuz)."""
    a, b = set(e.get("parties") or []), set(cand.get("parties") or [])
    if a and b:
        if len(a & b) >= 2:
            return True
        return a == b and _overlap(e["summary"], cand["summary"]) >= 0.4
    return _overlap(e["summary"], cand["summary"]) >= 0.5


def _find_match(events: list[dict], cand: dict) -> dict | None:
    fam = FAMILY.get(cand["type"], "strike")
    if fam == "transfer":
        return _find_transfer(events, cand) if cand.get("transfer") else None
    ct = parse_time(cand["time"])
    best, best_d = None, float("inf")
    for e in events:
        if e.get("wave") or e.get("alert") or e["theater"] != cand["theater"] or FAMILY.get(e["type"], "strike") != fam:
            continue
        if abs(ct - parse_time(e["time"])) > WINDOW:
            continue
        if fam in ("diplomacy", "legal") and not _same_talks(e, cand):
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


def merge(events: list[dict], candidates: list[dict]) -> list[dict]:
    events = _fold_stored_alerts(events)
    for cand in sorted(candidates, key=lambda c: c["time"]):
        if _is_alert(cand):
            _merge_alert(events, cand)
            continue
        if _is_wave(cand):
            _merge_wave(events, cand)
            continue
        rep = cand["report"]
        match = _find_match(events, cand)
        if match is None:
            events.append({
                "id": short_hash("event", rep["url"]), "alert": False,
                "theater": cand["theater"], "type": cand["type"], "summary": cand["summary"],
                "place": cand["place"], "country": cand["country"], "attacker": cand.get("attacker"),
                "lat": cand["lat"], "lon": cand["lon"], "approx": cand["approx"],
                "origins": list(cand.get("origins") or []),
                "parties": list(cand.get("parties") or []),
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
        # Google News results from outlets not listed in sources.yaml share one group. Much of it
        # is syndicated copy (local sites republishing Reuters), so it only counts when no listed
        # outlet has reported the event.
        counted = groups - WEAK_GROUPS if groups - WEAK_GROUPS else groups
        if len(counted) >= 2 and ((neutral & counted) or len(sides) >= 2):
            e["status"] = "corroborated"
        elif neutral:
            e["status"] = "unconfirmed"
        else:
            e["status"] = "claimed"
        e["sources_count"] = len(counted)
        if not e.get("wave") and not e.get("alert"):
            e["summary"] = _headline(e)["summary"]


WEAK_GROUPS = {"google-news"}

SUPPLY_RETENTION_DAYS = 30  # arms transfers are shown as 30-day flows


def prune(events: list[dict], now: datetime, retention_days: int, max_events: int) -> list[dict]:
    cutoff = iso(now - timedelta(days=retention_days))
    supply_cutoff = iso(now - timedelta(days=max(retention_days, SUPPLY_RETENTION_DAYS)))
    kept = [e for e in events if e["updated"] >= (supply_cutoff if e["type"] == "arms_transfer" else cutoff)]
    kept.sort(key=lambda e: e["updated"], reverse=True)
    return kept[:max_events]


def public_event(e: dict) -> dict:
    """Strip internal fields before publishing."""
    out = {k: v for k, v in e.items() if k not in ("reports", "us", "cn", "wave_key", "alert_key", "origin", "checked", "checks")}
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
        reply = ask(SPLIT_PROMPT, json.dumps({"events": payload}, ensure_ascii=False), state, settings, now, max_tokens=4000)
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
