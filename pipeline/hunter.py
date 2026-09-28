"""Corroboration hunter: look for more reporting on important events that still rest on one
source or one side's claim.

Each run, up to MAX_EVENTS events of severity 2 or 3 that happened within the last 12 hours and
are still "Single source" or "One side's claim" get a Google News search built from the place
name and event-type keywords, limited to the last day. Each event is searched at most twice,
at least MIN_GAP apart. Results go into the normal extraction queue ahead of everything else
(weight 4), credited like any Google News result: outlets listed in sources.yaml count as
their own source, and all other outlets share the google-news group, which counts only when no
listed outlet reported the event. The existing merge and confidence rules decide whether a
result matches the incident. The hunter makes no model calls.
"""
from __future__ import annotations

import re
from datetime import timedelta
from urllib.parse import quote_plus

import feedparser

from common import clean_text, iso, log, make_item, parse_time
from sources.rss import _entry_time, _outlet_src

MAX_EVENTS = 8
MAX_SEARCHES = 2
MIN_GAP = timedelta(hours=3)
WINDOW = timedelta(hours=12)
RESULTS_PER_SEARCH = 10
SOURCE = {"name": "Google News (corroboration search)", "kind": "news", "group": "google-news",
          "weight": 4, "prefilter": True}
SOURCE_ID = "rss:corroboration-search"

KEYWORDS = {
    "missile_drone": "drone OR missile", "airstrike": 'airstrike OR "air strike" OR bombing',
    "artillery": "shelling OR artillery", "ground": "fighting OR clashes OR attack",
    "territory": "captured OR advance OR control", "air_defense": 'intercepted OR "shot down" OR "air defense"',
    "naval": "ship OR vessel OR tanker", "explosion": "explosion OR blast",
    "deployment": "troops OR deployment OR exercise", "diplomacy": "talks OR meeting OR agreement",
    "ceasefire": "ceasefire OR truce", "hybrid": "sabotage OR arson OR cable OR jamming",
    "incursion": "drone OR airspace OR incursion", "arms_transfer": "weapons OR delivery OR shipment",
    "legal": 'court OR resolution OR "Article 51"',
}


def query(e: dict) -> str | None:
    """Google News search for an event: its place, event-type keywords, the last day."""
    place = re.sub(r"\s*\(.*?\)", "", str(e.get("place") or "")).split(",")[0].strip().replace('"', "")
    if len(place) < 3:
        return None
    return f'"{place}" ({KEYWORDS.get(e.get("type"), "attack")}) when:1d'


def pick(events: list[dict], state: dict, now) -> list[dict]:
    """Events due a search this run, most severe and most recent first."""
    log_ = state.get("hunter") or {}
    due = []
    for e in events:
        t = parse_time(e.get("time"))
        if (e.get("hidden") or e.get("alert") or (e.get("severity") or 1) < 2
                or e.get("status") not in ("unconfirmed", "claimed") or not t or now - t > WINDOW or not query(e)):
            continue
        tries = [parse_time(x) for x in log_.get(e["id"], [])]
        if len(tries) >= MAX_SEARCHES or (tries and now - max(tries) < MIN_GAP):
            continue
        due.append(e)
    due.sort(key=lambda e: e.get("time") or "", reverse=True)
    due.sort(key=lambda e: -(e.get("severity") or 1))
    return due[:MAX_EVENTS]


def run(events: list[dict], state: dict, session, now, outlets: dict | None = None) -> list[dict]:
    """Search for the picked events and return the results as extraction items."""
    out: list[dict] = []
    searched = 0
    for e in pick(events, state, now):
        url = f"https://news.google.com/rss/search?q={quote_plus(query(e))}&hl=en-US&gl=US&ceid=US%3Aen"
        try:
            r = session.get(url, timeout=20)
            r.raise_for_status()
            feed = feedparser.parse(r.content)
        except Exception as exc:  # noqa: BLE001 - a failed search is not counted and is retried later
            log(f"[hunter] search failed: {exc}")
            continue
        state.setdefault("hunter", {}).setdefault(e["id"], []).append(iso(now))
        searched += 1
        entries = sorted(feed.entries, key=lambda en: _entry_time(en) or now.min.replace(tzinfo=now.tzinfo), reverse=True)
        for entry in entries[:RESULTS_PER_SEARCH]:
            published, link = _entry_time(entry), entry.get("link") or ""
            if not published or not link or now - published > timedelta(days=1):
                continue
            title, summary = clean_text(entry.get("title", "")), clean_text(entry.get("summary", ""))[:600]
            text = title + (f"\n{summary}" if summary and summary != title else "")
            origin = _outlet_src(SOURCE, entry, outlets or {})
            out.append(make_item(origin, "rss", SOURCE_ID, link, text, published, uid=entry.get("id") or link))
    # forget events that can no longer be picked
    cutoff = iso(now - timedelta(days=2))
    state["hunter"] = {k: v for k, v in (state.get("hunter") or {}).items() if v and max(v) >= cutoff}
    if searched:
        log(f"[hunter] searched {searched} events, {len(out)} results")
    return out
