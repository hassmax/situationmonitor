"""Same-story check: fold events that are one incident reported in different words or places.

The merge rules join reports by place, time, and wording. Some duplicates still slip through:
one outlet pins a story to London and another to RAF Fairford, or one files an Iran-linked plot
under the Middle East and another under the NATO flank. So at most once an hour, recent events
in the same country that look alike are put to the model in pairs ("the same specific incident
or statement?"), and pairs it calls the same are folded into the earliest event.

- One model call at most, from the shared daily budget, skipped when fewer than
  dedupe_min_calls calls are left (the brief keeps priority).
- Each pair is asked about once; answers are remembered for a week.
- Only events from the last 48 hours, not attack waves or alert groups (they have their own
  grouping), and only pairs with some wording in common are asked about.
- Similar but separate incidents (two strikes on the same city, two drills) stay separate.
"""
from __future__ import annotations

import json
from datetime import timedelta

from common import iso, log, parse_time
from merge import FAMILY, _absorb, _words

MIN_INTERVAL = timedelta(hours=1)
LOOKBACK = timedelta(hours=48)
PAIR_WINDOW = timedelta(hours=36)
MIN_OVERLAP = 0.2
MAX_PAIRS = 30
KEEP_DAYS = 7
# Families that can describe the same incident (a drone strike reported as an explosion).
GROUP = {"strike": "violence", "ground": "violence", "naval": "naval", "deployment": "deployment",
         "hybrid": "hybrid", "incursion": "incursion", "diplomacy": "diplomacy", "legal": "diplomacy"}

PROMPT = """You check a live conflict map for duplicates. Each case is a pair of events from the map, each with its summary, place, country, and time.

Answer "same": true only when both describe the same specific incident or statement: the same arrests, the same strike, the same seizure, the same exercise, the same announcement, including follow-up coverage of it (new details, reactions, denials of that same incident). Places can differ (a city versus the base or region it is about) and wording can differ.
Answer "same": false when they are separate incidents that resemble each other (two strikes on the same city, two drills, two arrests in different cases), when one is only background to the other, or when you are unsure.

Reply with one JSON object and nothing else:
{"results": [{"i": <case number>, "same": true or false}]}"""


def _key(a: str, b: str) -> str:
    return "|".join(sorted((a, b)))


def _overlap(e: dict, f: dict) -> float:
    places = _words(" ".join(str(x.get("place") or "") for x in (e, f)))
    a, b = _words(e.get("summary")) - places, _words(f.get("summary")) - places
    return len(a & b) / min(len(a), len(b)) if a and b else 0.0


def pairs(events: list[dict], judged: dict, now) -> list[tuple[dict, dict]]:
    """Pairs worth asking about, most alike first."""
    since = now - LOOKBACK
    pool = [e for e in events if not e.get("wave") and not e.get("alert") and e.get("country")
            and (parse_time(e.get("time")) or since) > since and e["type"] != "arms_transfer"]
    out = []
    for i, e in enumerate(pool):
        for f in pool[i + 1:]:
            if (e["country"] != f["country"] or _key(e["id"], f["id"]) in judged
                    or GROUP.get(FAMILY.get(e["type"])) != GROUP.get(FAMILY.get(f["type"]))
                    or abs(parse_time(e["time"]) - parse_time(f["time"])) > PAIR_WINDOW):
                continue
            score = _overlap(e, f)
            if score >= MIN_OVERLAP:
                out.append((score, e, f))
    out.sort(key=lambda x: -x[0])
    return [(e, f) for _, e, f in out[:MAX_PAIRS]]


def _fold(events: list[dict], same: list[tuple[str, str]], skip: set[str]) -> tuple[list[dict], list[dict]]:
    by_id = {e["id"]: e for e in events}
    parent = {i: i for i in by_id}

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for a, b in same:
        if a in by_id and b in by_id and a not in skip and b not in skip:
            ra, rb = root(a), root(b)
            if ra != rb:  # the earliest event keeps its id
                first, second = sorted((ra, rb), key=lambda i: (by_id[i]["time"], i))
                parent[second] = first
    folded = []
    for i, e in by_id.items():
        r = root(i)
        if r == i:
            continue
        keep = by_id[r]
        urls = {x["url"] for x in keep["reports"]}
        keep["reports"] += [x for x in e["reports"] if x["url"] not in urls]
        _absorb(keep, e)
        keep["updated"] = max(keep["updated"], e["updated"])
        folded.append(e)
    gone = {e["id"] for e in folded}
    return [e for e in events if e["id"] not in gone], folded


def run(events: list[dict], state: dict, settings: dict, now, ask, remaining: int,
        skip: set[str]) -> tuple[list[dict], list[dict]]:
    """Returns (events, the events folded away). `ask` is extract.ask_json."""
    st = state.setdefault("dedupe", {})
    judged: dict = st.setdefault("judged", {})
    cutoff = iso(now - timedelta(days=KEEP_DAYS))
    st["judged"] = judged = {k: v for k, v in judged.items() if v.get("at", "") >= cutoff}
    # pairs already judged the same are folded again if both came back (e.g. restored by a check)
    events, folded = _fold(events, [tuple(k.split("|")) for k, v in judged.items() if v.get("same")], skip)
    tried = parse_time(st.get("attempt"))
    if tried and now - tried < MIN_INTERVAL:
        return events, folded
    todo = [(e, f) for e, f in pairs(events, judged, now) if e["id"] not in skip and f["id"] not in skip]
    if not todo:
        return events, folded
    if remaining < int(settings.get("dedupe_min_calls", 10)):
        log(f"[dedupe] skipped: only {remaining} model calls left today")
        return events, folded
    st["attempt"] = iso(now)
    side = lambda e: {"summary": e.get("summary"), "place": e.get("place"), "country": e.get("country"),  # noqa: E731
                      "time": e.get("time")}
    payload = [{"i": n, "a": side(e), "b": side(f)} for n, (e, f) in enumerate(todo)]
    reply = ask(PROMPT, json.dumps({"cases": payload}, ensure_ascii=False), state, settings, now, max_tokens=1500)
    if not isinstance(reply, dict) or not isinstance(reply.get("results"), list):
        log("[dedupe] no model answer; will try again later")
        return events, folded
    same = []
    for res in reply["results"]:
        if not isinstance(res, dict) or not isinstance(res.get("i"), int) or not 0 <= res["i"] < len(todo):
            continue
        e, f = todo[res["i"]]
        judged[_key(e["id"], f["id"])] = {"same": res.get("same") is True, "at": iso(now)}
        if res.get("same") is True:
            same.append((e["id"], f["id"]))
            log(f"[dedupe] same story: {e['summary']!r} + {f['summary']!r}")
    events, more = _fold(events, same, skip)
    log(f"[dedupe] asked about {len(todo)} pairs, folded {len(more)} events")
    return events, folded + more
