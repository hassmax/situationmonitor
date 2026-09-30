"""Same-story check: fold events that are one incident reported in different words or places.

The merge rules join reports by place, time, and wording. Some stories still end up as several
events: one outlet pins the arrests near RAF Fairford to London, another to the base, a third
to "the UK"; one files the Iran-linked plot under the Middle East, another under the NATO flank;
and follow-up coverage keeps arriving for days, after the merge window has closed. Wording alone
can't sort these out (reports of the same arrests shared anywhere from 9% to 89% of their words).

So recent events of the same kind in the same country are shown to the model together, a group
at a time, and it says which of them describe the same specific incident. Those are folded into
the earliest event.

- Every run while groups are still waiting; otherwise at most every MIN_INTERVAL, and RETRY after
  a failed call. From the shared daily budget, skipped when fewer than dedupe_min_calls calls
  are left (the brief keeps priority).
- Events from the last LOOKBACK hours, not attack waves or alert groups (they have their own
  grouping) and not arms transfers.
- Hybrid attacks, deployments, incursions and naval incidents in one country are shown as one
  group; strikes and fighting only when their wording overlaps, since a country at war has
  many separate strikes a day.
- Diplomacy and legal steps are grouped by who takes part, not by country or theater (a German
  minister's visit to the ICC was filed once under the NATO flank and once under Ukraine; one
  meeting was pinned to New York, Berlin and Moscow): events whose parties are the same, one
  within the other (["EU"] and ["EU", "RU"]), or two in common, and some wording in common.
  Hybrid and deployment events that name parties join these groups too, since a statement is
  sometimes filed under the kind of event it is about (the EU's top diplomat warning of Russian
  sabotage was filed as a hybrid attack).
- A group is shown again only when it contains a pair not settled yet; answers are
  remembered for a week, and pairs judged the same are folded again if both come back.
- "Different" gets one second look after SECOND_LOOK: when Google's usual model is busy the
  check runs on a weaker backup, which once called nearly every pair different (among them two
  reports of the same EU fund release), and a first "different" used to stand for a week.
  After the second answer the pair is settled either way.
- Up to MAX_CALLS_PER_RUN calls while groups are waiting (a backlog of 68 groups kept a Myanmar
  airstrike reported four times waiting for hours at one call per run); the second and third
  only while at least EXTRA_CALLS_FLOOR calls are left today.
- For each set of events it folds, the model also writes one headline from the facts the events
  agree on ("Myanmar military airstrike on a market in Rakhine kills 50"), in the same call; it
  leads until a new report arrives. A headline with a number none of the events gives is dropped.
- Late follow-ups: a story reported again days later (the RAF Fairford arrests, 62 hours after
  the first event) falls outside PAIR_WINDOW. So each event from the last NEW_HOURS is also
  compared with up to LATE_CANDIDATES older events of the same kind and country, up to LATE_DAYS
  back (hybrid attacks, deployments, incursions, naval incidents: the closest in wording; strikes,
  fighting and talks: only with close wording and the same names): in the working set (folded as usual, so the event keeps its first date) and in the
  archive (the new event takes the archived event's date, so an old story isn't shown as new).
"""
from __future__ import annotations

import json
import re
from datetime import timedelta

from common import iso, log, parse_time
from merge import FAMILY, MIN_SHARED, _absorb, _names, _words

MIN_INTERVAL = timedelta(minutes=30)
RETRY = timedelta(minutes=15)
LOOKBACK = timedelta(hours=72)
PAIR_WINDOW = timedelta(hours=48)
SECOND_LOOK = timedelta(hours=6)
VIOLENCE_OVERLAP = 0.2
TALKS_OVERLAP = 0.2  # with the same (or two shared) parties
TALKS_BARE_OVERLAP = 0.4  # when either event lists no parties
MAX_GROUP = 20
MAX_EVENTS = 60
MAX_CALLS_PER_RUN = 3
EXTRA_CALLS_FLOOR = 100  # a second or third call in one run only while this many calls are left today
NEW_HOURS = 36       # events this recent are compared with older ones (late follow-ups)
LATE_DAYS = 14       # how far back
LATE_OVERLAP = 0.4   # strikes, fighting, talks: wording an older event needs, plus the same names
LATE_CANDIDATES = 2  # older events compared per new event
KEEP_DAYS = 7
# Families that can describe the same incident (a drone strike reported as an explosion).
GROUP = {"strike": "violence", "ground": "violence", "naval": "naval", "deployment": "deployment",
         "hybrid": "hybrid", "incursion": "incursion", "diplomacy": "talks", "legal": "talks"}
WHOLE = {"naval", "deployment", "hybrid", "incursion"}  # shown as one group per country
STATEMENT_KINDS = {"hybrid", "deployment"}  # with parties named, also grouped with talks
# Order of questions: groups holding a pair never asked about go first, second looks after (the
# waiting-since times sort within each); prefixes of the sort key.
NEW, SECOND = "0|", "1|"
# An explosion whose summary speaks of sabotage (or of what saboteurs hit) is also grouped with the
# country's hybrid events: one Syrian pipeline fire came in as sabotage from some outlets and as an
# explosion from Reuters, and the two were never compared.
SABOTAGE_RE = re.compile(r"\b(?:sabotage|saboteurs?|arson|pipelines?|cables?|railways?|rail line|substation|power station)\b", re.I)

PROMPT = """You check a live conflict map for duplicates. Each case is a list of events from the map in the same country, or diplomatic and legal events between the same parties, each with an id, its summary, place, and time.

Group the events that describe the same specific incident or statement: the same arrests, the same strike, the same seizure, the same exercise, the same announcement, the same meeting, visit or call, the same vote, filing or ruling, including follow-up coverage of it over the following days (new details, reactions, questioning of suspects, denials). Places can differ (a city, the base it is about, the capital that spoke, or the whole country) and wording can differ.
Reports often tell one event from different angles or with different details (who gets the money, what was said first, who attended); that is still one event.
Keep events apart when they are separate incidents that resemble each other (two strikes on the same city, two drills, arrests in two different cases, two meetings between the same countries), when one is only background to the other, or when you are unsure. For meetings, statements and legal steps, a response by another government or body is its own event (a third country criticizing a meeting is not the meeting).

For each group, also write "summary": one neutral sentence of at most 25 words stating what the events agree on, taking the most recent figures they give (a death toll that rose). Use only facts in the summaries; keep attributions ("Russian MoD claims", "reportedly") where the events have them; never add a number, place, or name they don't give.

Reply with one JSON object and nothing else, with one entry for every case. In "groups" list only groups of two or more ids; events in no group stay separate. Use "groups": [] when a case has no duplicates:
{"results": [{"i": <case number>, "groups": [{"ids": ["<id>", "<id>", ...], "summary": "<one sentence>"}, ...]}]}"""


def _key(a: str, b: str) -> str:
    return "|".join(sorted((a, b)))


def _overlap(e: dict, f: dict) -> float:
    places = _words(" ".join(str(x.get("place") or "") for x in (e, f)))
    a, b = _words(e.get("summary")) - places, _words(f.get("summary")) - places
    return len(a & b) / min(len(a), len(b)) if a and b else 0.0


def _linked(e: dict, f: dict, group: str) -> bool:
    if abs(parse_time(e["time"]) - parse_time(f["time"])) > PAIR_WINDOW:
        return False
    if group == "talks":
        a, b = set(e.get("parties") or []), set(f.get("parties") or [])
        if a and b:
            return (a <= b or b <= a or len(a & b) >= 2) and _overlap(e, f) >= TALKS_OVERLAP
        return _overlap(e, f) >= TALKS_BARE_OVERLAP
    return group in WHOLE or _overlap(e, f) >= VIOLENCE_OVERLAP


def _answers(v: dict | None) -> int:
    """How many times a pair has been answered (entries from before the count was kept: once)."""
    return int(v.get("n", 1)) if v else 0


def _settled(v: dict | None, now) -> bool:
    """A pair needs no (further) question: judged the same, judged different twice, or judged
    different less than SECOND_LOOK ago."""
    if not v:
        return False
    if v.get("same") or _answers(v) >= 2:
        return True
    return now - (parse_time(v.get("at")) or now) < SECOND_LOOK


def groups(events: list[dict], judged: dict, now) -> list[list[dict]]:
    """Groups worth asking about (some pair in them not settled yet), longest waiting first."""
    return [part for _, part in sorted(_due(events, judged, now), key=lambda w: w[0])]


def _due(events: list[dict], judged: dict, now) -> list[tuple[str, list[dict]]]:
    """(waiting since, group) for every group with a pair not settled yet."""
    since = now - LOOKBACK
    buckets: dict[tuple, list[dict]] = {}
    for e in events:
        if e.get("wave") or e.get("alert") or (parse_time(e.get("time")) or since) <= since:
            continue
        g = GROUP.get(FAMILY.get(e["type"]))
        if g and g != "talks" and e.get("country"):
            buckets.setdefault((e["country"], g), []).append(e)
            if e["type"] == "explosion" and SABOTAGE_RE.search(e.get("summary") or ""):
                buckets.setdefault((e["country"], "hybrid"), []).append(e)
        # talks are linked by who takes part, wherever they were pinned
        if g == "talks" or (g in STATEMENT_KINDS and e.get("parties")):
            buckets.setdefault(("", "talks"), []).append(e)
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
            for part in _chunks(sorted(comp, key=lambda e: e["time"])):
                open_pairs = [judged.get(_key(a["id"], b["id"])) for i, a in enumerate(part) for b in part[i + 1:]]
                waits = [_waiting_since(v, a, b)
                         for (a, b), v in zip(((a, b) for i, a in enumerate(part) for b in part[i + 1:]), open_pairs)
                         if not _settled(v, now)]
                if len(part) > 1 and waits:
                    # groups with a pair never asked about go before second looks (NEW / SECOND)
                    fresh = any(v is None for v in open_pairs)
                    out.append(((NEW if fresh else SECOND) + min(waits), part))
    # The group whose question has waited longest goes first (see groups). Biggest-first let the
    # big diplomacy groups, which gain a new event every few runs, keep a small group (two Belgian
    # reports of one story) waiting until it left the window.
    return out


def late_cases(events: list[dict], history: list[dict], judged: dict, now) -> list[tuple[str, list[dict]]]:
    """(waiting since, [older, newer]) pairs: an event from the last NEW_HOURS and an older event
    of the same kind and country (or parties, for talks) beyond PAIR_WINDOW, up to LATE_DAYS back,
    with similar wording. Older events come from the working set and the archive (`history`)."""
    new_since, old_since = now - timedelta(hours=NEW_HOURS), now - timedelta(days=LATE_DAYS)
    ok = lambda e: not e.get("wave") and not e.get("alert") and GROUP.get(FAMILY.get(e.get("type")))  # noqa: E731
    fresh = [e for e in events if ok(e) and (parse_time(e.get("time")) or old_since) >= new_since]
    older = [e for e in events + history if ok(e) and (parse_time(e.get("time")) or now) >= old_since]
    out = []
    for n in fresh:
        g, nt = GROUP[FAMILY[n["type"]]], parse_time(n["time"])
        scored = []
        for o in older:
            if o["id"] == n["id"] or nt - (parse_time(o["time"]) or nt) <= PAIR_WINDOW:
                continue
            og = GROUP[FAMILY[o["type"]]]
            if g == "talks" or og == "talks":
                if not (g == og == "talks" and _linked({**o, "time": n["time"]}, n, "talks")):
                    continue
            elif og != g or not n.get("country") or o.get("country") != n.get("country"):
                continue
            ov = _overlap(o, n)
            if g in WHOLE:
                # compared whatever their wording within the window, so here too: the closest ones
                # (the RAF Fairford arrests, told again in other words, shared 15%)
                if _shared(o, n) >= 1:
                    scored.append((ov, o))
                continue
            a, b = sorted((_names(o.get("summary")), _names(n.get("summary"))), key=len)
            if ov >= LATE_OVERLAP and _shared(o, n) >= MIN_SHARED and a <= b:
                scored.append((ov, o))
        for _, o in sorted(scored, key=lambda x: -x[0])[:LATE_CANDIDATES]:
            v = judged.get(_key(o["id"], n["id"]))
            if not _settled(v, now):
                out.append(((NEW if v is None else SECOND) + n["time"], [o, n]))
    return out


def _shared(e: dict, f: dict) -> int:
    places = _words(" ".join(str(x.get("place") or "") for x in (e, f)))
    return len((_words(e.get("summary")) - places) & (_words(f.get("summary")) - places))


def _waiting_since(v: dict | None, a: dict, b: dict) -> str:
    """When a pair became due: when the later of the two arrived, or when its second look fell due."""
    if v:
        at = parse_time(v.get("at"))
        if at:
            return iso(at + SECOND_LOOK)
    return max(a["time"], b["time"])


def _chunks(comp: list[dict]) -> list[list[dict]]:
    """A group too big to show at once, in time order, as overlapping runs of MAX_GROUP (each
    event is shown next to the ones reported around the same time; keeping only the newest
    left the first reports of a story out)."""
    if len(comp) <= MAX_GROUP:
        return [comp]
    step = MAX_GROUP // 2
    starts = list(range(0, len(comp) - MAX_GROUP, step)) + [len(comp) - MAX_GROUP]
    return [comp[i:i + MAX_GROUP] for i in starts]


def _fold(events: list[dict], same: list[tuple[str, str]], skip: set[str],
          history: list[dict] | None = None) -> tuple[list[dict], list[dict]]:
    """Fold events judged the same into the earliest. An event judged the same as an archived one
    (no longer in the working set) takes the archived event's date instead: the story is old."""
    archived = {h["id"]: h for h in history or []}
    by_id = {e["id"]: e for e in events}
    for a, b in same:
        old, new = (a, b) if a in archived else (b, a) if b in archived else (None, None)
        if old and new in by_id and by_id[new]["time"] > archived[old]["time"]:
            log(f"[dedupe] {by_id[new]['summary'][:70]!r} is a late report of {archived[old]['summary'][:70]!r} "
                f"({archived[old]['time'][:10]}); dated to then")
            by_id[new]["time"] = archived[old]["time"]
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
        skip: set[str], history: list[dict] | None = None, share: int | None = None) -> tuple[list[dict], list[dict]]:
    """Returns (events, the events folded away). `ask` is extract.ask_json; `history` holds archived
    events no longer in the working set (compared with new events as late follow-ups); `share` is
    how many calls this check may still make now under its paced daily share (extract.share_left)."""
    st = state.setdefault("dedupe", {})
    cutoff = iso(now - timedelta(days=KEEP_DAYS))
    st["judged"] = judged = {k: v for k, v in (st.get("judged") or {}).items() if v.get("at", "") >= cutoff}
    # pairs already judged the same are folded again if both came back (e.g. restored by a check)
    events, folded = _fold(events, [tuple(k.split("|")) for k, v in judged.items() if v.get("same")], skip)
    done, failed = parse_time(st.get("attempt")), parse_time(st.get("failed"))
    # While groups are waiting, every run asks (a 15-minute wait against runs 13 minutes apart
    # skipped every other run); the budget floors below still apply. After a failed call (the
    # model down), wait RETRY; when nothing was left waiting, check again after MIN_INTERVAL.
    wait = timedelta(0) if st.get("backlog") else MIN_INTERVAL
    if (done and now - done < wait) or (failed and now - failed < RETRY):
        return events, folded
    have = {e["id"] for e in events}
    history = [h for h in (history or []) if h.get("id") not in have and h.get("id") not in skip]
    for call in range(MAX_CALLS_PER_RUN):
        live = [e for e in events if e["id"] not in skip]
        due = sorted(_due(live, judged, now) + late_cases(live, history, judged, now), key=lambda w: w[0])
        cases, size = [], 0
        for _, comp in due:
            if size + len(comp) > MAX_EVENTS:
                continue
            cases.append(comp)
            size += len(comp)
        if not cases:
            st["backlog"] = False
            break
        if remaining - call < int(settings.get("dedupe_min_calls", 10)):
            log(f"[dedupe] skipped: only {remaining - call} model calls left today")
            break
        if call and remaining - call < EXTRA_CALLS_FLOOR:
            break  # the backlog waits for the next run rather than eat into extraction
        if share is not None and call >= share:
            if not call:
                log("[dedupe] waiting: its share of today's model calls is used for now")
            break
        payload = [{"i": n, "events": [{"id": e["id"], "summary": e.get("summary"), "place": e.get("place"),
                                        "time": e.get("time")} for e in comp]} for n, comp in enumerate(cases)]
        reply = ask(PROMPT, json.dumps({"cases": payload}, ensure_ascii=False), state, settings, now, max_tokens=3000,
                    purpose="dedupe")
        if not isinstance(reply, dict) or not isinstance(reply.get("results"), list):
            st["failed"] = iso(now)
            log(f"[dedupe] no model answer; will try again in {int(RETRY.total_seconds() // 60)} minutes")
            break
        st["attempt"] = iso(now)
        st["backlog"] = len(cases) < len(due)
        st.pop("failed", None)
        same, heads = _read(reply, cases, judged, now)
        events, more = _fold(events, same, skip, history)
        _headlines(events, heads)
        log(f"[dedupe] asked about {len(cases)} groups ({size} events), folded {len(more)} events")
        folded += more
        if not st["backlog"]:
            break
    return events, folded


def _read(reply: dict, cases: list[list[dict]], judged: dict, now) -> tuple[list[tuple[str, str]], list]:
    """Record the model's answers. Returns (pairs judged the same, [(member events, headline)])."""
    same, heads = [], []
    answered = set()
    now_seen: set[str] = set()  # a pair shown in two overlapping cases counts as one answer
    for res in reply["results"]:
        n_case = _case_number(res)
        if n_case is None or not 0 <= n_case < len(cases) or n_case in answered:
            continue
        answered.add(n_case)
        comp = cases[n_case]
        ids = {e["id"] for e in comp}
        where, texts = {}, {}
        for n, (grp, text) in enumerate(_groups(res)):
            texts[n] = text
            for i in grp:
                i = str(i).strip()
                if i in ids and i not in where:
                    where[i] = n
        for x, a in enumerate(comp):
            for b in comp[x + 1:]:
                hit = a["id"] in where and where.get(a["id"]) == where.get(b["id"])
                k = _key(a["id"], b["id"])
                if k in now_seen:
                    judged[k]["same"] = judged[k]["same"] or hit
                else:
                    now_seen.add(k)
                    judged[k] = {"same": hit, "at": iso(now), "n": _answers(judged.get(k)) + 1}
                if hit:
                    same.append((a["id"], b["id"]))
        for n in sorted(set(where.values())):
            members = [e for e in comp if where.get(e["id"]) == n]
            if len(members) > 1:
                log("[dedupe] same story: " + " + ".join(repr(e["summary"][:60]) for e in members))
                heads.append((members, texts.get(n)))
    if len(answered) < len(cases):
        sample = json.dumps(reply["results"][:1], ensure_ascii=False)[:200]
        log(f"[dedupe] {len(cases) - len(answered)} of {len(cases)} groups got no usable answer "
            f"(asked again later); first result looked like: {sample}")
    return same, heads


_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


def _headlines(events: list[dict], heads: list) -> None:
    """Give each folded event the model's combined headline, when it keeps to the members' facts
    (every number in it appears in one of their summaries or casualty counts)."""
    by_id = {e["id"]: e for e in events}
    for members, text in heads:
        keep = next((by_id[m["id"]] for m in sorted(members, key=lambda m: (m["time"], m["id"])) if m["id"] in by_id), None)
        text = " ".join(str(text or "").split())
        if keep is None or not text or len(text) > 220:
            continue
        known = " ".join([m.get("summary") or "" for m in members]
                         + [str(m.get(k)) for m in members for k in ("killed", "injured") if m.get(k) is not None])
        if all(n in _NUMBER.findall(known) for n in _NUMBER.findall(text)):
            keep["headline"] = text
        else:
            log(f"[dedupe] combined headline dropped (a number the events don't give): {text[:90]!r}")


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


def _groups(res: dict) -> list[tuple[list, str | None]]:
    """The id groups of a result, with the combined headline when given: lists of ids, or objects
    holding them ({"ids": [...], "summary": "..."})."""
    out = []
    for g in res.get("groups") or res.get("same") or []:
        text = None
        if isinstance(g, dict):
            text = g.get("summary") or g.get("headline")
            g = g.get("ids") or g.get("events") or []
        if isinstance(g, list) and len(g) > 1:
            out.append((g, text if isinstance(text, str) else None))
    return out
