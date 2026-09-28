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
               "weight": max(3, int(src.get("weight", 1))) if known.get("tier") == 1 else int(src.get("weight", 1))}
        return out
    return {**src, "name": f"{name} (via Google News)" if name else src.get("name")}


LABEL_KEYS = ("source", "kind", "side", "group", "weight")


def outlet_labels(items: list[dict]) -> dict[str, dict]:
    """Google News article link -> who published it, from items read this run."""
    return {it["url"]: {k: it.get(k) for k in LABEL_KEYS} for it in items
            if "news.google.com/" in it.get("url", "") and str(it.get("source", "")).endswith("(via Google News)")}


def fetch_outlet_labels(sources: list[dict], session, outlets: dict, days: int) -> dict[str, dict]:
    """Re-read the Google News searches over the last `days` days only to learn who published
    each article (no model calls, nothing new is extracted). Used once, for reports stored
    before outlets were told apart."""
    items = []
    for src in sources:
        if "news.google.com/" not in src["url"]:
            continue
        url = re.sub(r"when%3A\d+[hd]", f"when%3A{days}d", src["url"])
        try:
            r = session.get(url, timeout=25)
            r.raise_for_status()
            feed = feedparser.parse(r.content)
        except Exception as exc:  # noqa: BLE001
            log(f"[rss] outlet lookup for {src.get('name') or url}: {exc}")
            continue
        for entry in feed.entries:
            link = entry.get("link") or ""
            if link:
                items.append({"url": link, **_label(_outlet_src(src, entry, outlets))})
        time.sleep(0.2)
    return outlet_labels(items)


def _label(src: dict) -> dict:
    return {"source": src.get("name"), "kind": src.get("kind", "osint"), "side": src.get("side"),
            "group": src.get("group") or f"rss:{src.get('id') or src['url']}", "weight": int(src.get("weight", 1))}


def relabel(reports: list[dict], labels: dict[str, dict]) -> int:
    """Credit stored Google News reports (and queued items) to the outlet that published them.
    Reports stored before outlets were told apart carry only the search's name."""
    n = 0
    for r in reports:
        lab = labels.get(r.get("url", ""))
        if lab and not str(r.get("source", "")).endswith("(via Google News)") and any(r.get(k) != lab[k] for k in LABEL_KEYS):
            r.update(lab)
            n += 1
    return n


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
