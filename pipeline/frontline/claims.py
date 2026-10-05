"""Claims agent: reads the map's ground-fighting and territory reports (their model-written
summaries, never full posts) and lists every claim about who controls a specific settlement:
who took, holds, lost or is fighting inside it, on whose word, and on what evidence.

One budgeted model call a run (purpose "frontline", share frontline_daily_max), two while a large
backlog waits; each report is read once (state["frontline"]["read"]). Reports from the last
LOOKBACK only. Claims go into the ledger; the assessor decides what they add up to.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import timedelta

from common import iso, log, parse_time

from . import ledger

TYPES = {"territory", "ground"}
LOOKBACK = timedelta(days=7)
BATCH = 30          # a 50-report reply was cut off in a trial (2026-10-03)
BACKLOG_EXTRA = 150     # a second call this run when more than this many reports wait
READ_MEMORY = timedelta(days=10)

PROMPT = """You read short report summaries from a live conflict map and list claims about who controls specific settlements, for a front-line map like the Institute for the Study of War's. Reply with one JSON object and nothing else.

Conflicts and their sides (use these ids exactly):
{conflicts}

For each report (identified by "i"), list every settlement the report says changed hands, is held, or is being fought over inside it. A settlement is a named city, town, village or small locality. A region, district, oblast, state, "direction", front, river, road or height is not a settlement, and neither is a facility in or near one: capturing "the Mekelle airport", a base, a headquarters, a factory or a checkpoint is not capturing the town (leave it out).
- change: "took" (a side captured, seized, liberated, entered and now holds, or established control over it), "holds" (a side is said to keep or still hold it), "lost" (a side withdrew from it or lost it), "contested" (fighting inside it, a battle for it, or forces entered but control is unclear).
- Not claims: fighting "near", "around", "on the outskirts of" or "in the direction of" a settlement; advances "near" it; strikes, shelling or drone attacks on it; casualties there. Leave those out.
- actor: the side the change is about (who took, holds or lost it); for "contested", the side said to be attacking, else null.
- claimed_by: the side whose statement the report relays ("Russian MoD claims", "the army says", "Houthi media report"), else null. A source of the report that speaks for a side counts as that side's statement.
- basis: "footage" (geolocated or verified video or imagery), "on_scene" (a reporter or independent monitor at the place), "both_sides" (the report says both sides acknowledge it), "analyst" (an independent analysis or mapping group's assessment, such as ISW), "party" (only a side's statement), "unattributed" (no source given).
- settlement: its standard English name, one spelling for every report: for Ukraine the Ukrainian transliteration (Hrachivka, not Grachovka; Nesterne, not Nesternoye; Kupiansk, not Kupyansk). local_name: for Ukraine and Russia only, the same name in Cyrillic (Ukrainian for Ukraine, Russian for Russia), or null if you are not sure; null for every other country.
- region: the province, oblast, state or governorate it is in, as the report gives it, or if the report does not, the one you know it to be in when the name is not shared with other places; else null. country: ISO 3166-1 alpha-2 of the settlement.
- date: YYYY-MM-DD the change happened, if the report gives it, else null.
- note: what the report says about this settlement, in your own words, at most 15 words.
Never infer a claim the report does not make, and keep each side's claim as that side's claim. An empty list is fine.

JSON: {{"reports": [{{"i": <n>, "claims": [{{"settlement": "...", "local_name": "..." or null, "region": "..." or null, "country": "..", "conflict": "<id>", "change": "...", "actor": "<id>" or null, "claimed_by": "<id>" or null, "basis": "...", "date": "YYYY-MM-DD" or null, "note": "..."}}]}}]}}"""


def report_key(r: dict) -> str:
    return hashlib.sha1(f"{r.get('url')}|{r.get('summary')}".encode()).hexdigest()[:16]


def _conflicts_text(conflicts: list[dict]) -> str:
    lines = []
    for c in conflicts:
        sides = "; ".join(f"{a['id']} = {a['name']} ({a.get('aka') or a['name']})" for a in c["actors"])
        lines.append(f"- {c['id']} ({c['name']}, countries {', '.join(c['countries'])}): {sides}")
    return "\n".join(lines)


def waiting(events: list[dict], conflicts: list[dict], fl: dict, now) -> list[dict]:
    """Unread reports of ground fighting and territory events in a configured conflict, newest first."""
    since = now - LOOKBACK
    out = []
    for e in events:
        if e.get("type") not in TYPES or e.get("alert"):
            continue
        conflict = ledger.conflict_for(conflicts, e.get("country"))
        if not conflict:
            continue
        for r in e.get("reports") or []:
            t = parse_time(r.get("time"))
            if not r.get("summary") or not t or t < since:
                continue
            k = report_key(r)
            if k in fl["read"]:
                continue
            out.append({"key": k, "report": r, "event": e, "conflict": conflict})
    out.sort(key=lambda x: x["report"].get("time") or "", reverse=True)
    return out


def _payload(batch: list[dict]) -> str:
    items = []
    for n, x in enumerate(batch):
        r, e = x["report"], x["event"]
        speaks = ledger.aligned_with(x["conflict"], r.get("side"))
        items.append({"i": n, "conflict": x["conflict"]["id"], "posted": (r.get("time") or "")[:10],
                      "source": r.get("source"), **({"source_speaks_for": speaks} if speaks else {}),
                      "map_place": e.get("place"), "summary": r.get("summary")})
    return json.dumps({"reports": items}, ensure_ascii=False)


def _clean(c: dict, x: dict, conflicts: list[dict]) -> dict | None:
    """One claim, checked against the conflict's sides; None if it doesn't hold together."""
    if not isinstance(c, dict):
        return None
    name = re.sub(r"\s+", " ", str(c.get("settlement") or "")).strip(" .,")
    country = str(c.get("country") or x["event"].get("country") or "").upper()[:2]
    conflict = ledger.conflict_for(conflicts, country, c.get("conflict")) or (x["conflict"] if country in x["conflict"]["countries"] else None)
    if not name or len(name) > 60 or not conflict:
        return None
    ids = ledger.actor_ids(conflict)
    change = c.get("change")
    actor = c.get("actor") if c.get("actor") in ids else None
    claimed_by = c.get("claimed_by") if c.get("claimed_by") in ids else None
    basis = c.get("basis") if c.get("basis") in ledger.BASES else "unattributed"
    if change not in ledger.CHANGES or (change != "contested" and not actor):
        return None
    r = x["report"]
    posted = parse_time(r.get("time"))
    when = parse_time(f"{c['date']}T12:00:00Z") if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(c.get("date") or "")) else None
    if not when or not posted or when > posted or posted - when > timedelta(days=10):
        when = posted
    aligned = claimed_by or ledger.aligned_with(conflict, r.get("side"))
    region = str(c.get("region") or "").strip() or None
    local = re.sub(r"\s+", " ", str(c.get("local_name") or "")).strip() or None
    e = x["event"]
    # the event's own pin, when the event is about this settlement: a hint for the lookup
    hint = [e["lat"], e["lon"]] if e.get("lat") is not None and ledger.key(e.get("place") or "", country) == ledger.key(name, country) else None
    return {"name": name, "local": local, "region": region, "country": country, "conflict": conflict["id"], "hint": hint,
            "claim": {"time": iso(when), "actor": actor, "change": change, "claimed_by": claimed_by, "basis": basis,
                      "aligned": aligned, "group": r.get("group") or r.get("source"), "source": r.get("source"),
                      "url": r.get("url"), "summary": (r.get("summary") or "")[:300], "event": x["event"].get("id")}}


def run(events: list[dict], conflicts: list[dict], state: dict, settings: dict, now, ask, budget: int) -> list[dict]:
    """Read waiting reports (at most `budget` calls); returns the claims found, as
    {name, region, country, conflict, claim}."""
    fl = ledger.state_of(state)
    cutoff = iso(now - READ_MEMORY)
    fl["read"] = {k: v for k, v in fl["read"].items() if v >= cutoff}
    queue = waiting(events, conflicts, fl, now)
    calls = min(budget, 2 if len(queue) > BACKLOG_EXTRA else 1)
    if not queue or calls <= 0:
        if queue:
            log(f"[frontline] claims: {len(queue)} reports wait for model budget")
        return []
    system = PROMPT.format(conflicts=_conflicts_text(conflicts))
    found = []
    for _ in range(calls):
        batch, queue = queue[:BATCH], queue[BATCH:]
        if not batch:
            break
        got = ask(system, _payload(batch), state, settings, now, max_tokens=8000, purpose="frontline")
        if got is None:
            log("[frontline] claims: the model gave no answer; the reports wait")
            break
        n_claims = 0
        for rep in got.get("reports") or []:
            i = rep.get("i") if isinstance(rep, dict) else None
            if not isinstance(i, int) or not 0 <= i < len(batch):
                continue
            for c in rep.get("claims") or []:
                claim = _clean(c, batch[i], conflicts)
                if claim:
                    found.append(claim)
                    n_claims += 1
        for x in batch:  # read, whether or not it held a claim
            fl["read"][x["key"]] = iso(now)
        log(f"[frontline] claims: read {len(batch)} reports, {n_claims} control claims; {len(queue)} still waiting")
    return found


# Capture headlines from the standing-control searches (standing.py: "Sudanese army recaptures
# Sodari", "Government forces seize Mekelle"), read with the same prompt, under their own purpose
# "frontline_news" (share frontline_news_daily_max). Each headline is read once. The claim's text
# is the outlet's name and the model's note, never the headline.
NEWS_BATCH = 40
NEWS_BACKLOG_EXTRA = 120
NEWS_NOTE = """

Here each report is a news headline ("headline") found by searching for the settlement named in "searched_for". A headline about another place that shares the name, or about anything but who holds the settlement, gives no claim. A headline saying a side "says" or "claims" it took a place is that side's claim (claimed_by)."""


def _news_order(queue: list[dict]) -> list[dict]:
    """Newest first within each conflict, taken in turn, so Ukraine's long list can't crowd out the rest."""
    by = {}
    for x in queue:
        by.setdefault(x["conflict"], []).append(x)
    out, lists = [], [sorted(v, key=lambda x: x["time"], reverse=True) for v in by.values()]
    while any(lists):
        for v in lists:
            if v:
                out.append(v.pop(0))
    return out


def run_news(conflicts: list[dict], state: dict, settings: dict, now, ask, budget: int) -> list[dict]:
    fl = ledger.state_of(state)
    news = fl.get("news") or {}
    queue = _news_order(news.get("queue") or [])
    calls = min(budget, 2 if len(queue) > NEWS_BACKLOG_EXTRA else 1)
    if not queue or calls <= 0:
        if queue:
            log(f"[frontline] capture headlines: {len(queue)} wait for model budget")
        return []
    by_id = {c["id"]: c for c in conflicts}
    system = PROMPT.format(conflicts=_conflicts_text(conflicts)) + NEWS_NOTE
    found, done = [], set()
    for _ in range(calls):
        batch = [x for x in queue if x["key"] not in done and x["conflict"] in by_id][:NEWS_BATCH]
        if not batch:
            break
        rows = [{"key": x["key"], "conflict": by_id[x["conflict"]],
                 "report": {"time": x["time"], "source": x["source"], "side": x["side"], "group": x["group"],
                            "url": x["url"], "summary": x["headline"]},
                 "event": {"country": x["country"], "place": x["town"]}} for x in batch]
        payload = json.loads(_payload(rows))
        for item, x in zip(payload["reports"], batch):
            item["headline"] = item.pop("summary")
            item["searched_for"] = f"{x['town']} ({x.get('region') or x['country']})"
            item.pop("map_place", None)
        got = ask(system, json.dumps(payload, ensure_ascii=False), state, settings, now, max_tokens=8000, purpose="frontline_news")
        if got is None:
            log("[frontline] capture headlines: the model gave no answer; they wait")
            break
        n_claims = 0
        for rep in got.get("reports") or []:
            i = rep.get("i") if isinstance(rep, dict) else None
            if not isinstance(i, int) or not 0 <= i < len(rows):
                continue
            for c in rep.get("claims") or []:
                claim = _clean(c, rows[i], conflicts)
                if not claim:
                    continue
                source = (rows[i]["report"]["source"] or "a news outlet").removesuffix(" (via Google News)")
                note = re.sub(r"\s+", " ", str(c.get("note") or "")).strip()[:160]
                claim["claim"].update(summary=f"{source}: {note}" if note else f"{source} reports this in a headline",
                                      event=None, via="news")
                found.append(claim)
                n_claims += 1
        done |= {x["key"] for x in batch}
        log(f"[frontline] capture headlines: read {len(batch)}, {n_claims} control claims; "
            f"{len(queue) - len(done)} still waiting")
    news["queue"] = [x for x in news.get("queue") or [] if x["key"] not in done]
    return found
