"""ISW's Map Room (understandingwar.org/analysis/map-room/): the Institute for the Study of War's
daily maps of Ukraine, Yemen (Taiz and Lahij), the Iranian-backed campaign and more.

The maps are published as images, with a title and date but no caption. The list is read every
CHECK_EVERY through the site's WordPress API (one request, no model). New maps are then read by
the model, which sees the image (and, for a control-of-terrain map, the previous edition of the
same map, to spot front-line changes) and lists what the map reports, each finding tied to a place
name printed on it. Those findings are ordinary posts from then on: the normal extraction turns them
into events, the place names are looked up like any other (never positions read off the image),
and each report is credited to ISW with a link to the map.

Budget: one model call per map ("maproom" in the day's tally), at most PER_RUN a run and
maproom_daily_max a day (extract.SHARES). Maps that cover a month or a week, or a theme ("September
2026"), describe older events the map would drop anyway, and are not read.
"""
from __future__ import annotations

import base64
import html
import re
from datetime import datetime, timedelta

from common import health_fail, health_ok, iso, log, make_item, parse_time

API = "https://understandingwar.org/wp-json/wp/v2/map"
FIELDS = "id,date_gmt,link,title,content,class_list"
# The site turns away some automated requests; the API answers a browser's.
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/131.0 Safari/537.36", "Accept": "application/json,*/*"}
CHECK_EVERY = timedelta(hours=1)
PER_RUN = 3
MAX_AGE = timedelta(hours=36)     # a map not read by then is left (its events would be too old)
MAX_SPAN_DAYS = 2                 # maps covering longer periods ("September 1 to September 30") are skipped
PREVIOUS_MAX = timedelta(days=4)  # the previous edition is compared only if this recent
TRIES = 2
IMAGE_WIDTH = 1638                # ISW's 1638x2048 rendition: place labels stay readable
TIMEOUT = 30
SEEN_DAYS = 10
MAX_FINDINGS = 12

SOURCE = {"name": "ISW Map Room", "kind": "analysis", "group": "isw", "weight": 4, "prefilter": False}

_MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
_DATE_RE = re.compile(rf"\b({_MONTHS})\s+(\d{{1,2}})(?:\s*[-–]\s*(\d{{1,2}}))?(?:,?\s+(\d{{4}}))?", re.I)
_MONTH_ONLY_RE = re.compile(rf"\b({_MONTHS})\s+\d{{4}}\b", re.I)
_YEMEN_RE = re.compile(r"yemen|houthi|taiz|lahij|hodeidah|hudaydah|marib|red sea|aden", re.I)
_CONTROL_RE = re.compile(r"assessed control of terrain", re.I)

PROMPT = f"""You read one map published by the Institute for the Study of War (ISW) and list what it reports, for a live armed-conflict map. Reply with one JSON object and nothing else.

Rules:
- Report only what the map shows in words or explains in its legend: labels, callouts, text boxes, and legend-coded symbols or shading next to a printed place name. Never guess what an unexplained shape or symbol means. Leave out anything you cannot read clearly.
- Every finding names a place exactly as printed on the map (a town, village, district, base, port, island or sea), and the province, oblast, governorate or district it is in when the map or its title shows it (many villages share a name). Never give coordinates, and never describe a position the map's text doesn't give ("north of", "between").
- Text boxes and callouts come first. Symbols the legend dates to the map's period ("significant fighting in the past 24 hours", "assessed advances") count when they surround or touch a printed place label: one finding per place, "near <place>". Leave out such symbols with no printed place at them.
- Keep the map's own attribution and hedging: ISW "assesses", a side "claimed", "reportedly". Never say who did something if the map doesn't say.
- Only what the map marks as happening in its own period (the date or dates in its title): attacks, strikes, clashes, advances, withdrawals, places changing hands, incursions. Leave out standing context: long-held positions, bases, infrastructure, the legend itself, and areas of control that did not change.
- If a second image is given, it is the previous edition of the same map. Also list clear changes between them in who controls ground or where the front line runs, near a printed place name ("ISW now assesses Russian forces control <place>; its map of <date> did not"). Only changes you are sure of; small differences in shading or drawing are not changes.
- At most {MAX_FINDINGS} findings, most significant first. An empty list is a fine answer.

JSON: {{"findings": [{{"text": "<one sentence in your own words, with the place name, and who, if the map says>", "place": "<place as printed on the map>", "region": "<its province, oblast, governorate or district, if shown>" or null, "country": "<ISO alpha-2 of the place>", "date": "<YYYY-MM-DD the map gives for it, else the map's date>"}}]}}"""


def _plain(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def _dates(title: str) -> list[datetime]:
    """Every day named in the title; a day without a year takes the next year given
    ("September 24 to September 30, 2026")."""
    found = list(_DATE_RE.finditer(title))
    out = []
    for n, m in enumerate(found):
        year = m.group(4) or next((f.group(4) for f in found[n + 1:] if f.group(4)), None)
        for day in filter(None, (m.group(2), m.group(3))):
            try:
                out.append(datetime.strptime(f"{m.group(1)} {day} {year}", "%B %d %Y"))
            except (ValueError, TypeError):
                pass
    return out


def skip_reason(title: str) -> str | None:
    """Why a map is not read: it covers a long period or a theme, not the last day."""
    dates = _dates(title)
    if not dates:
        return "no day in its title (a theme or a month)" if _MONTH_ONLY_RE.search(title) else "no date in its title"
    if (max(dates) - min(dates)).days > MAX_SPAN_DAYS:
        return f"covers {(max(dates) - min(dates)).days + 1} days"
    return None


def series(title: str) -> str:
    """The map's name without its date: "Assessed Control of Terrain in the Sumy Direction"."""
    m = _DATE_RE.search(title)
    name = title[: m.start()] if m else title
    name = re.sub(r"\b(as of|between|on|from)\s*$", "", name.strip(" ,–-"), flags=re.I)
    return re.sub(r"\s+", " ", name).strip(" ,–-").lower()


def priority(title: str, classes: list[str]) -> int:
    """Lower first: Yemen, then the Middle East and the whole-war Ukraine map, then the rest,
    then Ukraine's individual front sections."""
    cls = " ".join(classes or [])
    if _YEMEN_RE.search(title):
        return 0
    if "team-middle-east" in cls or "russo-ukrainian war" in title.lower():
        return 1
    if "focus-area-ukraine" in cls or "team-russia-ukraine" in cls:
        return 3
    return 2


def _image(content: str) -> str | None:
    """The 1638-wide rendition from the map's srcset, else the full image."""
    srcset = re.search(r'srcset="([^"]+)"', content or "")
    if srcset:
        options = []
        for part in srcset.group(1).split(","):
            bits = part.strip().split()
            if len(bits) == 2 and bits[1].endswith("w") and bits[1][:-1].isdigit():
                options.append((int(bits[1][:-1]), bits[0]))
        fits = [o for o in options if o[0] <= 2000]
        if fits:
            return min(fits, key=lambda o: abs(o[0] - IMAGE_WIDTH))[1]
    src = re.search(r'<img[^>]+src="([^"]+)"', content or "")
    return src.group(1) if src else None


def list_maps(session) -> list[dict]:
    r = session.get(API, params={"per_page": 40, "_fields": FIELDS}, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for m in r.json():
        title = _plain((m.get("title") or {}).get("rendered"))
        image = _image((m.get("content") or {}).get("rendered"))
        when = parse_time((m.get("date_gmt") or "") + "Z") if m.get("date_gmt") else None
        if not m.get("id") or not title or not image or not when:
            continue
        out.append({"id": str(m["id"]), "title": title, "link": m.get("link") or "", "image": image,
                    "date": iso(when), "series": series(title), "priority": priority(title, m.get("class_list"))})
    return out


def check(state: dict, session, health: dict, now: datetime) -> None:
    """List the Map Room (at most every CHECK_EVERY) and queue maps not seen before."""
    st = state.setdefault("maproom", {})
    seen, queue = st.setdefault("seen", {}), st.setdefault("queue", [])
    last = parse_time(st.get("checked"))
    if last and now - last < CHECK_EVERY:
        return
    st["checked"] = iso(now)
    try:
        maps = list_maps(session)
    except Exception as exc:  # noqa: BLE001 - try again next hour; the source panel shows the error
        log(f"[maproom] listing failed: {exc}")
        health["maproom"] = health_fail("ISW Map Room", "maproom", str(exc)[:200], health.get("maproom"))
        return
    added = skipped = 0
    for m in maps:
        if m["id"] in seen:
            continue
        seen[m["id"]] = m["date"]
        why = skip_reason(m["title"])
        if not why and now - parse_time(m["date"]) > MAX_AGE:
            why = "older than 36 hours"
        if why:
            skipped += 1
            log(f"[maproom] not read ({why}): {m['title']}")
            continue
        queue.append({**m, "tries": 0})
        added += 1
    cutoff = iso(now - timedelta(days=SEEN_DAYS))
    st["seen"] = {k: v for k, v in seen.items() if v >= cutoff}
    latest = max((parse_time(m["date"]) for m in maps), default=None)
    health["maproom"] = health_ok("ISW Map Room", "maproom", latest, len(maps), health.get("maproom"))
    log(f"[maproom] {len(maps)} maps listed, {added} new to read, {skipped} not read; {len(queue)} waiting")


def _data_url(session, url: str) -> str:
    r = session.get(url, headers={**HEADERS, "Accept": "image/*"}, timeout=TIMEOUT)
    r.raise_for_status()
    kind = (r.headers.get("content-type") or "").split(";")[0].strip() or "image/webp"
    if not kind.startswith("image/"):
        raise ValueError(f"not an image ({kind})")
    return f"data:{kind};base64,{base64.b64encode(r.content).decode()}"


def _previous(st: dict, m: dict) -> dict | None:
    """The last edition read of the same control-of-terrain map, if recent and older than this one."""
    if not _CONTROL_RE.search(m["title"]):
        return None
    prev = (st.get("series") or {}).get(m["series"])
    if not prev or prev.get("id") == m["id"] or prev["date"] >= m["date"]:
        return None
    if parse_time(m["date"]) - parse_time(prev["date"]) > PREVIOUS_MAX:
        return None
    return prev


def _items(m: dict, findings: list, now: datetime) -> list[dict]:
    out = []
    for n, f in enumerate(findings[:MAX_FINDINGS]):
        if not isinstance(f, dict) or not str(f.get("text") or "").strip() or not str(f.get("place") or "").strip():
            continue
        when = str(f.get("date") or "")[:10]
        text = (f"ISW map, {m['title']}: {str(f['text']).strip()}"
                + f" (Place on the map: {', '.join(str(f[k]).strip() for k in ('place', 'region', 'country') if f.get(k))}.)"
                + (f" Date: {when}." if re.fullmatch(r"\d{4}-\d{2}-\d{2}", when) else ""))
        out.append(make_item(SOURCE, "maproom", "isw-maproom", m["link"], text, parse_time(m["date"]) or now,
                             uid=f"{m['id']}:{n}"))
    return out


def read(state: dict, session, settings: dict, now: datetime, ask_json, budget: int) -> list[dict]:
    """Read up to PER_RUN waiting maps with the model (at most `budget` calls: what the day's
    maproom share and this run allow); their findings, as items for extraction."""
    st = state.setdefault("maproom", {})
    queue = [m for m in st.get("queue", []) if now - parse_time(m["date"]) <= MAX_AGE]
    if len(queue) < len(st.get("queue", [])):
        log(f"[maproom] {len(st.get('queue', [])) - len(queue)} maps waited too long and were left unread")
    queue.sort(key=lambda m: (m["priority"], m["date"] < iso(now - timedelta(hours=12)), m["date"]))
    st["queue"] = queue
    if budget <= 0 or not queue:
        return []
    items = []
    for m in list(queue[:min(PER_RUN, budget)]):
        try:
            images = [_data_url(session, m["image"])]
            prev = _previous(st, m)
            if prev:
                images.append(_data_url(session, prev["image"]))
        except Exception as exc:  # noqa: BLE001
            m["tries"] = m.get("tries", 0) + 1
            log(f"[maproom] could not fetch the image of {m['title']}: {exc}")
            if m["tries"] >= TRIES:
                queue.remove(m)
            continue
        ask = f"Map title: {m['title']}\nPublished: {m['date']}"
        if prev:
            ask += f"\nSecond image: the previous edition, {prev['title']}"
        got = ask_json(PROMPT, ask, state, settings, now, max_tokens=3000, purpose="maproom", images=images)
        if got is None:
            # the model was busy or its reply unreadable: one more try later
            m["tries"] = m.get("tries", 0) + 1
            if m["tries"] >= TRIES:
                queue.remove(m)
            break
        queue.remove(m)
        found = _items(m, got.get("findings") or [], now)
        items += found
        if _CONTROL_RE.search(m["title"]):
            st.setdefault("series", {})[m["series"]] = {k: m[k] for k in ("id", "title", "image", "date")}
        log(f"[maproom] read {m['title']}" + (f" (compared with {prev['date'][:10]})" if prev else "")
            + f": {len(found)} findings")
        for it in found:
            log(f"[maproom]   {it['text'][:240]}")
    cutoff = iso(now - timedelta(days=SEEN_DAYS))
    st["series"] = {k: v for k, v in (st.get("series") or {}).items() if v["date"] >= cutoff}
    return items
