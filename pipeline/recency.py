"""Catch old news that arrives with a fresh date.

News feeds, Google News above all, sometimes list an old article with a new date (a site
republished or updated it), and a bare headline like "US submarine sinks Iranian ship" gives the
extraction model no way to tell. So every event built only from news feeds is checked once:

  1. Search Google News for the event's key words, limited to articles published at least
     AGE_DAYS before the event.
  2. No older coverage: the event is new.
  3. Older coverage found: the model sees the event and the dated older headlines and says
     whether one of them reports the same specific incident. A similar but new incident
     (another strike on the same city) is not old news.
  4. The event is dropped only when the model names the matching older headline. Every drop
     is logged with that headline.

Searches or model calls that fail leave the event on the map and are retried on later runs.
"""
from __future__ import annotations

import json
import re
from datetime import timedelta
from urllib.parse import quote_plus

import feedparser

from common import clean_text, iso, log, parse_time
from sources.rss import _entry_time

AGE_DAYS = 3
CHECKS_PER_RUN = 15
MAX_TRIES = 3
MAX_OLDER = 8
_STOP = set("""a an the of in on at to for and or by with as is are was were be been its it this that
from after over into amid near during against about says said say claims claimed claim reports
reported report according officials official state states stated warns warned new""".split())

PROMPT = """You check whether news reports on a live conflict map are old stories that resurfaced with a fresh date.

For each case you get a report (its summary, place, and the date it was listed) and older news headlines, each with its date, found by a search for the same key words.

Answer "old": true only if one of the older headlines reports the same specific incident or statement as the report: the same sinking, the same strike, the same warning, the same meeting. The report is then old news, unless it adds significant new facts about that incident (new casualty figures, a new attribution, a new official response).
Answer "old": false when the report describes a new incident that merely resembles older ones (another strike on the same city, another round of talks, another drone incursion), when the older headlines are about something else, or when you are unsure.

Reply with one JSON object and nothing else:
{"results": [{"i": <case number>, "old": true or false, "match": <number of the matching older headline, or null>}]}"""


def _query(summary: str) -> str:
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z'-]+", summary or "") if w.lower() not in _STOP]
    return " ".join(words[:9])


def _older_headlines(session, e: dict) -> list[dict] | None:
    """Headlines about the event's key words dated at least AGE_DAYS before it; None on failure."""
    t = parse_time(e.get("time"))
    q = _query(e.get("summary", ""))
    if not t or len(q.split()) < 3:
        return []
    cutoff = t - timedelta(days=AGE_DAYS)
    url = (f"https://news.google.com/rss/search?q={quote_plus(q + ' before:' + cutoff.strftime('%Y-%m-%d'))}"
           "&hl=en-US&gl=US&ceid=US%3Aen")
    try:
        r = session.get(url, timeout=20)
        r.raise_for_status()
        feed = feedparser.parse(r.content)
    except Exception as exc:  # noqa: BLE001
        log(f"[recency] search failed: {exc}")
        return None
    older = []
    for entry in feed.entries[:40]:
        when = _entry_time(entry)
        title = clean_text(entry.get("title", ""))
        if when and title and when < cutoff:
            older.append({"title": title[:200], "date": when.strftime("%Y-%m-%d")})
        if len(older) >= MAX_OLDER:
            break
    return older


def _needs_check(e: dict) -> bool:
    return (not e.get("checked") and not e.get("wave") and not e.get("alert")
            and bool(e.get("reports")) and all(r.get("platform") == "rss" for r in e["reports"]))


def check(events: list[dict], new_ids: set[str], session, ask, state: dict, settings: dict, now) -> list[dict]:
    """Return events without the ones shown to be old news. `ask` is extract.ask_json."""
    todo = [e for e in events if _needs_check(e)]
    todo.sort(key=lambda e: e.get("time") or "", reverse=True)
    todo = sorted(todo, key=lambda e: e["id"] not in new_ids)[:CHECKS_PER_RUN]  # this run's events, then newest
    cases = []
    for e in todo:
        e["checks"] = e.get("checks", 0) + 1
        older = _older_headlines(session, e)
        if older is None:
            if e["checks"] >= MAX_TRIES:
                e["checked"] = True
            continue
        if not older:
            e["checked"] = True
            continue
        cases.append((e, older))
    if not cases:
        return events

    payload = [{"i": n, "report": e["summary"], "place": e.get("place"), "listed": (e.get("time") or "")[:10],
                "older": [{"n": k, **h} for k, h in enumerate(older)]} for n, (e, older) in enumerate(cases)]
    reply = ask(PROMPT, json.dumps({"cases": payload}, ensure_ascii=False), state, settings, now, max_tokens=1500)
    if not isinstance(reply, dict) or not isinstance(reply.get("results"), list):
        log("[recency] no model answer; will retry next run")
        for e, _ in cases:
            if e["checks"] >= MAX_TRIES:
                e["checked"] = True
        return events

    drop = set()
    for res in reply["results"]:
        if not isinstance(res, dict) or not isinstance(res.get("i"), int) or not 0 <= res["i"] < len(cases):
            continue
        e, older = cases[res["i"]]
        e["checked"] = True
        m = res.get("match")
        if res.get("old") is True and isinstance(m, int) and 0 <= m < len(older):
            drop.add(e["id"])
            log(f"[recency] old news, dropped: {e['summary']!r} matches {older[m]['title']!r} ({older[m]['date']})")
            state.setdefault("dropped_as_old", []).append({
                "summary": e["summary"], "listed": e.get("time"), "match": older[m]["title"],
                "match_date": older[m]["date"], "at": iso(now)})
    state["dropped_as_old"] = state.get("dropped_as_old", [])[-100:]
    return [e for e in events if e["id"] not in drop]
