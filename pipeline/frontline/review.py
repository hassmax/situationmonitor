"""Reviewer agent: nothing the assessor works out reaches the map until a second model call has
checked it. For each proposed change (a settlement newly on the map, a new holder, a new status)
it sees the evidence, what the map shows now and the published places nearby, and answers:

  confirm    the evidence says this about this settlement, at this strength
  downgrade  supported, but only as one side's claim: shown as "claimed"
  reject     not what the evidence says (fighting near it, a strike on it, a same-named place,
             an old event), or the place doesn't fit the region named or the nearby front: the
             claims behind it are set aside, with the reason

A change left without a verdict is asked again only when new evidence arrives (`asked`).
One budgeted call a run (purpose "frontline_review", share frontline_review_daily_max), up to
PER_CALL changes, those with the strongest evidence and the most recent first.
"""
from __future__ import annotations

import json

from common import haversine_km, iso, log

from . import ledger

PER_CALL = 15
NEARBY_KM = 40
EVIDENCE = 8

PROMPT = """You check proposed changes to a front-line map before they are published. Each item is a settlement, what the map shows for it now, the proposed change, published places nearby, and the evidence (short report summaries). Reply with one JSON object and nothing else.

For each item decide:
- "confirm": the evidence says what the proposal says about this specific settlement, and the strength fits. Status "assessed" needs more than one side's word: verified or geolocated footage, reporting from the scene, both sides agreeing, an independent analyst, or two independent sources (including two independent outlets that describe the town as held as settled fact, basis "described": "Russian-occupied Melitopol", "Houthi-held Hodeidah"). Status "claimed" is one side's claim. Status "contested" is fighting inside the settlement.
- "downgrade": the change is supported, but every piece of evidence traces back to one side's own statement; it will be shown as "claimed". Use the tally: several independent outlets, or reporting from the scene, are not one side's statement, even if a side also made a claim.
- "reject": the evidence does not say this (it is about fighting near the settlement, a strike on it, a facility such as its airport or a base rather than the town, a different place with a similar name, or an old event), or the settlement's position does not fit the region named or the nearby front.
Evidence with basis "described" quotes the few words an outlet wrote. Reject it when the words are about a region, district or province of the same name rather than the town (in Ukraine "occupied Kherson" usually means the Kherson region, whose capital Ukraine holds), or the position does not fit what the map shows nearby.
Give a short reason (max 20 words).

JSON: {"items": [{"n": <n>, "verdict": "confirm" | "downgrade" | "reject", "reason": "..."}]}"""


def _label(conflict: dict, holder: str | None, status: str | None) -> str:
    if not status:
        return "not on the map"
    a = ledger.actor(conflict, holder)
    who = a["name"] if a else "no side"
    return {"assessed": f"held by {who} (assessed)", "claimed": f"claimed by {who}",
            "contested": f"contested (last held by {who})" if a else "contested"}.get(status, status)


def _item(n: int, p: dict, conflict: dict, proposed: dict, places: dict) -> dict:
    pub = p.get("published") or {}
    nearby = []
    for q in places.values():
        qp = q.get("published")
        if q is p or not qp or q.get("lat") is None or q["conflict"] != p["conflict"]:
            continue
        d = haversine_km(p["lat"], p["lon"], q["lat"], q["lon"])
        if d <= NEARBY_KM:
            nearby.append((d, f"{q['name']} ({round(d)} km): {_label(conflict, qp.get('holder'), qp.get('status'))}"))
    live = [c for c in p["claims"] if not c.get("rejected")]
    tally = {"independent_outlets": len({c["group"] for c in live if not c.get("aligned")}),
             "statements_by_side": sorted({ledger.actor(conflict, c["aligned"])["name"] for c in live
                                           if c.get("aligned") and ledger.actor(conflict, c["aligned"])}),
             "kinds_of_evidence": sorted({c["basis"] for c in live}), "reports": len(live)}
    evidence = []
    for c in live[-EVIDENCE:]:
        who = ledger.actor(conflict, c.get("aligned"))
        evidence.append({"date": c["time"][:10], "source": c.get("source"), "speaks_for": who["name"] if who else None,
                         "basis": c["basis"], "summary": c.get("summary")})
    return {"n": n, "settlement": p["name"], "region": p.get("region"), "country": p["country"],
            "position": [p["lat"], p["lon"]], "conflict": conflict["name"],
            "now": _label(conflict, pub.get("holder"), pub.get("status")),
            "proposed": f"{_label(conflict, proposed.get('holder'), proposed['status'])}; basis: {proposed.get('basis')}",
            "nearby": [x for _, x in sorted(nearby)[:6]], "tally": tally, "evidence": evidence}


def run(pending: list[tuple], conflicts: list[dict], fl: dict, state: dict, settings: dict, now, ask, budget: int) -> dict:
    """pending: [(key, proposed)]. Applies verdicts to the ledger; returns counts by verdict."""
    counts = {"confirm": 0, "downgrade": 0, "reject": 0, "waiting": len(pending)}
    if not pending or budget <= 0:
        return counts
    rank = {"assessed": 0, "contested": 1, "claimed": 2}
    pending = sorted(pending, key=lambda kp: kp[1].get("last") or "", reverse=True)
    pending.sort(key=lambda kp: rank.get(kp[1]["status"], 3))
    batch = pending[:PER_CALL]
    items = []
    for n, (k, proposed) in enumerate(batch):
        p = fl["places"][k]
        items.append(_item(n, p, ledger.conflict_for(conflicts, p["country"], p["conflict"]), proposed, fl["places"]))
    got = ask(PROMPT, json.dumps({"items": items}, ensure_ascii=False), state, settings, now,
              max_tokens=3000, purpose="frontline_review")
    if got is None:
        log("[frontline] review: no answer; changes wait")
        return counts
    verdicts = {v.get("n"): v for v in got.get("items") or [] if isinstance(v, dict)}
    for n, (k, proposed) in enumerate(batch):
        p = fl["places"][k]
        v = verdicts.get(n) or {}
        verdict, reason = v.get("verdict"), str(v.get("reason") or "")[:200]
        live = [c for c in p["claims"] if not c.get("rejected")]
        if verdict in ("confirm", "downgrade"):
            status = "claimed" if verdict == "downgrade" and proposed["status"] == "assessed" else proposed["status"]
            p["published"] = {k2: proposed.get(k2) for k2 in ("holder", "since", "basis", "sources", "event", "previous")}
            p["published"].update(status=status, reviewed=iso(now), note=reason)
            p.pop("asked", None)
            if status != proposed["status"]:  # shown weaker than worked out: ask again only on new evidence
                p["published"]["downgraded"] = True
                p["asked"] = len(live)
        elif verdict == "reject":
            for c in live:
                if c["time"] >= (proposed.get("since") or ""):
                    c["rejected"] = reason or "rejected by the reviewer"
            p.pop("asked", None)
        else:
            p["asked"] = len(live)
            continue
        counts[verdict] += 1
        counts["waiting"] -= 1
        log(f"[frontline] review {verdict}: {p['name']} -> {proposed['status']} {proposed.get('holder')}: {reason}")
    return counts
