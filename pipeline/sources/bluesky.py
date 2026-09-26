"""Read recent posts from Bluesky accounts via the public AppView (no login needed)."""
from __future__ import annotations

import time

from common import health_fail, health_ok, log, make_item, parse_time

FEED_URL = "https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed"


def _embed_text(embed: dict | None) -> str:
    """Pull link-card titles and quoted-post text so the model sees the whole post."""
    if not embed:
        return ""
    parts = []
    etype = embed.get("$type", "")
    if "external" in embed and isinstance(embed["external"], dict):
        ext = embed["external"]
        parts.append(" ".join(x for x in (ext.get("title"), ext.get("description")) if x))
    if etype.startswith("app.bsky.embed.record"):
        rec = embed.get("record") or {}
        rec = rec.get("record", rec)  # recordWithMedia nests one level deeper
        value = rec.get("value") if isinstance(rec, dict) else None
        if isinstance(value, dict) and value.get("text"):
            parts.append("Quoted post: " + value["text"])
    if "media" in embed and isinstance(embed["media"], dict):
        parts.append(_embed_text(embed["media"]))
    text = "\n".join(p for p in parts if p)
    return ("\n" + text) if text else ""


def fetch(sources: list[dict], session, health: dict) -> list[dict]:
    items: list[dict] = []
    for src in sources:
        handle = src["handle"].lstrip("@")
        sid = f"bsky:{handle}"
        name = src.get("name") or handle
        try:
            r = session.get(
                FEED_URL,
                params={"actor": handle, "limit": 30, "filter": "posts_no_replies"},
                timeout=20,
            )
            if r.status_code == 400:
                raise ValueError("handle not found or account unavailable")
            r.raise_for_status()
            latest = None
            count = 0
            for entry in r.json().get("feed", []):
                if entry.get("reason"):  # skip reposts
                    continue
                post = entry.get("post") or {}
                record = post.get("record") or {}
                text = (record.get("text") or "") + _embed_text(post.get("embed"))
                published = parse_time(record.get("createdAt")) or parse_time(post.get("indexedAt"))
                uri = post.get("uri", "")
                if not text.strip() or not published or not uri:
                    continue
                author = (post.get("author") or {}).get("handle", handle)
                rkey = uri.rsplit("/", 1)[-1]
                url = f"https://bsky.app/profile/{author}/post/{rkey}"
                items.append(make_item(src, "bluesky", sid, url, text, published, uid=uri))
                count += 1
                latest = published if latest is None or published > latest else latest
            health[sid] = health_ok(name, "bluesky", latest, count, health.get(sid))
        except Exception as exc:  # noqa: BLE001 - one bad source must not stop the run
            log(f"[bluesky] {handle}: {exc}")
            health[sid] = health_fail(name, "bluesky", exc, health.get(sid))
        time.sleep(0.25)
    return items
