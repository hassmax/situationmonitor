"""Front lines: this site's own assessment of who holds which settlement, made the way ISW makes
its assessments, by a team of separate agents, each with one job (config/frontlines.yaml lists
the conflicts and their sides):

  scout.py         searches the news for more on settlements resting on one side's claim (no model)
  standing.py      finds the towns news outlets describe as held as settled fact ("Russian-occupied
                   Melitopol", "Houthi-held Hodeidah"): long-held ground the reports of fighting
                   never mention (no model)
  isw.py           reads ISW's written reports (its text, never its maps) and lists the control
                   findings in them, credited to ISW (model, purpose "frontline_isw")
  claims.py        reads the map's ground-fighting and territory reports and lists every control
                   claim: who took, holds, lost or fights inside which settlement, on whose word,
                   on what evidence (model, purpose "frontline"); and the capture headlines the
                   standing-control searches find (purpose "frontline_news")
  social.py        reads Telegram and Bluesky posts directly for the same claims: flag-raising and
                   assault videos, "geolocated to" posts, a side's own announcements; who posted
                   decides the weight (model, purpose "frontline_social")
  heat.py          satellite fire detections near tracked settlements (no model)
  imagery.py       before-and-after Sentinel-2 pictures of a disputed settlement, compared by the
                   model for visible change; evidence for the reviewer only (model, purpose
                   "frontline_imagery")
  assess.py        files claims per settlement, looks each up once, and applies fixed evidence
                   rules: assessed / claimed / contested (no model)
  review.py        checks every change against its evidence and the nearby front before it is
                   published (model, purpose "frontline_review")
  wikipedia.py     a one-time snapshot of Wikipedia's conflict maps that fills the gaps where the
                   agents have nothing (no model)
  cartographer.py  what the map draws: shaded areas of control around the assessed settlements (no model)

The scout and the standing-control agent run before extraction (the scout's results join the
queue; the standing agent's descriptions go straight into the ledger); the rest after the events
of the run are merged. The ledger lives in state["frontline"] (see ledger.py).
"""
from __future__ import annotations

import re
from datetime import timedelta

from common import iso, log

from . import assess, cartographer, claims, heat, imagery, isw, ledger, review, scout, social, standing, wikipedia

__all__ = ["search", "update", "public"]

REVIEW_VERSION = 3   # bump when the reviewer's rules change in a way that should revisit its rejections
COUNTED_RE = re.compile(r"\b(majority|split|conflicting|mixed)\b", re.I)


def search(state: dict, session, now, outlets: dict | None = None, conflicts: list[dict] | None = None,
           items: list[dict] | None = None) -> list[dict]:
    """The scout's search results, as extraction items. The standing-control agent's findings (from
    its own searches and this run's fetched `items`) are filed in the ledger directly."""
    if conflicts:
        found = standing.run(conflicts, state, session, now, items or [], outlets)
        assess.add(ledger.state_of(state), found, now)
    return scout.run(state, session, now, outlets)


def update(events: list[dict], conflicts: list[dict], state: dict, settings: dict, now, ask, geocoder,
           budget, disabled: bool = False, session=None, posts: list[dict] | None = None) -> dict:
    """One round: read claims (from the map's reports, ISW and this run's social `posts`), file
    and place them, assess, review. `budget(purpose)` gives the model calls a purpose may make now.
    Returns counts for the log."""
    fl = ledger.state_of(state)
    queued = social.intake(posts or [], fl, now)
    if queued:
        log(f"[frontline] social: {queued} posts about control queued")
    if fl.get("review_version", 0) < 2:
        # 2026-10-04: the reviewer set aside descriptions of all of Crimea ("occupied Crimea") as not
        # naming the town; it now counts them for every town in it. Give those claims back.
        back = 0
        for p in fl["places"].values():
            for c in p["claims"]:
                if c.get("basis") != ledger.DESCRIBED:
                    continue
                said = c.get("summary") or ""
                quoted = said.split('"')[1] if said.count('"') >= 2 else ""
                if quoted and p["name"].lower() not in quoted.lower() and "covers all of" not in said:
                    c["summary"] = f"{said}, which covers all of that region, {p['name']} included"
                if c.pop("rejected", None):
                    back += 1
            p.pop("asked", None)
        log(f"[frontline] {back} descriptions set aside by the earlier review rule are back for review")
    if fl.get("review_version", 0) < 3:
        # 2026-10-05: the reviewer rejected the government's recapture of Mokha because most of the
        # (older) reports said the Houthis held it; it now weighs evidence by date. Give back what it
        # set aside on such counts, and set aside claims whose own words say the place was taken
        # from the side named as taking it.
        back = flipped = 0
        for p in fl["places"].values():
            conflict = ledger.conflict_for(conflicts, p["country"], p["conflict"])
            for c in p["claims"]:
                if c.get("rejected") and COUNTED_RE.search(c["rejected"]):
                    c.pop("rejected")
                    back += 1
                    p.pop("asked", None)
                note = (c.get("summary") or "").split(": ", 1)[-1]
                if conflict and not c.get("rejected") and ledger.backwards(conflict, c.get("actor"), c.get("change"), note):
                    c["rejected"] = "the report says the place was taken from this side"
                    flipped += 1
                    p.pop("asked", None)
        log(f"[frontline] {back} claims set aside by counting older reports are back for review; "
            f"{flipped} claims filed the wrong way round set aside")
    if fl.get("review_version", 0) < REVIEW_VERSION:
        fl["review_version"] = REVIEW_VERSION
    if not disabled:
        found = claims.run(events, conflicts, state, settings, now, ask, budget("frontline"))
        found += claims.run_news(conflicts, state, settings, now, ask, budget("frontline_news"))
        if session is not None:
            found += isw.run(conflicts, state, settings, session, now, ask, budget("frontline_isw"))
        found += social.run(conflicts, state, settings, now, ask, budget("frontline_social"))
        added = assess.add(fl, found, now)
    else:
        added = 0
    placed = assess.locate(fl, conflicts, geocoder)
    merged = assess.merge_spellings(fl)
    if session is not None:
        wikipedia.run(conflicts, state, session, now)  # once: Wikipedia's maps fill the gaps (no model)
    if session is not None and not disabled:
        heat.update(conflicts, fl, session, now)  # satellite fire detections near tracked places (no model)

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
    if session is not None and not disabled:
        # before-and-after satellite pictures of disputed settlements, for the reviewer
        imagery.run(conflicts, fl, state, settings, session, now, ask, budget("frontline_imagery"), [k for k, _ in pending])
    counts = review.run(pending, conflicts, fl, state, settings, now, ask,
                        0 if disabled else budget("frontline_review"))
    shown = sum(1 for p in fl["places"].values() if p.get("published"))
    log(f"[frontline] {added} new claims, {placed} settlements placed, {merged} spellings merged, {len(fl['places'])} tracked, {shown} on the map; "
        f"review: {counts['confirm']} confirmed, {counts['downgrade']} downgraded, {counts['reject']} rejected, "
        f"{counts['waiting']} waiting")
    return counts


def public(state: dict, conflicts: list[dict], now) -> dict:
    return cartographer.public(ledger.state_of(state), conflicts, now)
