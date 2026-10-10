"""Atomically re-read legacy country-wide attack campaigns as specific incidents.

No event or evidence is removed until every report is assigned exactly once and every
replacement passes the ordinary extraction and location checks. Failures retry next run.
"""
from __future__ import annotations

import json
from copy import deepcopy
from datetime import timedelta

import extract
import geo
import merge
from common import iso, log, parse_time, short_hash

PROMPT = extract.SYSTEM_PROMPT + """

Repair task: the input is ONE legacy attack campaign incorrectly grouped by attacker and
country over 18 hours. Re-read ALL report summaries as evidence. Partition their numbered
reports into specific incidents, NOT one country-wide wave. Airport vs oil-field attacks,
different target facilities, renewed attacks at the same airport, separate interceptions,
and distinct policy decisions must have separate groups. Follow-up casualty reporting,
footage and condemnations of ONE identifiable attack belong with that attack. A renewed
attack mentioning a past attack's three deaths must NOT inherit those deaths. Do not infer
casualties, occurrence dates, origins or attacker from the old campaign's aggregate fields.
If attribution or incident identity is uncertain, preserve that uncertainty; do not force
unrelated reports together. Every report number must occur EXACTLY ONCE, including vague
reports (give unresolved reporting its own group when it cannot be assigned confidently).
Use existing target coordinates only when they actually identify that group's site.

Override the output format above. Return ONLY:
{"groups": [{"reports": [report numbers], "event": {the ordinary relevant event fields above}}]}
Each event must include type, summary, place, country, theater, severity, happened, killed,
injured, attacker, lat and lon. Use the earliest source's publication date only as context;
happened is null unless the evidence supplies an occurrence date. Do not discard evidence.
"""


def replacements(parent, reply, geocoder, theaters, existing_ids):
    """Validate the whole partition before constructing any replacement events."""
    groups = reply.get("groups") if isinstance(reply, dict) else None
    reports = parent.get("reports") or []
    if not isinstance(groups, list) or not groups or not reports:
        return None
    flat = []
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("reports"), list) or not group["reports"]:
            return None
        if not isinstance(group.get("event"), dict):
            return None
        flat.extend(group["reports"])
    if any(type(k) is not int for k in flat) or sorted(flat) != list(range(len(reports))):
        return None
    earliest = min(range(len(reports)), key=lambda i: reports[i]["time"])
    result = []
    used = set(existing_ids) - {parent["id"]}
    for group in groups:
        evidence = [deepcopy(reports[i]) for i in group["reports"]]
        first = min(evidence, key=lambda r: r["time"])
        item = {**first, "text": first["summary"], "platform": first.get("platform", "rss"),
                "kind": first.get("kind", "news"), "group": first.get("group") or first.get("source"),
                "source": first.get("source", "unknown")}
        facts = group["event"]
        if facts.get("type") not in extract.EVENT_TYPES or not isinstance(facts.get("summary"), str):
            return None
        rec = extract._clean_record({**facts, "relevant": True, "alert": False}, item)
        candidate = geo.place_record(rec, geocoder, theaters) if rec else None
        if not candidate:
            return None
        event = merge.merge([], [candidate])[0]
        # Preserve the original link for its earliest reported incident. New IDs are stable.
        eid = parent["id"] if earliest in group["reports"] else short_hash(
            "incident", parent["id"], *sorted(r["url"] for r in evidence))
        if eid in used:
            return None
        used.add(eid)
        event.update(id=eid, reports=evidence, incident_split=parent["id"],
                     updated=max(r["time"] for r in evidence))
        # Do not copy aggregate casualties, status, nearby-news corroboration or old headlines.
        result.append(event)
    return result


def repair(events, state, ask, settings, now, geocoder, theaters, hidden):
    """Bounded, resumable migration; legacy campaigns cannot take in fresh reports."""
    waiting = [e for e in events if e.get("wave") and e["id"] not in hidden and e.get("reports")
               and parse_time(e.get("updated") or e["time"]) >= now - timedelta(days=14)]
    waiting.sort(key=lambda e: (e.get("updated", e["time"]), len(e["reports"])), reverse=True)
    repaired = {}
    for e in waiting[:int(settings.get("incident_repairs_per_run", 3))]:
        payload = {"theater": e["theater"], "country": e.get("country"),
                   "targets": [{k: t.get(k) for k in ("place", "lat", "lon")} for t in e.get("targets") or []],
                   "reports": [{"r": i, "time": r["time"], "summary": r["summary"],
                                "source": r.get("source"), "incident": r.get("incident")}
                               for i, r in enumerate(e["reports"])]}
        reply = ask(PROMPT, json.dumps(payload, ensure_ascii=False), state, settings, now,
                    max_tokens=16000 if len(e["reports"]) > 120 else 8000, purpose="incident_repair")
        if reply is None:
            break  # budget/provider unavailable; preserve all remaining originals
        parts = replacements(e, reply, geocoder, theaters, {x["id"] for x in events} |
                             {p["id"] for ps in repaired.values() for p in ps})
        if parts is None:
            log(f"[incidents] {e['id']}: incomplete/invalid repair; original retained for retry")
            continue
        repaired[e["id"]] = parts
        log(f"[incidents] {e['id']}: {len(e['reports'])} reports -> {len(parts)} specific incidents")
    state["incident_repair"] = {"at": iso(now), "remaining": len(waiting) - len(repaired),
                                "repaired": list(repaired)}
    return [part for e in events for part in repaired.get(e["id"], [e])]
