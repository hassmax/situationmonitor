"""Standing-control agent: no model. The claims agent hears about settlements only when a report
says they changed hands or are being fought over, so towns held for months or years (occupied
Melitopol, Houthi-run Sanaa, RSF-held Nyala) would never be shaded. This agent finds what news
outlets say about them as settled fact, the way they write it in headlines: "Russian-occupied
Melitopol", "Houthi-held Hodeidah", "the al-Shabaab stronghold of Jilib".

- Where it looks: the `standing` towns and cities listed per conflict in frontlines.yaml (which
  places to check, never who holds them), plus every settlement already in the ledger.
- How: a Google News search per listed town, PER_RUN a run, each town every EVERY (results from
  the last WHEN), and every item fetched this run is scanned too. Only exact phrasings count: a
  side's name joined to "occupied", "held", "controlled" or "run" right before the town's name,
  "<side> stronghold of <town>", and, where a side is marked `plain_occupied`, a bare "occupied
  <town>". "Formerly occupied", "near", a region ("Kherson region"), or a facility ("the
  Zaporizhzhia nuclear plant") don't count; a town whose name is also its region's (`city_only`)
  counts only as "<town> city" or "city of <town>".
- What it files: a claim per outlet and article, basis "described" (the outlet describes the town
  as held as a matter of fact), with the few words quoted and the link; it never changes anything
  on its own. The assessor counts two independent outlets describing the same holder within
  assess.DESCRIBED_WINDOW (one of them unaligned), or the other side's own media doing so, as
  strong evidence; the reviewer checks every change, as for all front-line evidence.
"""
from __future__ import annotations

import re
from datetime import timedelta
from urllib.parse import quote_plus

import feedparser

from common import clean_text, iso, log, parse_time
from sources.rss import _entry_time, _outlet_src

from . import ledger

PER_RUN = 4
FIRST_PASS = 8      # searches a run while some listed towns have never been searched
EVERY = timedelta(days=5)
WHEN = "60d"
RESULTS = 60
VERBS = r"(?:occupied|held|controlled|run)"
# words allowed between "-held" and the town: "Houthi-held capital Sanaa", "Houthi-run Yemeni
# capital Sanaa", "occupied town of Tokmak", "rebel-held east Congo city of Bukavu" (nothing else:
# "Russian-held areas near Pokrovsk" is not a description of Pokrovsk)
LEADS = (r"(?:(?:the|a|its|yemeni|yemen's|ukrainian|ukraine's|sudanese|congolese|somali|malian|burkinabe|nigerien|"
         r"burmese|myanmar's|ethiopian|east|west|north|south|congo|dr|drc|eastern|western|northern|southern|central|coastal|port|"
         r"regional|provincial|"
         r"key|strategic|capital|city|town|village|of)\s+){0,4}")
NOT_TOWN = (r"region|regions|oblast|oblasts|province|governorate|state|district|territory|territories|area|areas|"
            r"part|parts|nuclear|power|plant|NPP|airport|airbase|base|port|coast|coastline|front|direction|axis|"
            r"outskirts|suburbs?|countryside|villages|province's|region's|oblast's")
FORMERLY = re.compile(r"(?:formerly|previously|once|briefly|recently[\s-]+liberated|de)[\s-]*$", re.I)
SOURCE = {"name": "Google News (standing-control search)", "kind": "news", "group": "google-news", "weight": 1}


def towns(conflict: dict) -> list[dict]:
    """The conflict's listed towns: {name, names, region, country, city_only, key, reach_km, whole?}
    (reach_km: the town's or its group's own, for towns on a front line or a river; else None).
    A group marked `whole` also lists the region itself: a description of it ("Russian-occupied
    Crimea") counts for every town in it."""
    out = []
    for g in conflict.get("standing") or []:
        country = g.get("country") or conflict["countries"][0]
        members = []
        for t in g.get("places") or []:
            t = t if isinstance(t, dict) else {"name": t}
            names = [n.strip() for n in str(t["name"]).split("/") if n.strip()]
            members.append({"name": names[0], "names": names, "region": g.get("region"), "country": country,
                            "city_only": bool(t.get("city_only")), "key": ledger.key(names[0], country),
                            "reach_km": t.get("reach_km") or g.get("reach_km"), "front": bool(g.get("front")),
                            "provinces": list(t.get("provinces") or [])})
        out += members
        if g.get("whole"):
            names = [g["region"]] + list(g.get("aka") or [])
            out.append({"name": g["region"], "names": names, "region": g["region"], "country": country,
                        "city_only": False, "key": None, "reach_km": None, "front": False, "provinces": [],
                        "whole": members})
    return out


_PATTERNS: dict = {}


def _pattern(conflict: dict, names: list[str]) -> re.Pattern | None:
    k = (conflict["id"], tuple(sorted(names)))
    if k not in _PATTERNS:
        if len(_PATTERNS) > 64:
            _PATTERNS.clear()
        _PATTERNS[k] = _build(conflict, names)
    return _PATTERNS[k]


def _build(conflict: dict, names: list[str]) -> re.Pattern | None:
    words = sorted({w for a in conflict["actors"] for w in a.get("held_as") or []}, key=len, reverse=True)
    if not words or not names:
        return None
    adj = "|".join(re.escape(w).replace(r"\ ", r"[\s-]+") for w in words)
    town = "|".join(re.escape(n).replace(r"\ ", r"\s+") for n in sorted(set(names), key=len, reverse=True))
    plain = r"(?:temporarily\s+)?occupied" if any(a.get("plain_occupied") for a in conflict["actors"]) else None
    held = rf"(?P<adj>{adj})[\s-]+{VERBS}"
    hold = rf"(?P<adj2>{adj})(?:['’]s)?\s+(?:(?:northern|southern|eastern|western|last|key|main|major)\s+)?strongholds?(?:\s+of)?"
    lead = rf"(?:{held}|{hold}|{plain})" if plain else rf"(?:{held}|{hold})"
    return re.compile(rf"\b{lead}\s+{LEADS}(?P<town>{town})\b(?!-)(?P<after>\s+(?:(?:port\s+)?city\b|(?:{NOT_TOWN})\b))?", re.I)


def _actor_for(conflict: dict, word: str | None) -> str | None:
    if word is None:
        return next((a["id"] for a in conflict["actors"] if a.get("plain_occupied")), None)
    w = re.sub(r"[\s-]+", " ", word).lower()
    return next((a["id"] for a in conflict["actors"] if w in {x.lower() for x in a.get("held_as") or []}), None)


def find(text: str, conflict: dict, places: list[dict]) -> list[tuple[dict, str, str]]:
    """(place, actor, quoted words) for each description of a known place in the text as held."""
    by_name = {}
    for p in places:
        for n in p["names"]:
            by_name.setdefault(re.sub(r"\s+", " ", n).lower(), p)
    pat = _pattern(conflict, list(by_name))
    out, seen = [], set()
    if not pat:
        return out
    for m in pat.finditer(text or ""):
        if FORMERLY.search(text[max(0, m.start() - 20):m.start()]):
            continue
        p = by_name.get(re.sub(r"\s+", " ", m["town"]).lower())
        after = re.sub(r"\s+", " ", (m["after"] or "").strip().lower())
        if not p or (after and after not in ("city", "port city")):
            continue
        if p.get("city_only") and after not in ("city", "port city") and not re.search(r"\bof\s*$", text[m.start():m.start("town")], re.I):
            continue
        adj = m["adj"] if m["adj"] is not None else m["adj2"]
        actor = _actor_for(conflict, adj)
        if not actor or (p["name"], actor) in seen:
            continue
        seen.add((p["name"], actor))
        out.append((p, actor, re.sub(r"\s+", " ", m.group(0)).strip()))
    return out


def _claims(item: dict, conflict: dict, places: list[dict], now) -> list[dict]:
    t = parse_time(item.get("time"))
    if not t or now - t > timedelta(days=ledger.MEMORY_DAYS):
        return []
    out = []
    aligned = ledger.aligned_with(conflict, item.get("side"))
    for p, actor, words in find(item.get("text") or "", conflict, places):
        source = item.get("source") or "a news outlet"
        for town in p.get("whole") or [p]:
            said = f'{source} writes "{words}"'
            if p.get("whole"):   # a region one side holds all of: the description covers this town
                said += f", which covers all of {p['name']}, {town['name']} included"
            out.append({"name": town["name"], "region": town["region"], "country": town["country"], "conflict": conflict["id"],
                        "local": None, "hint": None,
                        "claim": {"time": iso(t), "actor": actor, "change": "holds", "claimed_by": None, "basis": "described",
                                  "aligned": aligned, "group": item.get("group") or source, "source": source,
                                  "url": item.get("url"), "summary": said, "event": None}})
    return out


def _known(conflict: dict, fl: dict) -> list[dict]:
    """Listed towns plus the settlements already in the ledger for this conflict."""
    listed = towns(conflict)
    keys = {t["key"] for t in listed if t["key"]}
    for k, p in fl["places"].items():
        if p["conflict"] == conflict["id"] and k not in keys and len(p["name"]) >= 4:
            listed.append({"name": p["name"], "names": [p["name"]], "region": p.get("region"), "country": p["country"],
                           "city_only": False, "key": k})
    return listed


def due(conflicts: list[dict], fl: dict, now) -> list[tuple[dict, dict]]:
    """Listed towns (and whole regions) whose search is due: never searched first, then oldest.
    Each conflict gets a share of the run's searches in proportion to how many of its towns are
    due (Ukraine's long list gets more, but every conflict gets some); FIRST_PASS a run while
    towns have never been searched, PER_RUN after that."""
    last = fl.setdefault("standing", {})
    queues = []
    for c in conflicts:
        q = []
        for t in towns(c):
            k = t["key"] or ledger.key(t["name"], t["country"]) + ":region"
            when = parse_time(last.get(k))
            if when and now - when < EVERY:
                continue
            q.append((last.get(k) or "", k, c, t))
        q.sort(key=lambda x: (x[0], not x[3].get("whole")))   # a whole region (Crimea) first: it covers many towns
        if q:
            queues.append(q)
    fresh = any(x[0] == "" for q in queues for x in q)
    limit = FIRST_PASS if fresh else PER_RUN
    taken = [0] * len(queues)
    out = []
    while len(out) < limit and any(queues):
        # the conflict with the most towns due per search it got this run
        i = max((j for j, q in enumerate(queues) if q), key=lambda j: (len(queues[j]) / (taken[j] + 1), -j))
        _, k, c, t = queues[i].pop(0)
        taken[i] += 1
        out.append((c, {**t, "search_key": k}))
    return out


def run(conflicts: list[dict], state: dict, session, now, items: list[dict], outlets: dict | None = None) -> list[dict]:
    """Claims from this run's fetched items and from due searches, as assess.add takes them."""
    fl = ledger.state_of(state)
    known = {c["id"]: _known(c, fl) for c in conflicts}
    found = []
    lowered = {cid: [n.lower() for t in ts for n in t["names"]] for cid, ts in known.items()}
    for it in items:
        text = (it.get("text") or "").lower()
        for c in conflicts:
            if any(n in text for n in lowered[c["id"]]):
                found += _claims(it, c, known[c["id"]], now)
    searched = 0
    for c, t in due(conflicts, fl, now):
        names = " OR ".join(f'"{n}"' for n in t["names"][:3])
        q = f"({names}) (occupied OR held OR controlled OR run OR stronghold) when:{WHEN}"
        url = f"https://news.google.com/rss/search?q={quote_plus(q)}&hl=en-US&gl=US&ceid=US%3Aen"
        try:
            r = session.get(url, timeout=20)
            r.raise_for_status()
            feed = feedparser.parse(r.content)
        except Exception as exc:  # noqa: BLE001 - tried again next run
            log(f"[frontline] standing search failed: {exc}; the rest wait for the next run")
            break
        fl["standing"][t["search_key"]] = iso(now)
        searched += 1
        for entry in feed.entries[:RESULTS]:
            published, link = _entry_time(entry), entry.get("link") or ""
            if not published or not link:
                continue
            src = _outlet_src(SOURCE, entry, outlets or {})
            title = clean_text(entry.get("title", ""))
            found += _claims({"text": title, "time": iso(published), "url": link, "source": src.get("name"),
                              "group": src.get("group"), "side": src.get("side")}, c, [t], now)
    keep = {(t["key"] or ledger.key(t["name"], t["country"]) + ":region") for c in conflicts for t in towns(c)}
    fl["standing"] = {k: v for k, v in fl["standing"].items() if k in keep}
    if searched or found:
        log(f"[frontline] standing: searched {searched} towns; {len(found)} descriptions of a town as held")
    return found
