"""Scout: no model. Looks for more reporting on settlements whose status rests on one side's
claim or on fighting inside: a Google News search per settlement (its name, the region, and
words for control changing hands, last 2 days), at most PER_RUN a run, each settlement at most
MAX_SEARCHES times and MIN_GAP apart. Results join the normal extraction queue ahead of everything
else (weight 4), credited like any Google News result; the claims agent reads what comes of them.
"""
from __future__ import annotations

from datetime import timedelta
from urllib.parse import quote_plus

import feedparser

from common import clean_text, iso, log, make_item, parse_time
from sources.rss import _entry_time, _outlet_src

from . import ledger

PER_RUN = 6
MAX_SEARCHES = 3
MIN_GAP = timedelta(hours=6)
FRESH = timedelta(days=3)
RESULTS = 8
WORDS = "captured OR seized OR liberated OR control OR withdrew OR entered OR recaptured"
SOURCE = {"name": "Google News (front-line search)", "kind": "news", "group": "google-news", "weight": 4, "prefilter": True}
SOURCE_ID = "rss:frontline-search"


def due(fl: dict, now) -> list[tuple[str, dict]]:
    """Settlements worth a search: claimed or contested (shown or waiting), recent evidence."""
    out = []
    for k, p in fl["places"].items():
        pub = p.get("published") or {}
        last = parse_time((p["claims"] or [{}])[-1].get("time"))
        if not last or now - last > FRESH or pub.get("status") == "assessed":
            continue
        tries = [parse_time(t) for t in fl["scout"].get(k, [])]
        if len(tries) >= MAX_SEARCHES or (tries and now - max(tries) < MIN_GAP):
            continue
        out.append((k, p))
    out.sort(key=lambda kp: kp[1]["claims"][-1]["time"], reverse=True)
    return out[:PER_RUN]


def run(state: dict, session, now, outlets: dict | None = None) -> list[dict]:
    fl = ledger.state_of(state)
    items, searched = [], 0
    for k, p in due(fl, now):
        q = f'"{p["name"]}" ({WORDS}) when:2d'
        url = f"https://news.google.com/rss/search?q={quote_plus(q)}&hl=en-US&gl=US&ceid=US%3Aen"
        try:
            r = session.get(url, timeout=20)
            r.raise_for_status()
            feed = feedparser.parse(r.content)
        except Exception as exc:  # noqa: BLE001 - not counted; tried again next run
            log(f"[frontline] scout search failed: {exc}")
            continue
        fl["scout"].setdefault(k, []).append(iso(now))
        searched += 1
        for entry in feed.entries[:RESULTS]:
            published, link = _entry_time(entry), entry.get("link") or ""
            if not published or not link or now - published > timedelta(days=2):
                continue
            title, summary = clean_text(entry.get("title", "")), clean_text(entry.get("summary", ""))[:600]
            text = title + (f"\n{summary}" if summary and summary != title else "")
            items.append(make_item(_outlet_src(SOURCE, entry, outlets or {}), "rss", SOURCE_ID, link, text, published,
                                   uid=entry.get("id") or link))
    cutoff = iso(now - timedelta(days=7))
    fl["scout"] = {k: v for k, v in fl["scout"].items() if v and max(v) >= cutoff}
    if searched:
        log(f"[frontline] scout searched {searched} settlements, {len(items)} results")
    return items
