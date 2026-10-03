"""Cartographer: no model. Turns the reviewed ledger into what the map draws: one point per
settlement, coloured by its holder, solid when assessed, faint when only claimed, amber when
contested, with the evidence summarised for the hover. No areas are drawn between settlements:
the map shows only places the reports name. Settlements with no evidence in SHOW_DAYS are left off.
"""
from __future__ import annotations

from datetime import timedelta

from common import iso

from . import ledger

SHOW_DAYS = 45
CHANGED_DAYS = 7
MAX_PLACES = 600


def public(fl: dict, conflicts: list[dict], now) -> dict:
    cutoff = iso(now - timedelta(days=SHOW_DAYS))
    changed = iso(now - timedelta(days=CHANGED_DAYS))
    places = []
    for p in fl.get("places", {}).values():
        pub = p.get("published")
        if not pub or p.get("lat") is None or not p["claims"] or p["claims"][-1]["time"] < cutoff:
            continue
        conflict = ledger.conflict_for(conflicts, p["country"], p["conflict"])
        if not conflict:
            continue
        holder = ledger.actor(conflict, pub.get("holder"))
        prev = ledger.actor(conflict, pub.get("previous"))
        live = [c for c in p["claims"] if not c.get("rejected")]
        places.append({
            "name": p["name"], "region": p.get("region"), "country": p["country"], "conflict": conflict["id"],
            "lat": p["lat"], "lon": p["lon"], "status": pub["status"],
            "holder": pub.get("holder"), "holder_name": holder["name"] if holder else None,
            "color": holder["color"] if holder else None,
            "previous_name": prev["name"] if prev else None,
            "since": pub.get("since"), "basis": pub.get("basis"), "sources": pub.get("sources"),
            "last": live[-1]["time"] if live else p["claims"][-1]["time"], "event": pub.get("event"),
            "changed": bool(pub.get("since") and pub["since"] >= changed),
        })
    places.sort(key=lambda x: x["last"], reverse=True)
    return {"conflicts": [{"id": c["id"], "name": c["name"],
                           "actors": [{"id": a["id"], "name": a["name"], "color": a["color"]} for a in c["actors"]]}
                          for c in conflicts],
            "places": places[:MAX_PLACES]}
