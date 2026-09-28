"""Same-story check: fold events that are one incident reported in different words or places.

The merge rules join reports by place, time, and wording. Some stories still end up as several
events: one outlet pins the arrests near RAF Fairford to London, another to the base, a third
to "the UK"; one files the Iran-linked plot under the Middle East, another under the NATO flank;
and follow-up coverage keeps arriving for days, after the merge window has closed. Wording alone
can't sort these out (reports of the same arrests shared anywhere from 9% to 89% of their words).

So recent events of the same kind in the same country are shown to the model together, a group
at a time, and it says which of them describe the same specific incident. Those are folded into
the earliest event.

- One model call at most per MIN_INTERVAL (every RETRY while groups are still waiting, or after
  a failed call), from the shared daily budget, skipped when fewer than dedupe_min_calls calls
  are left (the brief keeps priority).
- Events from the last LOOKBACK hours, not attack waves or alert groups (they have their own
  grouping), not arms transfers, and not diplomacy (its own rules keep separate talks apart).
- Hybrid attacks, deployments, incursions and naval incidents in one country are shown as one
  group; strikes and fighting only when their wording overlaps, since a country at war has
  many separate strikes a day.
- A group is shown again only when it contains a pair not judged before; answers are
  remembered for a week, and pairs judged the same are folded again if both come back.
"""
from __future__ import annotations

import json
from datetime import timedelta

from common import iso, log, parse_time
from merge import FAMILY, _absorb, _words

MIN_INTERVAL = timedelta(hours=1)
RETRY = timedelta(minutes=15)
LOOKBACK = timedelta(hours=72)
PAIR_WINDOW = timedelta(hours=48)
VIOLENCE_OVERLAP = 0.2
MAX_GROUP = 20
MAX_EVENTS = 60
KEEP_DAYS = 7
# Families that can describe the same incident (a drone strike reported as an explosion).
GROUP = {"strike": "violence", "ground": "violence", "naval": "naval", "deployment": "deployment",
         "hybrid": "hybrid", "incursion": "incursion"}
WHOLE = {"naval", "deployment", "hybrid", "incursion"}  # shown as one group per country

PROMPT = """You check a live conflict map for duplicates. Each case is a list of events from the map in the same country, each with an id, its summary, place, and time.

Group the events that describe the same specific incident or statement: the same arrests, the same strike, the same seizure, the same exercise, the same announcement, including follow-up coverage of it over the following days (new details, reactions, questioning of suspects, denials). Places can differ (a city, the base it is about, or the whole country) and wording can differ.
Keep events apart when they are separate incidents that resemble each other (two strikes on the same city, two drills, arrests in two different cases), when one is only background to the other, or when you are unsure.

Reply with one JSON object and nothing else, with one entry for every case. In "groups" list only groups of two or more ids; events in no group stay separate. Use "groups": [] when a case has no duplicates:
{"results": [{"i": <case number>, "groups": [["<id>", "<id>", ...], ...]}]}"""


def _key(a: str, b: str) -> str:
    return "|".join(sorted((a, b)))


def _overlap(e: dict, f: dict) -> float:
    places = _words(" ".join(str(x.get("place") or "") for x in (e, f)))
    a, b = _words(e.get("summary")) - places, _words(f.get("summary")) - places
    return len(a & b) / min(len(a), len(b)) if a and b else 0.0


def _linked(e: dict, f: dict, group: str) -> bool:
    if abs(parse_time(e["time"]) - parse_time(f["time"])) > PAIR_WINDOW:
        return False
    return group in WHOLE or _overlap(e, f) >= VIOLENCE_OVERLAP


def groups(events: list[dict], judged: dict, now) -> list[list[dict]]:
    """Groups worth asking about (some pair in them not judged yet), newest first."""
    since = now - LOOKBACK
    buckets: dict[tuple, list[dict]] = {}
    for e in events:
        g = GROUP.get(FAMILY.get(e["type"]))
        if (g and not e.get("wave") and not e.get("alert") and e.get("country")
                and (parse_time(e.get("time")) or since) > since):
            buckets.setdefault((e["country"], g), []).append(e)
    out = []
    for (_, g), pool in buckets.items():
        pool.sort(key=lambda e: e["time"])
        seen: set[str] = set()
        for start in pool:  # connected groups of linked events
            if start["id"] in seen:
                continue
            comp, todo = [], [start]
            seen.add(start["id"])
            while todo:
                e = todo.pop()
                comp.append(e)
                for f in pool:
                    if f["id"] not in seen and _linked(e, f, g):
                        seen.add(f["id"])
                        todo.append(f)
            comp = sorted(comp, key=lambda e: e["time"])[-MAX_GROUP:]
            if len(comp) > 1 and any(_key(a["id"], b["id"]) not in judged
                                     for i, a in enumerate(comp) for b in comp[i + 1:]):
                out.append(comp)
    # biggest groups first (a story reported many times over is the likeliest duplicate), then newest
    out.sort(key=lambda c: (len(c), c[-1]["time"]), reverse=True)
    return out


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
    cutoff = iso(now - timedelta(days=KEEP_DAYS))
    st["judged"] = judged = {k: v for k, v in (st.get("judged") or {}).items() if v.get("at", "") >= cutoff}
    # pairs already judged the same are folded again if both came back (e.g. restored by a check)
    events, folded = _fold(events, [tuple(k.split("|")) for k, v in judged.items() if v.get("same")], skip)
    done, failed = parse_time(st.get("attempt")), parse_time(st.get("failed"))
    wait = RETRY if st.get("backlog") else MIN_INTERVAL
    if (done and now - done < wait) or (failed and now - failed < RETRY):
        return events, folded
    cases, size, due = [], 0, groups([e for e in events if e["id"] not in skip], judged, now)
    for comp in due:
        if size + len(comp) > MAX_EVENTS:
            continue
        cases.append(comp)
        size += len(comp)
    if not cases:
        return events, folded
    if remaining < int(settings.get("dedupe_min_calls", 10)):
        log(f"[dedupe] skipped: only {remaining} model calls left today")
        return events, folded
    payload = [{"i": n, "events": [{"id": e["id"], "summary": e.get("summary"), "place": e.get("place"),
                                    "time": e.get("time")} for e in comp]} for n, comp in enumerate(cases)]
    reply = ask(PROMPT, json.dumps({"cases": payload}, ensure_ascii=False), state, settings, now, max_tokens=2000)
    if not isinstance(reply, dict) or not isinstance(reply.get("results"), list):
        st["failed"] = iso(now)
        log(f"[dedupe] no model answer; will try again in {int(RETRY.total_seconds() // 60)} minutes")
        return events, folded
    st["attempt"] = iso(now)
    st["backlog"] = len(cases) < len(due)
    st.pop("failed", None)
    same = []
    answered = set()
    for res in reply["results"]:
        n_case = _case_number(res)
        if n_case is None or not 0 <= n_case < len(cases) or n_case in answered:
            continue
        answered.add(n_case)
        comp = cases[n_case]
        ids = {e["id"] for e in comp}
        where = {}
        for n, grp in enumerate(_groups(res)):
            for i in grp:
                i = str(i).strip()
                if i in ids and i not in where:
                    where[i] = n
        for x, a in enumerate(comp):
            for b in comp[x + 1:]:
                hit = a["id"] in where and where.get(a["id"]) == where.get(b["id"])
                judged[_key(a["id"], b["id"])] = {"same": hit, "at": iso(now)}
                if hit:
                    same.append((a["id"], b["id"]))
        for n in sorted(set(where.values())):
            members = [e for e in comp if where.get(e["id"]) == n]
            if len(members) > 1:
                log("[dedupe] same story: " + " + ".join(repr(e["summary"][:60]) for e in members))
    events, more = _fold(events, same, skip)
    log(f"[dedupe] asked about {len(cases)} groups ({size} events), folded {len(more)} events")
    if len(answered) < len(cases):
        sample = json.dumps(reply["results"][:1], ensure_ascii=False)[:200]
        log(f"[dedupe] {len(cases) - len(answered)} of {len(cases)} groups got no usable answer "
            f"(asked again later); first result looked like: {sample}")
    return events, folded + more


def _case_number(res) -> int | None:
    """The case a result is about: "i" as a number or a numeric string (models differ)."""
    if not isinstance(res, dict):
        return None
    v = next((res[k] for k in ("i", "case", "index", "id") if k in res), None)
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str) and v.strip().lstrip("#").isdigit():
        return int(v.strip().lstrip("#"))
    return None


def _groups(res: dict) -> list[list]:
    """The id groups of a result: lists of ids, or objects holding them ({"ids": [...]})."""
    out = []
    for g in res.get("groups") or res.get("same") or []:
        if isinstance(g, dict):
            g = g.get("ids") or g.get("events") or []
        if isinstance(g, list) and len(g) > 1:
            out.append(g)
    return out
