"""Read news RSS/Atom feeds."""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta
from urllib.parse import quote_plus

import feedparser

from common import UTC, clean_text, health_fail, health_ok, log, make_item, parse_time


def _entry_time(entry) -> datetime | None:
    st = entry.get("published_parsed") or entry.get("updated_parsed")
    if not st:
        return None
    try:
        return datetime(*st[:6], tzinfo=UTC)
    except (TypeError, ValueError):
        return None


def fetch(sources: list[dict], session, health: dict, lookback_days: int = 0) -> list[dict]:
    items: list[dict] = []
    for src in sources:
        sid = f"rss:{src.get('id') or src['url']}"
        name = src.get("name") or src.get("id") or src["url"]
        url = src["url"]
        if lookback_days:
            # Google News searches: widen "when:1d" to the backfill period
            url = re.sub(r"when%3A\d+d", f"when%3A{lookback_days}d", url)
        try:
            r = session.get(url, timeout=25)
            r.raise_for_status()
            feed = feedparser.parse(r.content)
            if not feed.entries:
                raise ValueError("feed returned no entries")
            latest = None
            count = 0
            for entry in feed.entries[: (100 if lookback_days else 40)]:
                published = _entry_time(entry)
                link = entry.get("link") or ""
                if not published or not link:
                    continue
                title = clean_text(entry.get("title", ""))
                summary = clean_text(entry.get("summary", ""))[:600]
                text = title + (f"\n{summary}" if summary and summary != title else "")
                items.append(make_item(src, "rss", sid, link, text, published,
                                       uid=entry.get("id") or link))
                count += 1
                latest = published if latest is None or published > latest else latest
            health[sid] = health_ok(name, "rss", latest, count, health.get(sid))
        except Exception as exc:  # noqa: BLE001
            log(f"[rss] {name}: {exc}")
            health[sid] = health_fail(name, "rss", exc, health.get(sid))
        time.sleep(0.2)
    return items


# Google News sometimes lists an old article with a fresh date (a site republished or updated it),
# and a bare headline like "US submarine sinks Iranian ship" gives the model no way to tell.
# Before such a headline becomes an event, search Google News for the same headline without a
# date limit: if it already appeared days earlier, the story is a recap. Any failure lets it through.
REPUBLISHED_DAYS = 3
_STOP = {"a", "an", "the", "of", "in", "on", "at", "to", "for", "and", "or", "by", "with", "as", "is",
         "are", "was", "were", "be", "its", "from", "after", "over", "into", "amid"}


def is_google_news(item: dict) -> bool:
    return "news.google.com/" in (item.get("url") or "")


def _headline(text: str) -> str:
    """First line of a Google News item, without the trailing " - Outlet"."""
    title = (text or "").split("\n", 1)[0].strip()
    return title.rsplit(" - ", 1)[0].strip() if " - " in title else title


def _words(headline: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", headline.lower()) if w not in _STOP}


def republished(item: dict, session) -> bool:
    """True when the same headline was already on Google News days before this item's date."""
    headline = _headline(item.get("text", ""))
    words = _words(headline)
    posted = parse_time(item.get("time"))
    if len(words) < 4 or not posted:
        return False
    phrase = headline.replace('"', " ")
    url = f"https://news.google.com/rss/search?q=%22{quote_plus(phrase)}%22&hl=en-US&gl=US&ceid=US%3Aen"
    try:
        r = session.get(url, timeout=20)
        r.raise_for_status()
        feed = feedparser.parse(r.content)
    except Exception as exc:  # noqa: BLE001
        log(f"[rss] recap check failed, keeping the item: {exc}")
        return False
    cutoff = posted - timedelta(days=REPUBLISHED_DAYS)
    for entry in feed.entries[:40]:
        when = _entry_time(entry)
        other = _words(_headline(clean_text(entry.get("title", ""))))
        if not when or when >= cutoff or not other:
            continue
        if len(words & other) / len(words | other) >= 0.8:
            log(f"[rss] recap: {headline!r} was already on Google News on {when:%Y-%m-%d}")
            return True
    return False
