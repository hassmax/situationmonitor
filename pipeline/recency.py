"""Catch old news that arrives with a fresh date.

News feeds, Google News above all, sometimes list an old article with a new date (a site
republished or updated it), and a bare headline like "US submarine sinks Iranian ship" gives the
extraction model no way to tell. So every event built only from news feeds is checked:

  1. Search Google News for the event's key words around the event's own date ("recent") and in
     articles at least AGE_DAYS older ("older").
  2. No older coverage on the topic: the event is new and stays.
  3. Recent headlines that repeat an older headline are republished copies, not current
     coverage, and are set aside. (Current coverage alone used to settle it, but an old story
     that is being talked about again, such as a new claim about a strike months ago, has
     plenty of it.)
  4. The model compares the event with the recent and older headlines and says whether it is
     the same specific incident as an older one. New claims or details about an older incident
     count as old: the map shows incidents when they happened. Another incident of the same
     kind (another strike on the same city) does not. The event is dropped only when the model
     names the matching older headline. Every drop is logged with it.

Model calls: one per run at most, and at most every MODEL_GAP unless MODEL_BATCH events are
waiting, from the shared daily budget.

Held back: a headline like "Yemen's Houthis launched ballistic missiles at Israel" describes an
attack that recurs, so the model can't tell a March article relisted today from a new attack, and
rightly keeps "another incident of the same kind". But a real attack of that size is covered widely
within hours. So a notable event resting on one source, whose search finds older coverage of the
topic and no current coverage from any other outlet, is held off the map (`held`) until a second,
independent source reports it; it is kept, and shown as soon as that happens.

Searches or model calls that fail leave the event on the map and are retried on later runs.
Dropped events are kept in state. When the rule changes (CHECK_VERSION), they are put back and
every event is checked again under the new rule.
"""
from __future__ import annotations

import json
import re
from datetime import timedelta
from urllib.parse import quote_plus

import feedparser

from common import clean_text, iso, log, parse_time
from sources.rss import _entry_time

CHECK_VERSION = 4    # 4: coverage is recorded so a single-source story with only older coverage is held
KEEP_DROPS_FROM = 2  # drops made under this version or later stand (version 3 only drops more)
AGE_DAYS = 14        # older coverage must be at least this much older than the event
RECENT_DAYS = 2      # "current" coverage: within this many days of the event
MODEL_GAP = timedelta(minutes=30)
MODEL_BATCH = 8
CHECKS_PER_RUN = 15
MAX_TRIES = 3
HOLD_MIN_SEVERITY = 2  # holding applies to notable events (a minor local item isn't widely covered anyway)
_STOP = set("""a an the of in on at to for and or by with as is are was were be been its it this that
from after over into amid near during against about says said say claims claimed claim reports
reported report according officials official state states stated warns warned new following""".split())

PROMPT = """You check whether news reports on a live conflict map are old stories that resurfaced with a fresh date.

For each case you get a report (its summary, place, and the date it was listed), headlines from around that date ("recent"), and older headlines from weeks or months before ("older"), all found by a search for the same key words.

Answer "old": true when one of the older headlines reports the same specific incident or statement as the report (the same sinking, the same strike, the same warning, the same vote) and nothing in the recent headlines shows it happening again now. This includes reports that only add new claims, details, or accounts about that earlier incident (who was hurt in it, a new casualty count, someone's account of it): the incident itself is old.
Answer "old": false when the report describes a new incident that resembles older ones (another strike on the same city, another coast guard drill, another vote), when the recent headlines show it happening now, when the older headlines are about something else, or when you are unsure. Similar wording is not enough: many events recur with near-identical headlines.

Reply with one JSON object and nothing else:
{"results": [{"i": <case number>, "old": true or false, "match": <number of the matching older headline, or null>}]}"""


def _words(text: str) -> set[str]:
    return {w.rstrip("s") for w in re.findall(r"[a-z][a-z'-]+", (text or "").lower()) if w not in _STOP}


def _query(summary: str) -> str:
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z'-]+", summary or "") if w.lower() not in _STOP]
    return " ".join(words[:9])


def _headline(title: str) -> str:
    return title.rsplit(" - ", 1)[0].strip() if " - " in title else title


def _search(session, q: str) -> list[dict] | None:
    url = f"https://news.google.com/rss/search?q={quote_plus(q)}&hl=en-US&gl=US&ceid=US%3Aen"
    try:
        r = session.get(url, timeout=20)
        r.raise_for_status()
        feed = feedparser.parse(r.content)
    except Exception as exc:  # noqa: BLE001
        log(f"[recency] search failed: {exc}")
        return None
    out = []
    for entry in feed.entries[:60]:
        when, title = _entry_time(entry), clean_text(entry.get("title", ""))
        if when and title:
            out.append({"title": title[:200], "when": when, "link": entry.get("link") or ""})
    return out


def _on_topic(report_words: set[str], title: str) -> bool:
    hw = _words(_headline(title))
    return bool(report_words and hw) and len(report_words & hw) / min(len(report_words), len(hw)) >= 0.5


def _distinct(results: list[dict]) -> list[dict]:
    """Drop syndicated copies: headlines that are near-identical count once."""
    kept: list[dict] = []
    for r in results:
        w = _words(_headline(r["title"]))
        if not any(len(w & _words(_headline(k["title"]))) / max(1, len(w | _words(_headline(k["title"])))) >= 0.8 for k in kept):
            kept.append(r)
    return kept


def _evidence(session, e: dict):
    """(recent on-topic headlines, older headlines), or None if a search failed."""
    t = parse_time(e.get("time"))
    q = _query(e.get("summary", ""))
    if not t or len(q.split()) < 3:
        return [], []
    own = {r.get("url") for r in e.get("reports", [])}
    outlets = {_outlet(r.get("source")) for r in e.get("reports", [])} - {""}
    rw = _words(e.get("summary", ""))
    recent = _search(session, f"{q} after:{(t - timedelta(days=RECENT_DAYS)).strftime('%Y-%m-%d')}")
    if recent is None:
        return None
    recent = _distinct([r for r in recent if abs(r["when"] - t) <= timedelta(days=RECENT_DAYS)
                        and r["link"] not in own and _outlet_of(r["title"]) not in outlets
                        and _on_topic(rw, r["title"])])
    older = _search(session, f"{q} before:{(t - timedelta(days=AGE_DAYS)).strftime('%Y-%m-%d')}")
    if older is None:
        return None
    older = [r for r in older if r["when"] < t - timedelta(days=AGE_DAYS) and _on_topic(rw, r["title"])][:8]
    return [r for r in recent if not any(_same_headline(r, o) for o in older)], older


def _outlet(source: str | None) -> str:
    """"Mid-Day (via Google News)" -> "mid-day"."""
    return (source or "").split(" (via ")[0].strip().lower()


def _outlet_of(title: str) -> str:
    """The outlet a Google News title ends with ("... - Mid-Day")."""
    return title.rsplit(" - ", 1)[1].strip().lower() if " - " in title else ""


def held(e: dict) -> bool:
    """Kept off the map until a second source reports it: a notable news-only event from a single
    source whose check found older coverage of the topic and no current coverage elsewhere."""
    cov = e.get("coverage") or {}
    groups = {r.get("group") or r.get("source") for r in e.get("reports", [])}
    return (bool(cov.get("older")) and not cov.get("current") and int(e.get("severity") or 1) >= HOLD_MIN_SEVERITY
            and len(groups) < 2 and not e.get("alert"))


def _same_headline(a: dict, b: dict) -> bool:
    wa, wb = _words(_headline(a["title"])), _words(_headline(b["title"]))
    return bool(wa and wb) and len(wa & wb) / len(wa | wb) >= 0.8


def _needs_check(e: dict) -> bool:
    """Events built only from news feeds, attack waves included: a July report of Iranian strikes
    on Bahrain, relisted with a fresh date, became a wave of its own. Waves with any report from
    another source (an air force channel, an OSINT account) are live and not checked. Alert groups
    come from air force channels and are never news-only."""
    return (e.get("checked") != CHECK_VERSION and not e.get("alert")
            and bool(e.get("reports")) and all(r.get("platform") == "rss" for r in e["reports"]))


def _restore(events: list[dict], state: dict) -> list[dict]:
    """Put back events dropped under an earlier version of the rule, to be checked again."""
    kept, back = [], []
    have = {e["id"] for e in events}
    for d in state.get("dropped_as_old", []):
        if d.get("event") and (d.get("version") or 0) < KEEP_DROPS_FROM:
            if d["event"]["id"] not in have:
                back.append({**d["event"], "checked": None, "checks": 0})
                log(f"[recency] put back for a new check: {d['summary']!r}")
            continue
        kept.append(d)
    state["dropped_as_old"] = kept
    return events + back


def check(events: list[dict], new_ids: set[str], session, ask, state: dict, settings: dict, now) -> list[dict]:
    """Return events without the ones shown to be old news. `ask` is extract.ask_json."""
    events = _restore(events, state)
    todo = [e for e in events if _needs_check(e)]
    asked = parse_time(state.get("recency_asked"))
    if asked and now - asked < MODEL_GAP and len(todo) < MODEL_BATCH:
        return events  # a few waiting: check them together in a while
    todo.sort(key=lambda e: e.get("time") or "", reverse=True)
    todo = sorted(todo, key=lambda e: e["id"] not in new_ids)[:CHECKS_PER_RUN]  # this run's events, then newest
    cases = []
    for e in todo:
        e["checks"] = e.get("checks", 0) + 1
        found = _evidence(session, e)
        if found is None:
            if e["checks"] >= MAX_TRIES:
                e["checked"] = CHECK_VERSION
            continue
        recent, older = found
        e["coverage"] = {"current": len(recent), "older": len(older)}
        if not older:
            e["checked"] = CHECK_VERSION
            log(f"[recency] kept (no older coverage found): {e.get('summary', '')[:90]!r}")
            continue
        cases.append((e, recent, older))
    if not cases:
        return events

    day = lambda r: r["when"].strftime("%Y-%m-%d")  # noqa: E731
    payload = [{"i": n, "report": e["summary"], "place": e.get("place"), "listed": (e.get("time") or "")[:10],
                "recent": [{"title": r["title"], "date": day(r)} for r in recent[:5]],
                "older": [{"n": k, "title": r["title"], "date": day(r)} for k, r in enumerate(older)]}
               for n, (e, recent, older) in enumerate(cases)]
    state["recency_asked"] = iso(now)
    reply = ask(PROMPT, json.dumps({"cases": payload}, ensure_ascii=False), state, settings, now, max_tokens=1500)
    if not isinstance(reply, dict) or not isinstance(reply.get("results"), list):
        log("[recency] no model answer; will retry next run")
        for e, _, _ in cases:
            if e["checks"] >= MAX_TRIES:
                e["checked"] = CHECK_VERSION
        return events

    drop = set()
    for res in reply["results"]:
        if not isinstance(res, dict) or not isinstance(res.get("i"), int) or not 0 <= res["i"] < len(cases):
            continue
        e, recent, older = cases[res["i"]]
        e["checked"] = CHECK_VERSION
        m = res.get("match")
        if res.get("old") is not True:
            log(f"[recency] kept (the model judged it new): {e.get('summary', '')[:90]!r}; "
                f"{len(recent)} current and {len(older)} older headlines, oldest shown "
                f"{older[0]['title'][:80]!r} ({day(older[0])})")
        if res.get("old") is True and isinstance(m, int) and 0 <= m < len(older):
            drop.add(e["id"])
            log(f"[recency] old news, dropped: {e['summary']!r} matches {older[m]['title']!r} ({day(older[m])})")
            state.setdefault("dropped_as_old", []).append({
                "summary": e["summary"], "listed": e.get("time"), "match": older[m]["title"],
                "match_date": day(older[m]), "at": iso(now), "version": CHECK_VERSION, "event": e})
    state["dropped_as_old"] = state.get("dropped_as_old", [])[-100:]
    return [e for e in events if e["id"] not in drop]
