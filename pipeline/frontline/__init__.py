"""Front lines: this site's own assessment of who holds which settlement, made the way ISW makes
its assessments, by a team of separate agents, each with one job (config/frontlines.yaml lists
the conflicts and their sides):

  scout.py         searches the news for more on settlements resting on one side's claim (no model)
  claims.py        reads the map's ground-fighting and territory reports and lists every control
                   claim: who took, holds, lost or fights inside which settlement, on whose word,
                   on what evidence (model, purpose "frontline")
  assess.py        files claims per settlement, looks each up once, and applies fixed evidence
                   rules: assessed / claimed / contested (no model)
  review.py        checks every change against its evidence and the nearby front before it is
                   published (model, purpose "frontline_review")
  cartographer.py  what the map draws: settlement points, coloured by holder (no model)

The scout runs before extraction (its results join the queue); the rest after the events of the
run are merged. The ledger lives in state["frontline"] (see ledger.py).
"""
from __future__ import annotations

from datetime import timedelta

from common import iso, log

from . import assess, cartographer, claims, ledger, review, scout

__all__ = ["search", "update", "public"]


def search(state: dict, session, now, outlets: dict | None = None) -> list[dict]:
    """The scout's search results, as extraction items."""
    return scout.run(state, session, now, outlets)


def update(events: list[dict], conflicts: list[dict], state: dict, settings: dict, now, ask, geocoder,
           budget, disabled: bool = False) -> dict:
    """One round: read claims, file and place them, assess, review. `budget(purpose)` gives the
    model calls a purpose may make now. Returns counts for the log."""
    fl = ledger.state_of(state)
    if not disabled:
        found = claims.run(events, conflicts, state, settings, now, ask, budget("frontline"))
        added = assess.add(fl, found, now)
    else:
        added = 0
    placed = assess.locate(fl, conflicts, geocoder)
    merged = assess.merge_spellings(fl)

    cutoff = iso(now - timedelta(days=ledger.MEMORY_DAYS))
    pending = []
    for k, p in list(fl["places"].items()):
        p["claims"] = [c for c in p["claims"] if c["time"] >= cutoff]
        if not p["claims"]:
            del fl["places"][k]  # nothing said about it for MEMORY_DAYS
            continue
        conflict = ledger.conflict_for(conflicts, p["country"], p["conflict"])
        if not conflict or p.get("lat") is None:
            continue
        proposed = assess.assess(p, conflict, now)
        pub = p.get("published") or {}
        if not proposed:
            p.pop("published", None)  # every claim behind it was set aside
            continue
        if (proposed.get("holder"), proposed["status"]) == (pub.get("holder"), pub.get("status")):
            for k2 in ("basis", "sources", "event"):  # same picture, fresher evidence
                if proposed.get(k2) is not None:
                    pub[k2] = proposed[k2]
            continue
        live = sum(1 for c in p["claims"] if not c.get("rejected"))
        if p.get("asked") is not None and live <= p["asked"]:
            continue  # the reviewer already saw this evidence
        pending.append((k, proposed))
    counts = review.run(pending, conflicts, fl, state, settings, now, ask,
                        0 if disabled else budget("frontline_review"))
    shown = sum(1 for p in fl["places"].values() if p.get("published"))
    log(f"[frontline] {added} new claims, {placed} settlements placed, {merged} spellings merged, {len(fl['places'])} tracked, {shown} on the map; "
        f"review: {counts['confirm']} confirmed, {counts['downgrade']} downgraded, {counts['reject']} rejected, "
        f"{counts['waiting']} waiting")
    return counts


def public(state: dict, conflicts: list[dict], now) -> dict:
    return cartographer.public(ledger.state_of(state), conflicts, now)
