"""Article date check: catch old articles that news feeds list with a fresh date.

Google News lists a republished or updated article with a new date, and a bare headline like
"Yemen's Houthis launched ballistic missiles at Israel" can't show it is a March story. The
article's own page usually says when it was first published (a "published" meta tag, the
datePublished field of its structured data). So for each notable event that rests on a single
news report, the article is opened once and its publication date read:

  - published more than OLD_DAYS before the date the feed gave: an old story, dropped (kept in
    state like the old-news check's drops, with the reason logged);
  - published earlier than the event's date by more than SLACK: the event is moved back to the
    article's date (it can't have happened after the article reporting it);
  - no date found, a blocked page, or a link that can't be resolved: nothing changes (the
    old-news check and its "possibly an old story" flag still apply).

Google News links are redirects (news.google.com/rss/articles/...). The publisher's address is
decoded from the link, or asked of Google when the link doesn't carry it. No model calls; at most
PER_RUN articles a run, most serious and longest waiting first, each event once (twice if its page
couldn't be read the first time).
"""
from __future__ import annotations

import base64
import json
import re
import time
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import quote

from common import UTC, iso, log, parse_time

OLD_DAYS = 14
SLACK = timedelta(hours=6)
PER_RUN = 20
BUDGET_SECONDS = 45                 # no new article is opened after this much time in one run
TIMEOUT = 10                        # seconds per web request
TRIES = 2                           # an article that can't be read is tried once more, on a later run
RECENT = timedelta(days=3)          # only events this recent are checked
MIN_SEVERITY = 2
# Military and government sites post photos and releases long after the fact ("USS Abraham Lincoln
# and USS Robert Smalls transited the South China Sea" was an old photo on the Pacific Fleet site):
# their items are checked whatever their severity.
OFFICIAL = re.compile(r"\.mil\b|dvidshub|defense\.gov|\.gov\.uk|nato\.int|mod\.gov|mil\.ru|idf\.il", re.I)
MAX_BYTES = 400_000
BROWSER = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
           "Chrome/126.0 Safari/537.36")

_GOOGLE = re.compile(r"news\.google\.com/(?:rss/)?articles/([A-Za-z0-9_-]+)")
_NAMES = r"(?:article:published_time|og:article:published_time|datePublished|pubdate|publishdate|publish-date|" \
         r"dc\.date\.issued|DC\.date\.issued|sailthru\.date|parsely-pub-date|article\.published)"
# in order of trust: explicit "published" tags first, then structured data
_PATTERNS = [
    re.compile(r"<meta[^>]+(?:property|name|itemprop)=[\"']" + _NAMES + r"[\"'][^>]*?content=[\"']([^\"']+)", re.I),
    re.compile(r"<meta[^>]+content=[\"']([^\"']+)[\"'][^>]*?(?:property|name|itemprop)=[\"']" + _NAMES + r"[\"']", re.I),
    re.compile(r"\"datePublished\"\s*:\s*\"([^\"]+)\""),
    re.compile(r"itemprop=[\"']datePublished[\"'][^>]*?(?:content|datetime)=[\"']([^\"']+)", re.I),
]


def check(events: list[dict], session, state: dict, now) -> list[dict]:
    """Returns events without old articles; moves others back to their article's date."""
    todo = [e for e in events if _eligible(e, now)]
    # Most serious first, then the one waiting longest. Newest-first let each run's new stories
    # push older ones back for hours (a relisted March report of Houthi missiles at Israel waited
    # behind 146 others).
    todo.sort(key=lambda e: (-int(e.get("severity") or 1), e.get("time") or ""))
    dated = old = moved = opened = 0
    drop = set()
    started = time.monotonic()
    for e in todo[:PER_RUN]:
        if time.monotonic() - started > BUDGET_SECONDS:
            break  # slow sites this run: the rest wait for the next run, in the same order
        opened += 1
        report = e["reports"][0]
        url = _publisher_url(session, report.get("url") or "")
        published = _published(session, url) if url else None
        e["dated"] = {"url": url, "published": iso(published) if published else None,
                      "tries": int((e.get("dated") or {}).get("tries", 0)) + 1}
        if not published:
            continue
        dated += 1
        listed = parse_time(report.get("time")) or parse_time(e.get("time"))
        if listed and listed - published > timedelta(days=OLD_DAYS):
            old += 1
            drop.add(e["id"])
            log(f"[datecheck] old article, dropped: {e['summary'][:80]!r} was published "
                f"{published:%Y-%m-%d}, listed {listed:%Y-%m-%d} ({url[:80]})")
            state.setdefault("dropped_as_old", []).append({
                "summary": e["summary"], "listed": e.get("time"), "match": f"article published {published:%Y-%m-%d}",
                "match_date": f"{published:%Y-%m-%d}", "at": iso(now), "version": 99, "event": e})
        elif (parse_time(e.get("time")) or now) - published > SLACK:
            moved += 1
            log(f"[datecheck] dated to the article: {e['summary'][:80]!r} {e['time'][:16]} -> {iso(published)[:16]}")
            e["time"] = iso(published)
    if opened:
        log(f"[datecheck] {opened} articles opened: {dated} dated, {old} old, {moved} moved back, "
            f"{opened - dated} without a readable date; {len(todo) - opened} waiting")
    state["dropped_as_old"] = state.get("dropped_as_old", [])[-100:]
    return [e for e in events if e["id"] not in drop]


def _eligible(e: dict, now) -> bool:
    """A notable event resting on one news report, recent, not dated yet (tried fewer than TRIES times)."""
    reps = e.get("reports") or []
    t = parse_time(e.get("time"))
    d = e.get("dated") or {}
    return (not d.get("published") and int(d.get("tries", 0)) < TRIES and not e.get("alert") and len(reps) == 1 and reps[0].get("platform") == "rss"
            and (int(e.get("severity") or 1) >= MIN_SEVERITY or _official(reps[0]))
            and bool(t) and now - t <= RECENT)


def _official(report: dict) -> bool:
    return bool(OFFICIAL.search(f"{report.get('source') or ''} {report.get('url') or ''}"))


def _publisher_url(session, url: str) -> str | None:
    """The publisher's address for a Google News link (other links are returned as they are)."""
    m = _GOOGLE.search(url)
    if not m:
        return url if url.startswith("http") else None
    gid = m.group(1)
    direct = _decode_id(gid)
    if direct:
        return direct
    return _ask_google(session, gid)


def _decode_id(gid: str) -> str | None:
    """Older Google News ids carry the address itself (base64 of a small record)."""
    try:
        raw = base64.urlsafe_b64decode(gid + "=" * (-len(gid) % 4))
    except (ValueError, TypeError):
        return None
    i = raw.find(b"http")
    if i < 0:
        return None
    end = i
    while end < len(raw) and 32 < raw[end] < 127:
        end += 1
    candidate = raw[i:end].decode("ascii", "ignore")
    return candidate if re.match(r"https?://[^/]+\.[^/]+", candidate) else None


def _ask_google(session, gid: str) -> str | None:
    """Newer ids are opaque: Google's article page gives a signature and timestamp, and its
    batchexecute endpoint returns the address for them."""
    try:
        page = session.get(f"https://news.google.com/rss/articles/{gid}", timeout=TIMEOUT, headers={"User-Agent": BROWSER})
        sig = re.search(r'data-n-a-sg="([^"]+)"', page.text)
        ts = re.search(r'data-n-a-ts="([^"]+)"', page.text)
        if not (sig and ts):
            return None
        inner = (f'["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,null,null,null,0,1],'
                 f'"X","X",1,[1,1,1],1,1,null,0,0,null,0],"{gid}",{ts.group(1)},"{sig.group(1)}"]')
        body = "f.req=" + quote(json.dumps([[["Fbv4je", inner, None, "generic"]]]))
        r = session.post("https://news.google.com/_/DotsSplashUi/data/batchexecute", data=body, timeout=TIMEOUT,
                         headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                                  "User-Agent": BROWSER})
        chunk = r.text.split("\n\n", 1)[1]
        url = json.loads(json.loads(chunk)[0][2])[1]
        return url if isinstance(url, str) and url.startswith("http") else None
    except Exception as exc:  # noqa: BLE001
        log(f"[datecheck] could not resolve a Google News link: {str(exc)[:80]}")
        return None


def _published(session, url: str) -> datetime | None:
    """The article's own publication date, read from its page, or None."""
    try:
        r = session.get(url, timeout=TIMEOUT, stream=True, headers={"User-Agent": BROWSER})
        if r.status_code != 200:
            return None
        html = r.raw.read(MAX_BYTES, decode_content=True).decode(r.encoding or "utf-8", "ignore")
    except Exception as exc:  # noqa: BLE001
        log(f"[datecheck] could not open {url[:60]}: {str(exc)[:60]}")
        return None
    return published_in(html)


def published_in(html: str) -> datetime | None:
    """The page's first-published date or, for a photo, the date it was taken, whichever is earlier
    (photo pages are often posted weeks after the picture: DVIDS and navy.mil give both)."""
    published = None
    for rx in _PATTERNS:
        m = rx.search(html or "")
        if m:
            published = _parse(m.group(1))
            if published:
                break
    taken = _taken(html or "")
    return min((t for t in (published, taken) if t), default=None)


# "Date Taken: 03.14.2026" (DVIDS, navy.mil photo pages), or the image's structured dateCreated
_TAKEN = [re.compile(r"Date Taken:?\s*(?:<[^>]+>\s*)*(\d{1,2})\.(\d{1,2})\.(\d{4})", re.I),
          re.compile(r"\"dateCreated\"\s*:\s*\"([^\"]+)\"")]


def _taken(html: str) -> datetime | None:
    m = _TAKEN[0].search(html)
    if m:
        try:
            return datetime(int(m.group(3)), int(m.group(1)), int(m.group(2)), tzinfo=UTC)
        except ValueError:
            pass
    m = _TAKEN[1].search(html)
    return _parse(m.group(1)) if m else None


def _parse(value: str) -> datetime | None:
    value = value.strip()
    t = parse_time(value)
    if t is None:
        try:
            t = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        t = t.astimezone(UTC) if t.tzinfo else t.replace(tzinfo=UTC)
    return t if datetime(2000, 1, 1, tzinfo=UTC) < t < datetime.now(UTC) + timedelta(days=1) else None
