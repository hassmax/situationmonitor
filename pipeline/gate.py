"""Gatekeeper: every new event is checked for duplicates and old news in the run that brings it in.

The same-story check (dedupe.py) and the old-story check (recency.py) work through a queue, a
little each run, so a new event used to go on the map, and set off its banner and Telegram alert,
before either had looked at it: on 2026-10-13 one drone strike near Koya was on the map four times
and 697 events waited for the same-story check. So every event created in a run now goes through
this step before the run publishes anything (the owner, 2026-10-13: "Every alert should be
exhaustively analyzed to make sure its not duplicate or old"):

1. Candidates (no model): the events most like it, the way dedupe.py finds them (TF-IDF over the
   summaries, the same rules on place and time, archived events within LATE_DAYS for late
   follow-ups), plus events within PAIR_WINDOW that the meaning-matching model (embed.py) finds
   close (EMB_NEAR in the same country or sea, EMB_FAR anywhere): "airstrike" and "missile attack"
   reports of one strike share few words. Near-identical statements and deals fold at once
   (dedupe.automatic).
2. Older coverage (no model): an event resting only on news reports gets recency.py's two
   Google News searches (coverage around its date, and two weeks and more before), SEARCH_WORKERS
   at a time. A strike, attack or other incident resting only on news reports is also shown the
   map's own events like it from PAIR_WINDOW to EARLIER_DAYS before it, near it ("earlier"): the
   searches and the candidates both missed those days. On 2026-10-25 a Khmer Times article, "13
   dead, 300 injured in Riyadh airport attack", relisted the Houthi strike on Riyadh airport the
   map had shown on 17 October (13 dead): eight days on, it was too late to be a candidate (a new
   attack days later is its own incident, dedupe.different_times) and too recent for the older
   search (recency.AGE_DAYS), so it went on the map as a new attack.
3. One question (CASES_PER_CALL events a call, at most MAX_CALLS a run) for the events with
   candidates, older coverage or earlier events: is it the same incident as a candidate, and is
   it an old story that resurfaced (an older headline or an earlier event on the map)? Gemini
   first, Cerebras if Gemini gives no answer (extract.GEMINI_FIRST), within the share
   `gate_daily_max`. Events with neither pass at once.
4. A duplicate is folded into the event it repeats (the earliest keeps its id), so it never
   appears as new and never alerts; an old story is dropped (state["dropped_as_old"], as
   recency.py does) when the model names the matching older headline or earlier event. Answers
   are recorded where the two checks keep theirs, so neither asks again.

An event the step could not finish (no model answered, or more new events than one run takes) is
published at once, marked `unchecked`: on the map, but no banner or Telegram alert. It is checked
in the next run that can; after GIVE_UP it is left as it is (`gate: "gave_up"`) and stays unalerted.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import dedupe
import embed
import recency
from common import iso, log, parse_time, short_hash

CASES_PER_CALL = 12
MAX_CALLS = 3
MAX_SEARCHED = 40       # events searched for older coverage a run (two searches each)
SEARCH_WORKERS = 4
GIVE_UP = timedelta(hours=6)
EMB_NEAR = 0.82         # meaning similarity for a candidate in the same country or sea ...
EMB_FAR = 0.90          # ... and anywhere else
CANDIDATES = 5
EARLIER = 3             # earlier map events shown for the old-story question ...
EARLIER_DAYS = recency.AGE_DAYS  # ... from PAIR_WINDOW up to this many days before (the older search starts here)

_DEDUPE_RULES = dedupe.PROMPT.split("\n\n", 1)[1].rsplit("\n\nWhen you list any", 1)[0]
_OLD_RULES = recency.PROMPT.split("\n\n", 2)[2].rsplit("\n\nReply with", 1)[0]

PROMPT = f"""You check new events on a live conflict map before they are published. Each case is one new event (its id, kind, summary, place, time and report excerpts) with:
- "candidates": events already on the map that look like it, each with an id, kind, summary, place and time. Events are often filed under different kinds and places by different outlets: one strike as an airstrike, a missile attack and shelling; a missile test as arms production; an arms sale as diplomacy; a city, the base it is about, the capital that spoke, or the whole country. The kind and the place don't decide it.
- "recent" and "older": headlines a news search found for its key words, around the event's date and weeks or months before (either may be missing).
- "earlier": incidents already on the map from 2 to 14 days before it, near it, that look like it (may be missing).

Question 1, duplicates: list the candidates that describe the same specific incident or statement as the event.
{_DEDUPE_RULES}

Question 2, old news (only when "older" headlines or "earlier" events are given): is the event an old story that resurfaced with a fresh date?
{_OLD_RULES}
The same goes for the "earlier" events: outlets relist wire stories days later with a fresh date. Answer "old": true with "earlier": <its number> when the event reports the same specific incident as an earlier one (the same strike on the same site, with the same or nearly the same figures, or only new details about it) and nothing in its reports or the recent headlines shows it happening again now. Another attack on the same place is not old: a report of a new attack, a new date, or a new wave is a new incident.
If the event is the same incident as a candidate, answer "old": false: it is coverage of an event already on the map.

When you list any candidates, also give:
- "type": the kind, of those the event and the listed candidates were filed under, that best fits what happened (a missile test is "weapons_test", not an attack or arms production; an approved arms sale is "arms_transfer"; a strike that killed people is "airstrike" or "missile_drone" over "diplomacy").
- "summary": one neutral sentence of at most 25 words stating what they agree on, with the most recent figures; only facts in the summaries, keeping attributions ("Russian MoD claims", "reportedly"); never add a number, place, or name they don't give.

Reply with one JSON object and nothing else, one entry for every case:
{{"results": [{{"i": <case number>, "same": ["<candidate id>", ...], "type": "<kind or null>", "summary": "<one sentence or null>", "old": true or false, "match": <number of the matching older headline, or null>, "earlier": <number of the matching earlier event, or null>}}]}}"""


def _index(v, items: list) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) and 0 <= v < len(items) else None


def _pending(e: dict) -> bool:
    return e.get("gate") == "pending"


def unchecked(e: dict) -> bool:
    """Published as `unchecked`: on the map without a banner or Telegram alert."""
    return e.get("gate") in ("pending", "gave_up")


def _texts(e: dict) -> list[str]:
    return dedupe._texts(e)[:3]


def _meaning_candidates(e: dict, pool: list[dict], vecs: dict | None) -> list[tuple[float, dict]]:
    """Events within PAIR_WINDOW whose meaning is close, by the same place and time rules."""
    if not vecs:
        return []
    out = []
    for f in pool:
        if (f["id"] == e["id"] or dedupe.separate_incidents(e, f) or f.get("alert") or dedupe.apart(e, f)
                or dedupe._gap(e, f) > dedupe.PAIR_WINDOW):
            continue
        s = embed.similarity(_texts(e), _texts(f), vecs)
        if s is not None and s >= (EMB_NEAR if dedupe.near(e, f) else EMB_FAR):
            out.append((s, f))
    out.sort(key=lambda x: -x[0])
    return out[:CANDIDATES]


def _earlier(e: dict, pool: list[dict], vec: dict) -> list[dict]:
    """The map's incidents like e, near it, from PAIR_WINDOW to EARLIER_DAYS before it, for an incident
    resting only on news reports: an old story relisted days later. Shown for the old-story
    question only, never folded into (a new attack days later is its own incident)."""
    if not _needs_search(e) or dedupe._family(e) not in dedupe.VIOLENCE:
        return []
    t = parse_time(e.get("time"))
    out = []
    for f in pool:
        ft = parse_time(f.get("time"))
        if (f["id"] == e["id"] or f.get("alert") or not t or not ft or dedupe._family(f) not in dedupe.VIOLENCE
                or not dedupe.PAIR_WINDOW < t - ft <= timedelta(days=EARLIER_DAYS)
                or not dedupe.near(e, f) or dedupe.apart(e, f)):
            continue
        a, b = vec.get(e["id"], [{}]), vec.get(f["id"], [{}])
        if a[0].keys().isdisjoint(b[0]):
            continue
        s = dedupe._score(a, b)  # word overlap, as for late follow-ups: meaning vectors cover PAIR_WINDOW only
        if s >= dedupe.LATE_FLOOR:
            out.append((s, f))
    out.sort(key=lambda x: -x[0])
    return [f for _, f in out[:EARLIER]]


def _same_figures(e: dict, f: dict) -> bool:
    """The same death or injury count as an earlier incident: the Riyadh airport relisting gave the
    17 October strike's 13 dead."""
    return any(isinstance(e.get(k), int) and e[k] > 0 and e[k] == f.get(k) for k in ("killed", "injured"))


def _window(e: dict, pool: list[dict]) -> list[dict]:
    t = parse_time(e.get("time"))
    return [f for f in pool if t and parse_time(f.get("time")) and abs(parse_time(f["time"]) - t) <= dedupe.PAIR_WINDOW]


def _needs_search(e: dict) -> bool:
    return (e.get("checked") != recency.CHECK_VERSION and not e.get("alert") and bool(e.get("reports"))
            and all(r.get("platform") == "rss" for r in e["reports"]))


def _show(e: dict, cands: list, found, earlier: list | None = None) -> dict:
    case = {"event": dedupe._show(e), "candidates": [dedupe._show(f) for _, f in cands]}
    if earlier:
        case["earlier"] = [{"n": k, "kind": f.get("type"), "summary": f.get("summary"), "place": f.get("place"),
                            "time": (f.get("time") or "")[:16]} for k, f in enumerate(earlier)]
    if found:
        recent, older = found
        day = lambda r: r["when"].strftime("%Y-%m-%d")  # noqa: E731
        case["listed"] = (e.get("time") or "")[:10]
        case["recent"] = [{"title": r["title"], "date": day(r)} for r in recent[:5]]
        case["older"] = [{"n": k, "title": r["title"], "date": day(r)} for k, r in enumerate(older)]
    return case


# Reports folded into older incidents by the first version of the gatekeeper (13 October 2026, from
# about 02:50 UTC), before dedupe.different_times: read again as new posts, once.
REPAIR_VERSION = 1
REPAIR_FROM = "2026-10-13T02:50:00Z"


def take_back_late_folds(events: list[dict], state: dict) -> list[dict]:
    """Reports that joined a strike, fight or other incident more than PAIR_WINDOW after it happened,
    since REPAIR_FROM, are taken out and returned as items for the extraction queue (each from its
    own summary, credited to its source), so the fixed rules file them: a new incident becomes its
    own event, and late coverage of the old one rejoins it by its occurrence date."""
    if state.get("gate_repair", 0) >= REPAIR_VERSION:
        return []
    state["gate_repair"] = REPAIR_VERSION
    since = parse_time(REPAIR_FROM)
    items = []
    for e in events:
        if dedupe._family(e) not in dedupe.VIOLENCE or e.get("alert") or e.get("wave") or not e.get("reports"):
            continue
        t = parse_time(e["time"])
        late = [r for r in e["reports"] if parse_time(r["time"]) >= since and parse_time(r["time"]) - t > dedupe.PAIR_WINDOW]
        if not late or len(late) == len(e["reports"]):
            continue
        gone = {id(r) for r in late}
        e["reports"] = [r for r in e["reports"] if id(r) not in gone]
        e["updated"] = max(r["time"] for r in e["reports"])
        e.pop("headline", None)
        for r in late:
            items.append({"id": short_hash("gate-repair", r.get("url"), r.get("summary")),
                          "source_id": r.get("source_id") or "gate-repair", "source": r.get("source"),
                          "platform": r.get("platform"), "kind": r.get("kind", "news"), "side": r.get("side"),
                          "group": r.get("group") or r.get("source"), "weight": int(r.get("weight", 2)),
                          "prefilter": False, "url": r.get("url"), "text": r.get("summary") or "", "time": r["time"],
                          "max_age_h": 24 * 30})
    if items:
        log(f"[gate] {len(items)} reports taken out of older incidents they were folded into, to be read again")
    return items


def check(events: list[dict], new_ids: set[str], session, ask, state: dict, settings: dict, now,
          skip: set[str], history: list[dict] | None = None) -> tuple[list[dict], list[dict]]:
    """Returns (events, the events folded away). `ask` is extract.ask_json; `history` holds archived
    events (dedupe.LATE_DAYS) for late follow-ups."""
    st = state.setdefault("gate", {})
    day = now.strftime("%Y-%m-%d")
    if st.get("date") != day:
        st.update(date=day, checked=0, folded=0, old=0, unchecked=0, calls=0)
    for e in events:
        if e["id"] in new_ids and "gate" not in e:
            e["gate"], e["gate_since"] = "pending", iso(now)
        if _pending(e) and now - (parse_time(e.get("gate_since")) or now) > GIVE_UP:
            e["gate"] = "gave_up"
            log(f"[gate] left unchecked after {GIVE_UP.total_seconds() / 3600:.0f} h: {e.get('summary', '')[:80]!r}")
    todo = [e for e in events if _pending(e) and e["id"] not in skip]
    if not todo:
        return events, []
    for e in [e for e in todo if e.get("alert")]:
        e["gate"] = "ok"  # drone and missile warnings: grouped by country and day (merge.py), never old
    todo = [e for e in todo if _pending(e)]
    todo.sort(key=lambda e: (-(e.get("severity") or 1), e.get("time") or ""))  # most serious first

    dd = state.setdefault("dedupe", {})
    judged = dd.setdefault("judged", {})
    history = [h for h in (history or []) if h["id"] not in {e["id"] for e in events} and h["id"] not in skip]
    pool = [e for e in events if e["id"] not in skip and not e.get("alert")]
    vec = dedupe._vectors(pool + [h for h in history if not h.get("alert")])
    # meaning vectors only for the events near the new ones in time: a few hundred texts a run
    near_pool = {f["id"]: f for e in todo for f in _window(e, pool)}
    vecs = embed.vectors([t for f in list(near_pool.values()) + todo for t in _texts(f)])

    cases, auto, asked = {}, [], set()
    for e in todo:
        found = dedupe.candidates(e, pool, vec) + dedupe.candidates(e, pool + history, vec, late=True)
        found += _meaning_candidates(e, pool, vecs)
        best: dict[str, tuple[float, dict]] = {}
        for s, f in found:
            if f["id"] not in best or s > best[f["id"]][0]:
                best[f["id"]] = (s, f)
        mine = []
        for s, f in sorted(best.values(), key=lambda x: -x[0])[:CANDIDATES]:
            k = dedupe._key(e["id"], f["id"])
            v = judged.get(k)
            if k in asked or (v and not v.get("same")):
                continue  # answered "different" already: no need to ask again here
            asked.add(k)
            if v and v.get("same"):
                auto.append((e, f, s))
            elif dedupe.automatic(e, f, s):
                judged[k] = {"same": True, "at": iso(now), "n": 1, "auto": round(s, 2)}
                auto.append((e, f, s))
            else:
                mine.append((s, f))
        cases[e["id"]] = [e, mine, None, _earlier(e, pool + history, vec)]

    folded: list[dict] = []
    if auto:
        events, more = dedupe._fold(events, [(e["id"], f["id"]) for e, f, _ in auto], skip, history)
        for e, f, s in auto:
            log(f"[gate] same story (near-identical, {s:.2f}): {e['summary'][:60]!r} = {f['summary'][:60]!r}")
        folded += more
    gone = {x["id"] for x in folded}
    for i in list(cases):
        if i in gone:
            cases.pop(i)
        else:
            cases[i][1] = [(s, f) for s, f in cases[i][1] if f["id"] not in gone]
            cases[i][3] = [f for f in cases[i][3] if f["id"] not in gone]

    # older coverage, for events resting on news reports only (two searches each, a few at a time)
    search = [c for c in cases.values() if _needs_search(c[0])][:MAX_SEARCHED]
    if search:
        with ThreadPoolExecutor(SEARCH_WORKERS) as pool_:
            results = list(pool_.map(lambda c: recency._evidence(session, c[0]), search))
        for c, found in zip(search, results):
            if found is None:
                continue  # a search failed: the old-story check (recency.py) tries again later
            c[2] = found
            recent, older = found
            c[0]["coverage"] = {"current": len(recent), "older": len(older)}
            if not older:
                c[0]["checked"] = recency.CHECK_VERSION
                c[2] = None
    for e, _, _, earlier in cases.values():
        if earlier:
            e.setdefault("coverage", {})["earlier"] = len(earlier)
            e["coverage"]["repeats"] = sum(_same_figures(e, f) for f in earlier)  # recency.held reads it

    questions = [c for c in cases.values() if c[1] or c[2] or c[3]]
    for e, cands, found, earlier in cases.values():
        # nothing like it on the map and no older coverage (or none needed); an event whose search
        # failed or didn't fit this run stays pending
        if not cands and not found and not earlier and not _needs_search(e):
            e["gate"] = "ok"
    drop, calls = set(), 0
    while questions and calls < MAX_CALLS:
        batch, questions = questions[:CASES_PER_CALL], questions[CASES_PER_CALL:]
        payload = [{"i": n, **_show(e, cands, found, earlier)} for n, (e, cands, found, earlier) in enumerate(batch)]
        reply = ask(PROMPT, json.dumps({"cases": payload}, ensure_ascii=False), state, settings, now,
                    max_tokens=8000, purpose="gate")
        calls += 1
        st["calls"] = st.get("calls", 0) + 1
        if not isinstance(reply, dict) or not isinstance(reply.get("results"), list):
            log(f"[gate] no model answer: {len(batch)} new events published unchecked, checked next run")
            break
        same, heads, kinds = [], [], {}
        answered = set()
        for res in reply["results"]:
            n = dedupe._case_number(res)
            if n is None or not 0 <= n < len(batch) or n in answered:
                continue
            answered.add(n)
            e, cands, found, earlier = batch[n]
            ids = {str(i).strip() for i in (res.get("same") or []) if isinstance(i, (str, int))}
            members = [e]
            for _, f in cands:
                k = dedupe._key(e["id"], f["id"])
                hit = f["id"] in ids
                judged[k] = {"same": hit, "at": iso(now), "n": dedupe._answers(judged.get(k)) + 1, "gate": True}
                if hit:
                    same.append((e["id"], f["id"]))
                    members.append(f)
            if len(members) > 1:
                log("[gate] same story: " + " + ".join(f"{m.get('type')} {m['summary'][:55]!r}" for m in members))
                text = res.get("summary") if isinstance(res.get("summary"), str) else None
                heads.append((members, text))
                kind = res.get("type")
                if isinstance(kind, str) and kind in {m.get("type") for m in members}:
                    kinds[e["id"]] = kind
            elif found or earlier:
                older = found[1] if found else []
                if found:
                    e["checked"] = recency.CHECK_VERSION
                m, k = _index(res.get("match"), older), _index(res.get("earlier"), earlier)
                match = (older[m]["title"], older[m]["when"].strftime("%Y-%m-%d")) if m is not None else (
                    (f"the map's event {earlier[k]['id']}: {earlier[k].get('summary')}", (earlier[k].get("time") or "")[:10])
                    if k is not None else None)
                if res.get("old") is True and match:
                    drop.add(e["id"])
                    log(f"[gate] old news, dropped: {e['summary']!r} matches {match[0]!r} ({match[1]})")
                    state.setdefault("dropped_as_old", []).append({
                        "summary": e["summary"], "listed": e.get("time"), "match": match[0],
                        "match_date": match[1], "at": iso(now), "version": recency.CHECK_VERSION, "event": e})
            if not _needs_search(e):
                e["gate"] = "ok"  # else its search failed or didn't fit: searched next run
        events, more = dedupe._fold(events, same, skip, history, kinds)
        dedupe._headlines(events, heads)
        folded += more
        gone = {x["id"] for x in more}
        questions = [c for c in questions if c[0]["id"] not in gone]
        log(f"[gate] asked about {len(batch)} new events, {len(answered)} answered: "
            f"{len(more)} folded as duplicates, {len(drop & {c[0]['id'] for c in batch})} old stories dropped")
    state["dropped_as_old"] = state.get("dropped_as_old", [])[-100:]
    events = [e for e in events if e["id"] not in drop]
    left = [e for e in events if _pending(e)]
    st["checked"] = st.get("checked", 0) + len(todo) - len(left)
    st["folded"] = st.get("folded", 0) + len(folded)
    st["old"] = st.get("old", 0) + len(drop)
    log(f"[gate] {len(todo)} new events: {len(todo) - len(left)} checked, {len(folded)} folded as duplicates, "
        f"{len(drop)} old stories dropped, {len(left)} published unchecked; {calls} model calls"
        f"{'' if vecs else ' (word overlap only)'}")
    return events, folded
