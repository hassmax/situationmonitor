"""ISW reader: the Institute for the Study of War's own written findings as front-line evidence
(the owner's choice, 2026-10-04: "Highlight the territory based on ISW analysis up front and then
have agents adjust"). Only ISW's text is used, as a news outlet would quote it, never its maps or
map data, whose terms forbid reuse without written permission.

- Which reports: ISW's sitemap (one request an hour, CHECK_EVERY) lists its research by date. The
  series in SERIES are read: the Russian Offensive Campaign Assessment and the Russian Occupation
  Update (Ukraine) and the Iran Update (the Houthi-Saudi war in Yemen; Israel in Lebanon and
  Syria). The Africa File (Sudan, the Sahel, eastern DRC, Somalia, Ethiopia) is published by ISW's partner, the Critical Threats Project,
  on criticalthreats.org only (never in ISW's sitemap, and its addresses end, not start, with
  "africa-file-<date>", so it was never read until 2026-10-04): its list page carries the reports as
  data (CTP_LIST, `INI_LIST`: slug, title, publication time). Up front, the last BACKFILL of
  reports; then each new one, newest first.
- How: the report page is fetched (as the Map Room reader does) and cut into sentences; the ones
  about ground changing hands or being held (CONTROL_RE) go to the claims agent's model prompt,
  BATCH a call, which lists settlement-level claims with their basis: "ISW assesses" is an
  independent analyst's assessment, geolocated footage is footage, and a side's claim that ISW
  relays ("the Russian MoD claimed") stays that side's claim. A report is read over several runs
  if need be (`state["frontline"]["isw"]["read"]` keeps where it stopped).
- Every sentence is also scanned for "occupied Melitopol"-style descriptions of listed towns
  (standing.find, no model): ISW describing a town as held counts like any outlet doing so.
- What is kept: the claims, credited to ISW or Critical Threats (one group, "isw", unaligned: the
  two work together, so they never corroborate each other) and linked to the report, with a note
  in the model's own words; never their text.

Model calls: purpose "frontline_isw", share frontline_isw_daily_max (not paced over the day, so the
backlog is read up front), at most CALLS_PER_RUN a run.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

from common import iso, log, parse_time
from sources.maproom import HEADERS

from . import claims, ledger, standing

SITEMAP_INDEX = "https://understandingwar.org/sitemap_index.xml"
CTP_LIST = "https://www.criticalthreats.org/analysis/africa-file"
CTP_BASE = "https://www.criticalthreats.org/analysis/"
AFRICA = ["sudan", "sahel", "drc", "somalia", "ethiopia"]
CHECK_EVERY = timedelta(hours=1)
BACKFILL = timedelta(days=14)
CALLS_PER_RUN = 2
BATCH = 40
TIMEOUT = 30
# URL slug prefix -> (series name, conflicts it covers)
SERIES = {
    "russian-offensive-campaign-assessment": ("Russian Offensive Campaign Assessment", ["ukraine"]),
    "russian-occupation-update": ("Russian Occupation Update", ["ukraine"]),
    "iran-update": ("Iran Update", ["yemen", "israel"]),
}
MONTHS = "january|february|march|april|may|june|july|august|september|october|november|december"
CONTROL_RE = re.compile(
    r"\b(seiz|captur|advanc|liberat|recaptur|took|take|taken|control|hold|held|withdr|retreat|enter|occup|"
    r"infiltrat|positions? (?:in|near)|counterattack|clear|encircl|fighting (?:in|within)|assault)", re.I)


def _date(slug: str) -> datetime | None:
    m = re.search(rf"({MONTHS})-(\d{{1,2}})-(\d{{4}})", slug)
    if not m:
        return None
    try:
        return datetime.strptime(f"{m[1]} {m[2]} {m[3]} 23:00 +0000", "%B %d %Y %H:%M %z")
    except ValueError:
        return None


def _series(url: str) -> tuple[str, str, list[str]] | None:
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    for prefix, (name, conflicts) in SERIES.items():
        if slug.startswith(prefix) and "updates" not in slug:
            return prefix, name, conflicts
    return None


def listing(session, fl: dict, now) -> list[dict]:
    """Recent reports of the read series, from ISW's sitemap (checked at most hourly; the list is
    kept between checks)."""
    st = fl.setdefault("isw", {"read": {}, "reports": [], "checked": None})
    checked = parse_time(st.get("checked"))
    if checked and now - checked < CHECK_EVERY:
        return st["reports"]
    try:
        r = session.get(SITEMAP_INDEX, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        maps = [u for u in re.findall(r"<loc>([^<]+)</loc>", r.text) if "/post-sitemap" in u]
        maps.sort(key=lambda u: int(re.search(r"post-sitemap(\d*)", u)[1] or 1))
        reports = []
        for sm in maps[-2:]:   # the newest posts are in the last sitemaps
            r = session.get(sm, headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            for url in re.findall(r"<loc>([^<]+)</loc>", r.text):
                ser, day = _series(url), _date(url)
                if ser and day and now - day <= BACKFILL:
                    reports.append({"url": url, "series": ser[1], "conflicts": ser[2], "date": iso(min(day, now)), "publisher": "ISW"})
    except Exception as exc:  # noqa: BLE001 - ISW's last list is kept; tried again next run
        log(f"[frontline] ISW sitemap failed: {exc}")
        reports = [x for x in st["reports"] if x.get("publisher", "ISW") == "ISW"]
    reports += africa_file(session, now)
    reports.sort(key=lambda x: x["date"], reverse=True)
    st["reports"], st["checked"] = reports, iso(now)
    return reports


def africa_file(session, now) -> list[dict]:
    """The Critical Threats Project's Africa File reports of the last BACKFILL, from the data its
    list page carries (`var INI_LIST = [...]`), or failing that the report links on the page."""
    try:
        r = session.get(CTP_LIST, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - tried again at the next check
        log(f"[frontline] Critical Threats Africa File list failed: {exc}")
        return []
    out = []
    m = re.search(r"var INI_LIST\s*=\s*(\[.*?\]);?\s*</script>", r.text, re.S)
    try:
        items = json.loads(m.group(1)) if m else []
    except ValueError:
        items = []
    for it in items:
        slug, ts = str(it.get("slug") or ""), it.get("published_timestamp")
        if not slug or not isinstance(ts, (int, float)) or "year-in-review" in slug:
            continue
        day = datetime.fromtimestamp(ts, tz=now.tzinfo)
        if now - day <= BACKFILL:
            out.append({"url": CTP_BASE + slug, "series": "Africa File", "conflicts": AFRICA, "date": iso(min(day, now)), "publisher": "Critical Threats"})
    if not items:   # no data on the page: the links, dated by their address
        for url in sorted(set(re.findall(r'href="(https://www\.criticalthreats\.org/analysis/[^"#]*africa-file-[a-z]+-\d{1,2}-\d{4})"', r.text))):
            day = _date(url.rsplit("/", 1)[-1])
            if day and now - day <= BACKFILL:
                out.append({"url": url, "series": "Africa File", "conflicts": AFRICA, "date": iso(min(day, now)), "publisher": "Critical Threats"})
    log(f"[frontline] Critical Threats: {len(out)} Africa File reports from the last {BACKFILL.days} days")
    return out


def sentences(html: str) -> list[str]:
    """The report's text, as sentences (scripts, styles, navigation and footnotes dropped)."""
    body = re.search(r"(?s)<(article|main)\b.*?</\1>", html)
    html = body.group(0) if body else html
    html = re.sub(r"(?s)<(script|style|nav|header|footer|figure)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;|&#160;", " ", text)
    text = re.sub(r"&#8217;|&rsquo;", "'", re.sub(r"&#822[01];|&[lr]dquo;", '"', text))
    text = re.sub(r"\[\d+\]", " ", re.sub(r"\s+", " ", text))
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z\"])", text) if 30 <= len(s.strip()) <= 700]


def run(conflicts: list[dict], state: dict, settings: dict, session, now, ask, budget: int) -> list[dict]:
    """Claims from ISW's reports (at most `budget` model calls), as assess.add takes them."""
    fl = ledger.state_of(state)
    reports = listing(session, fl, now)
    st = fl["isw"]
    cutoff = iso(now - BACKFILL - timedelta(days=2))
    st["read"] = {u: v for u, v in st["read"].items() if (v.get("date") or "") >= cutoff}
    by_id = {c["id"]: c for c in conflicts}
    found, calls, down = [], 0, set()
    for rep in reports:
        mark = st["read"].get(rep["url"]) or {}
        if mark.get("done") or calls >= min(budget, CALLS_PER_RUN) or rep.get("publisher", "ISW") in down:
            continue
        cs = [by_id[c] for c in rep["conflicts"] if c in by_id]
        if not cs:
            continue
        try:
            r = session.get(rep["url"], headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
        except Exception as exc:  # noqa: BLE001 - tried again next run
            log(f"[frontline] {rep.get('publisher', 'ISW')} report failed: {rep['url']}: {exc}")
            down.add(rep.get("publisher", "ISW"))   # that site is down this run; the other's reports still go
            continue
        text = sentences(r.text)
        pub = rep.get("publisher") or "ISW"
        source = {"url": rep["url"], "time": rep["date"], "summary": "", "source": f"{pub} ({rep['series']})",
                  "group": "isw", "side": None}
        if not mark:   # first look: towns ISW describes as held ("occupied Melitopol"), no model
            for c in cs:
                found += standing._claims({"text": " ".join(text), "time": rep["date"], "url": rep["url"],
                                           "source": source["source"], "group": "isw", "side": None},
                                          c, standing._known(c, fl), now)
        todo = [s for s in text if CONTROL_RE.search(s)]
        start = int(mark.get("at", 0))
        system = claims.PROMPT.format(conflicts=claims._conflicts_text(cs))
        while start < len(todo) and calls < min(budget, CALLS_PER_RUN):
            batch = todo[start:start + BATCH]
            items = [{"i": n, "conflict": cs[0]["id"] if len(cs) == 1 else None, "posted": rep["date"][:10],
                      "source": pub, "summary": s} for n, s in enumerate(batch)]
            got = ask(system, json.dumps({"reports": items}, ensure_ascii=False), state, settings, now,
                      max_tokens=8000, purpose="frontline_isw")
            calls += 1
            if got is None:
                log(f"[frontline] {pub}: the model gave no answer; the report waits")
                break
            n_claims = 0
            for rep_out in got.get("reports") or []:
                i = rep_out.get("i") if isinstance(rep_out, dict) else None
                if not isinstance(i, int) or not 0 <= i < len(batch):
                    continue
                for c in rep_out.get("claims") or []:
                    cl = claims._clean(c, {"report": source, "event": {}, "conflict": cs[0]}, conflicts)
                    if cl:
                        note = re.sub(r"\s+", " ", str(c.get("note") or "")).strip()[:160]
                        cl["claim"]["summary"] = f"{pub}: {note}" if note else f"{pub} {rep['series']}, {rep['date'][:10]}"
                        found.append(cl)
                        n_claims += 1
            start += len(batch)
            log(f"[frontline] {pub} {rep['series']} {rep['date'][:10]}: read {start}/{len(todo)} sentences, {n_claims} control claims")
        st["read"][rep["url"]] = {"date": rep["date"], "at": start, "done": start >= len(todo)}
    return found
