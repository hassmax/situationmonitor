"""Article date check: catch old articles that news feeds list with a fresh date.

Google News lists a republished or updated article with a new date, and a bare headline like
"Yemen's Houthis launched ballistic missiles at Israel" can't show it is a March story. So for
every recent event built only from news feeds, the article behind its first report is dated once:

  1. its own page: a "published" meta tag, the datePublished field of its structured data, or for a
     photo the date it was taken, whichever is earlier;
  2. its address, which often carries the date (".../2025/03/14/...", ".../20250314-...") and
     still answers when the page refuses automated reading (Reuters, The New York Times and Al
     Arabiya did, for a third of the articles on 2026-10-06): the earlier of the two is used;
  3. when neither gives a date, the earliest listing of the same headline on Google News (a search
     for the exact headline, TITLE_SEARCHES a run): an article first listed a year ago is old
     however often it is relisted.

  - more than OLD_DAYS before the date the feed gave: an old story, dropped (kept in state like the
    old-news check's drops, with the reason logged);
  - earlier than the event's date by more than SLACK (an exact date only): the event is moved back
    to it (it can't have happened after the article reporting it);
  - no date found: nothing changes (the old-news check and its "possibly an old story" flag still
    apply).

Google News links are redirects (news.google.com/rss/articles/...). The publisher's address is
decoded from the link, or asked of Google when the link doesn't carry it. No model calls; at most
PER_RUN articles a run, WORKERS at a time, this run's events and the most serious first, each
event once (twice if its page couldn't be read the first time).
"""
from __future__ import annotations

import base64
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import quote, quote_plus

import feedparser

from common import UTC, iso, log, parse_time

OLD_DAYS = 14
SLACK = timedelta(hours=6)
PER_RUN = 30
WORKERS = 4                         # articles opened at a time (Google resolves its links: go gently)
BUDGET_SECONDS = 45                 # no new article is opened after this much time in one run
TIMEOUT = 10                        # seconds per web request
TRIES = 2                           # an article that can't be read is tried once more, on a later run
RECENT = timedelta(days=3)          # only events this recent are checked
TITLE_SEARCHES = 8                  # exact-headline searches a run, for articles with no date
SAME_TITLE = 0.8                    # word overlap for a listing to count as the same headline
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


def check(events: list[dict], session, state: dict, now, new_ids: set[str] | None = None) -> list[dict]:
    """Returns events without old articles; moves others back to their article's date."""
    new_ids = new_ids or set()
    todo = [e for e in events if _eligible(e, now)]
    # This run's events first (so an old story is caught before it is shown), then the most
    # serious, then the one waiting longest (newest-first let each run's new stories push older
    # ones back for hours: a relisted March report of Houthi missiles at Israel waited behind 146).
    todo.sort(key=lambda e: (e["id"] not in new_ids, -int(e.get("severity") or 1), e.get("time") or ""))
    todo = todo[:PER_RUN]
    started = time.monotonic()

    def look(e):
        if time.monotonic() - started > BUDGET_SECONDS:
            return e, None, None, False  # slow sites this run: the rest wait for the next run
        report = _first_news(e)
        url = _publisher_url(session, report.get("url") or "")
        if not url:
            return e, None, None, True
        page = _published(session, url)
        addr, exact = url_date(url)
        if page and addr:
            return e, url, (min(page, addr), "page" if page <= addr else "address", True if page <= addr else exact), True
        if page:
            return e, url, (page, "page", True), True
        if addr:
            return e, url, (addr, "address", exact), True
        return e, url, None, True

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        looked = list(pool.map(look, todo))
    dated = old = moved = opened = listed_n = 0
    drop = set()
    searches = 0
    for e, url, found, tried in looked:
        if not tried:
            continue
        opened += 1
        report = _first_news(e)
        if not found and report.get("title") and searches < TITLE_SEARCHES:
            searches += 1
            first = first_listed(session, report["title"])
            if first:
                found = (first, "listed", True)
                listed_n += 1
        e["dated"] = {"url": url, "published": iso(found[0]) if found else None, "how": found[1] if found else None,
                      "tries": int((e.get("dated") or {}).get("tries", 0)) + 1}
        if not found:
            continue
        published, how, exact = found
        dated += 1
        listed = parse_time(report.get("time")) or parse_time(e.get("time"))
        if listed and listed - published > timedelta(days=OLD_DAYS):
            old += 1
            drop.add(e["id"])
            what = {"page": "published", "address": "dated in its address", "listed": "first listed"}[how]
            log(f"[datecheck] old article, dropped: {e['summary'][:80]!r} was {what} "
                f"{published:%Y-%m-%d}, listed {listed:%Y-%m-%d} ({(url or '')[:80]})")
            state.setdefault("dropped_as_old", []).append({
                "summary": e["summary"], "listed": e.get("time"), "match": f"article {what} {published:%Y-%m-%d}",
                "match_date": f"{published:%Y-%m-%d}", "at": iso(now), "version": 99, "event": e})
        elif exact and (parse_time(e.get("time")) or now) - published > SLACK:
            moved += 1
            log(f"[datecheck] dated to the article: {e['summary'][:80]!r} {e['time'][:16]} -> {iso(published)[:16]}")
            e["time"] = iso(published)
    if opened:
        log(f"[datecheck] {opened} articles opened: {dated} dated ({listed_n} by their headline's first listing), "
            f"{old} old, {moved} moved back, {opened - dated} without a date; "
            f"{sum(1 for e in events if e['id'] not in drop and _eligible(e, now))} waiting")
    state["dropped_as_old"] = state.get("dropped_as_old", [])[-100:]
    return [e for e in events if e["id"] not in drop]


def _first_news(e: dict) -> dict:
    """The first news report (the article the event was built from)."""
    reps = [r for r in e.get("reports") or [] if r.get("platform") == "rss"] or e.get("reports") or [{}]
    return min(reps, key=lambda r: r.get("time") or "")


def _eligible(e: dict, now) -> bool:
    """A recent event built only from news reports (every kind and severity: an old story is old
    however minor), not dated yet (tried fewer than TRIES times). Official sites' items too."""
    reps = e.get("reports") or []
    t = parse_time(e.get("time"))
    d = e.get("dated") or {}
    news = bool(reps) and all(r.get("platform") == "rss" for r in reps)
    return (not d.get("published") and int(d.get("tries", 0)) < TRIES and not e.get("alert")
            and (news or any(_official(r) for r in reps)) and bool(t) and now - t <= RECENT)


# A date in an article's address: ".../2025/03/14/...", ".../2025-03-14-...", ".../20250314...",
# ".../2026/1006/..." (RTE), or a month alone (".../2025/03/..."), read as that month's last day.
_URL_DATES = [
    re.compile(r"(?<!\d)(20\d\d)[/_.-](0[1-9]|1[0-2])[/_.-](0[1-9]|[12]\d|3[01])(?!\d)"),
    re.compile(r"/(20\d\d)/(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])/"),
    re.compile(r"[/_-](20\d\d)(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(?!\d)"),
]
_URL_MONTH = re.compile(r"/(20\d\d)/(0[1-9]|1[0-2])/")


def url_date(url: str) -> tuple[datetime | None, bool]:
    """(the date in an article's address, whether it is a full date), or (None, False)."""
    path = re.sub(r"^https?://[^/]+", "", url or "")
    for rx in _URL_DATES:
        m = rx.search(path)
        if m:
            try:
                t = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), 12, tzinfo=UTC)
            except ValueError:
                continue
            if t <= datetime.now(UTC) + timedelta(days=1):
                return t, True
    m = _URL_MONTH.search(path)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        nxt = datetime(y + (mo == 12), mo % 12 + 1, 1, tzinfo=UTC)
        t = nxt - timedelta(seconds=1)
        if t <= datetime.now(UTC) + timedelta(days=31):
            return t, False
    return None, False


def _head_words(title: str) -> set[str]:
    head = title.rsplit(" - ", 1)[0] if " - " in title else title
    return {w for w in re.findall(r"\w+", head.lower()) if len(w) > 2}


def first_listed(session, title: str) -> datetime | None:
    """When Google News first listed this headline: the earliest result of a search for it whose
    headline is the same (SAME_TITLE of its words). None if the search fails or finds none."""
    head = title.rsplit(" - ", 1)[0].strip() if " - " in title else title.strip()
    want = _head_words(title)
    if len(want) < 4:
        return None
    url = f"https://news.google.com/rss/search?q={quote_plus(chr(34) + head + chr(34))}&hl=en-US&gl=US&ceid=US%3Aen"
    try:
        r = session.get(url, timeout=TIMEOUT, headers={"User-Agent": BROWSER})
        r.raise_for_status()
        feed = feedparser.parse(r.content)
    except Exception as exc:  # noqa: BLE001
        log(f"[datecheck] headline search failed: {str(exc)[:80]}")
        return None
    times = []
    for entry in feed.entries[:60]:
        got = _head_words(entry.get("title", ""))
        if got and len(want & got) / len(want | got) >= SAME_TITLE:
            for key in ("published", "updated"):
                if entry.get(key):
                    t = _parse(entry[key])
                    if t:
                        times.append(t)
                        break
    return min(times) if times else None


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
