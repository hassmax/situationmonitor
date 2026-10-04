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


# Patrol: Bluesky's keyword search, for posts from accounts the list above doesn't follow (the owner,
# 2026-10-04, after OSINTtechnical's post on B-1s leaving RAF Fairford went unnoticed: "agents
# patrolling social media"). The public AppView refuses searches (403); api.bsky.app answers without
# a login. Only authors with at least `min_followers` are kept, so a stranger's guess doesn't use the
# model budget, and every post still goes through the keyword filter. Accounts not on the list share
# one source group (SEARCH_GROUP), counted only when nothing else reported the event (merge.WEAK_GROUPS):
# a post found this way is never corroborated by another found this way.
SEARCH_URL = "https://api.bsky.app/xrpc/app.bsky.feed.searchPosts"
PROFILES_URL = "https://api.bsky.app/xrpc/app.bsky.actor.getProfiles"
SEARCH_GROUP = "bluesky-search"
SEARCH_ID = "bsky-search"
SEARCH_LIMIT = 25        # posts per query per run (the newest)
SEARCH_HOURS = 6         # posts older than this are not taken from a search


def _followers(handles: list[str], session, known: dict) -> dict:
    """Follower counts by handle (getProfiles, 25 at a time); `known` caches them for the run."""
    todo = [h for h in handles if h not in known]
    for i in range(0, len(todo), 25):
        try:
            r = session.get(PROFILES_URL, params=[("actors", h) for h in todo[i:i + 25]], timeout=20)
            r.raise_for_status()
            for p in r.json().get("profiles", []):
                known[p.get("handle")] = int(p.get("followersCount") or 0)
        except Exception as exc:  # noqa: BLE001 - unknown counts mean the posts are skipped this run
            log(f"[bluesky] search: follower counts failed: {exc}")
        for h in todo[i:i + 25]:
            known.setdefault(h, 0)
    return known


def search(cfg: dict | None, listed: list[dict], session, health: dict, now) -> list[dict]:
    """Posts matching the patrol's queries from well-followed accounts. Listed accounts are credited
    as themselves; the rest share SEARCH_GROUP."""
    cfg = cfg or {}
    queries = [q for q in (cfg.get("queries") or []) if q]
    if not queries or cfg.get("disabled"):
        return []
    min_followers = int(cfg.get("min_followers", 2000))
    by_handle = {s["handle"].lstrip("@").lower(): s for s in listed}
    found: dict[str, tuple] = {}
    failures = 0
    for q in queries:
        try:
            params = {"q": q, "sort": "latest", "limit": SEARCH_LIMIT}
            if cfg.get("lang"):
                params["lang"] = cfg["lang"]
            r = session.get(SEARCH_URL, params=params, timeout=20)
            r.raise_for_status()
            for post in r.json().get("posts", []):
                record = post.get("record") or {}
                published = parse_time(record.get("createdAt")) or parse_time(post.get("indexedAt"))
                uri, author = post.get("uri", ""), (post.get("author") or {}).get("handle", "")
                text = (record.get("text") or "") + _embed_text(post.get("embed"))
                if uri and author and text.strip() and published and (now - published).total_seconds() < SEARCH_HOURS * 3600:
                    found[uri] = (post, author, text, published)
        except Exception as exc:  # noqa: BLE001 - one failed query must not stop the others
            failures += 1
            log(f"[bluesky] search {q!r}: {exc}")
        time.sleep(0.25)
    unlisted = sorted({a for _, a, _, _ in found.values() if a.lower() not in by_handle})
    followers = _followers(unlisted, session, {})
    items, latest = [], None
    for uri, (post, author, text, published) in found.items():
        src = by_handle.get(author.lower())
        if src is None:
            if followers.get(author, 0) < min_followers:
                continue
            name = (post.get("author") or {}).get("displayName") or author
            src = {"name": f"{name} (@{author}, Bluesky)", "kind": "osint", "group": SEARCH_GROUP, "weight": 1}
            sid = SEARCH_ID
        else:
            sid = f"bsky:{author}"
        url = f"https://bsky.app/profile/{author}/post/{uri.rsplit('/', 1)[-1]}"
        items.append(make_item(src, "bluesky", sid, url, text, published, uid=uri))
        latest = published if latest is None or published > latest else latest
    if failures == len(queries):
        health[SEARCH_ID] = health_fail("Bluesky search (patrol)", "bluesky", RuntimeError("every search failed"), health.get(SEARCH_ID))
    else:
        health[SEARCH_ID] = health_ok("Bluesky search (patrol)", "bluesky", latest, len(items), health.get(SEARCH_ID))
    log(f"[bluesky] search: {len(found)} recent posts for {len(queries)} queries, {len(items)} from listed or well-followed accounts")
    return items
