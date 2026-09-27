"""Read news RSS/Atom feeds."""
from __future__ import annotations

import re
import time
from datetime import datetime
from urllib.parse import urlparse

import feedparser

from common import UTC, clean_text, health_fail, health_ok, log, make_item


def _entry_time(entry) -> datetime | None:
    st = entry.get("published_parsed") or entry.get("updated_parsed")
    if not st:
        return None
    try:
        return datetime(*st[:6], tzinfo=UTC)
    except (TypeError, ValueError):
        return None


def _outlet_src(src: dict, entry, outlets: dict) -> dict:
    """For a Google News result, the source is the outlet that published it. Listed outlets
    count as their own source; others stay together under the search's own group."""
    info = entry.get("source") or {}
    host = urlparse(info.get("href") or "").netloc.lower().removeprefix("www.")
    title = clean_text(entry.get("title", ""))
    name = (info.get("title") or (title.rsplit(" - ", 1)[1] if " - " in title else "")).strip()
    known = next((outlets[d] for d in (host, host.split(".", 1)[-1]) if d in outlets), None)
    if known:
        out = {**src, "name": f"{known['name']} (via Google News)", "group": known.get("group") or f"outlet:{known['domain']}",
               "kind": known.get("kind", "news"), "side": known.get("side"),
               "weight": 3 if known.get("tier") == 1 else int(src.get("weight", 1))}
        return out
    return {**src, "name": f"{name} (via Google News)" if name else src.get("name")}


def fetch(sources: list[dict], session, health: dict, lookback_days: int = 0, outlets: dict | None = None) -> list[dict]:
    items: list[dict] = []
    for src in sources:
        sid = f"rss:{src.get('id') or src['url']}"
        name = src.get("name") or src.get("id") or src["url"]
        url = src["url"]
        if lookback_days:
            # Google News searches: widen the "when:" window to the backfill period
            url = re.sub(r"when%3A\d+[hd]", f"when%3A{lookback_days}d", url)
        try:
            r = session.get(url, timeout=25)
            r.raise_for_status()
            feed = feedparser.parse(r.content)
            if not feed.entries:
                raise ValueError("feed returned no entries")
            latest = None
            count = 0
            # Newest first: Google News orders search results by relevance, so a cap on the
            # unsorted list can cut off the freshest stories.
            entries = sorted(feed.entries, key=lambda en: _entry_time(en) or datetime.min.replace(tzinfo=UTC), reverse=True)
            for entry in entries[:100]:
                published = _entry_time(entry)
                link = entry.get("link") or ""
                if not published or not link:
                    continue
                title = clean_text(entry.get("title", ""))
                summary = clean_text(entry.get("summary", ""))[:600]
                text = title + (f"\n{summary}" if summary and summary != title else "")
                origin = _outlet_src(src, entry, outlets or {}) if "news.google.com/" in url else src
                items.append(make_item(origin, "rss", sid, link, text, published,
                                       uid=entry.get("id") or link))
                count += 1
                latest = published if latest is None or published > latest else latest
            health[sid] = health_ok(name, "rss", latest, count, health.get(sid))
        except Exception as exc:  # noqa: BLE001
            log(f"[rss] {name}: {exc}")
            health[sid] = health_fail(name, "rss", exc, health.get(sid))
        time.sleep(0.2)
    return items
