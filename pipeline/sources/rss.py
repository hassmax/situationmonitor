"""Read news RSS/Atom feeds."""
from __future__ import annotations

import time
from datetime import datetime

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


def fetch(sources: list[dict], session, health: dict) -> list[dict]:
    items: list[dict] = []
    for src in sources:
        sid = f"rss:{src.get('id') or src['url']}"
        name = src.get("name") or src.get("id") or src["url"]
        try:
            r = session.get(src["url"], timeout=25)
            r.raise_for_status()
            feed = feedparser.parse(r.content)
            if not feed.entries:
                raise ValueError("feed returned no entries")
            latest = None
            count = 0
            for entry in feed.entries[:40]:
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
