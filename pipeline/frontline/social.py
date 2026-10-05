"""Social media agent: reads Telegram and Bluesky posts directly (not the map's summaries of them)
for claims about who controls a settlement: flag-raising and assault videos, "geolocated to" posts,
a side's own announcements of captures and withdrawals. Added 2026-10-05 at the owner's request
("analysis of social media by our own agents"): until then only 54 of 1,668 control claims traced
back to a social channel, because the claims agent saw posts only after they had become events.

Who posted decides how much a claim weighs, never the model:
- A channel that speaks for a side (`side` in sources.yaml) is that side's statement (basis
  "party"), whatever footage it shows: its own flag-raising video is still its own claim. When a
  side's channel says the other side took a place, that is the side admitting the loss, filed as
  "lost" by its own side, which the assessor counts as strong (the losing side's admission).
- An unaligned channel's post counts as geolocated footage only when the post says the footage
  was geolocated or verified (or names who did it); as an analyst's assessment only from a source
  of kind "analysis"; otherwise it relays a side's claim ("party") or is unattributed.
- Footage of forces in a settlement, a flag raised in it, or an assault on it shows presence, not
  control: "contested", unless the post says the settlement was captured or cleared.

Posts fetched in a run that name capture, withdrawal, assault, flags or geolocation (CONTROL_RE, in
English, Ukrainian, Russian, Arabic and French) wait in state["frontline"]["social"]["queue"] for up
to KEEP; one model call a run reads BATCH of them (two while more than BACKLOG_EXTRA wait), purpose
"frontline_social": Cerebras first (providers.ROUTES), Gemini's paced share as the fallback. The
claims go into the same ledger as every other agent's, and nothing reaches the map without the
assessor's rules and the reviewer.
"""
from __future__ import annotations

import json
import re
from datetime import timedelta

from common import iso, log, parse_time

from . import ledger

PLATFORMS = {"telegram", "bluesky"}
KEEP = timedelta(hours=48)       # a post not read within this long is dropped from the queue
QUEUE_MAX = 300
BATCH = 25
BACKLOG_EXTRA = 60
TEXT_CHARS = 700
READ_MEMORY = timedelta(days=4)

CONTROL_RE = re.compile(
    r"captur|seiz|liberat|took (full )?control|taken control|in control of|entered|enter(ing)? the|flag|clear(ed|ing)|"
    r"mopp|storm|assault|withdr[ae]w|retreat|pull(ed)? (back|out)|encircl|surround|advanc|geolocat|geoconfirm|"
    r"footage|fell to|has fallen|lost control|front ?line|"
    r"звільн|захоп|прапор|штурм|відійш|відступ|оточ|просун|геолок|контрол|окупував|зайшли|"
    r"освобо|взят|взял|занял|зачист|флаг|отступ|окруж|продвин|вошли|"
    r"سيطر|تحرير|حرر|استعاد|اقتحم|انسحب|تقدم|تطهير|"
    r"pris le contr[oô]le|empar|repris|lib[ée]r[ée]|retir[ée]|encercl", re.I)
GEOLOCATED_RE = re.compile(r"geolocat|geoconfirm|verified|геолок|геопозиц", re.I)
# The model's own note still says "near", "will" or "towards": fighting around a place, or a forecast,
# is not a claim about the place (trial 2026-10-05: "repelled attacks near Sadky"; "the Houthis will
# gain full control of Taiz")
NOT_A_CLAIM_RE = re.compile(r"\b(near|nearby|around|outskirts|towards?|in the direction of|in the area of|vicinity|will|would|could|"
                            r"might|expect\w*|plan\w*|prepar\w*|about to)\b", re.I)

PROMPT = """You read posts from Telegram and Bluesky channels and list claims about who controls specific settlements, for a front-line map like the Institute for the Study of War's. Reply with one JSON object and nothing else. Posts may be in any language; answer in English.

Conflicts and their sides (use these ids exactly):
{conflicts}

Each post (identified by "i") gives its channel, the kind of channel, and "speaks_for" when the channel speaks for a side. For each post, list every settlement it says changed hands, is held, or is being fought over inside it. A settlement is a named city, town, village or small locality. A region, district, oblast, "direction", front, river, road or height is not a settlement, and neither is a facility in or near one (an airport, a base, a factory, a checkpoint).
- change: "took" (a side captured, seized, liberated, cleared or established control over it), "holds" (a side is said to keep or still hold it), "lost" (a side withdrew from it or lost it), "contested" (fighting inside it, an assault on it, or forces inside it while control is unclear).
- Footage of soldiers in a settlement, a flag raised in it, or an assault on it shows presence, not control: "contested", with actor = the side shown, unless the post says the settlement was captured or fully cleared.
- Not claims: fighting "near", "around", "towards" or "in the area of" a settlement, including "repelled attacks near X" and "advanced near X"; predictions, plans and expectations ("will take", "is about to fall", "preparing to storm"); strikes, shelling or drone attacks on it; casualties; prisoners. Leave those out.
- actor: the side the change is about (who took, holds or lost it; for "contested", the attacking side, else null). In "A recaptured X from B", A took X (actor A), not B.
- claimed_by: the side whose statement the post makes or relays. A post by a channel that speaks for a side is that side's statement, unless it reports the other side's claim (to deny or mock it, too): then claimed_by is the other side. null if the post names no side's statement.
- basis: "footage" (the post shows or cites video or imagery that it says was geolocated or verified), "on_scene" (a reporter or monitor at the place), "analyst" (the channel's own mapping or analysis), "party" (a side's statement), "unattributed".
- geolocated_by: who the post says geolocated or verified the footage, or null.
- settlement: its standard English name (for Ukraine the Ukrainian transliteration: Kupiansk, not Kupyansk). local_name: for Ukraine and Russia only, the name in Cyrillic, or null. region: the oblast, state or province, if the post gives it or the name is not shared with other places, else null. country: ISO 3166-1 alpha-2.
- date: YYYY-MM-DD the change happened if the post gives it, else null.
- note: what the post says about this settlement, in your own words, at most 15 words. Never copy the post's wording.
Never infer a claim the post does not make. An empty list is fine; most posts have none.

JSON: {{"posts": [{{"i": <n>, "claims": [{{"settlement": "...", "local_name": "..." or null, "region": "..." or null, "country": "..", "conflict": "<id>", "change": "...", "actor": "<id>" or null, "claimed_by": "<id>" or null, "basis": "...", "geolocated_by": "..." or null, "date": "YYYY-MM-DD" or null, "note": "..."}}]}}]}}"""


def _social(fl: dict) -> dict:
    s = fl.setdefault("social", {})
    s.setdefault("queue", [])
    s.setdefault("read", {})
    return s


def intake(items: list[dict], fl: dict, now) -> int:
    """Queue this run's Telegram and Bluesky posts that speak of control (CONTROL_RE)."""
    s = _social(fl)
    cutoff = iso(now - KEEP)
    known = set(s["read"]) | {p["id"] for p in s["queue"]}
    added = 0
    for it in items:
        if it.get("platform") not in PLATFORMS or it["id"] in known or (it.get("time") or "") < cutoff:
            continue
        text = it.get("text") or ""
        if not CONTROL_RE.search(text):
            continue
        s["queue"].append({"id": it["id"], "source": it.get("source"), "kind": it.get("kind"), "side": it.get("side"),
                           "group": it.get("group"), "url": it.get("url"), "time": it.get("time"), "text": text[:TEXT_CHARS]})
        known.add(it["id"])
        added += 1
    s["queue"] = sorted((p for p in s["queue"] if (p.get("time") or "") >= cutoff), key=lambda p: p["time"], reverse=True)[:QUEUE_MAX]
    return added


def _speaks_for(conflicts: list[dict], side: str | None) -> list[str]:
    return [f"{a} ({c['id']})" for c in conflicts for a in [ledger.aligned_with(c, side)] if a]


def _payload(batch: list[dict], conflicts: list[dict]) -> str:
    posts = []
    for n, p in enumerate(batch):
        speaks = _speaks_for(conflicts, p.get("side"))
        posts.append({"i": n, "channel": p.get("source"), "kind": p.get("kind"), **({"speaks_for": speaks} if speaks else {}),
                      "posted": (p.get("time") or "")[:10], "text": p.get("text")})
    return json.dumps({"posts": posts}, ensure_ascii=False)


def clean(c: dict, post: dict, conflicts: list[dict]) -> dict | None:
    """One claim from one post, with its weight set by who posted it (see the module notes)."""
    if not isinstance(c, dict):
        return None
    name = re.sub(r"\s+", " ", str(c.get("settlement") or "")).strip(" .,")
    country = str(c.get("country") or "").upper()[:2]
    conflict = ledger.conflict_for(conflicts, country, c.get("conflict"))
    if not name or len(name) > 60 or not conflict:
        return None
    ids = ledger.actor_ids(conflict)
    change = c.get("change")
    actor = c.get("actor") if c.get("actor") in ids else None
    if change not in ledger.CHANGES or (change != "contested" and not actor):
        return None
    said_by = c.get("claimed_by") if c.get("claimed_by") in ids else None
    basis = c.get("basis") if c.get("basis") in ledger.BASES else "unattributed"
    own = ledger.aligned_with(conflict, post.get("side"))
    text = post.get("text") or ""
    if own and said_by not in (None, own):
        aligned, claimed_by, basis = said_by, said_by, "party"       # the channel relays the other side's claim
    elif own:
        aligned, claimed_by, basis = own, own, "party"               # the side's own statement, whatever it shows
        if change in ("took", "holds") and actor != own and ledger.other(conflict, own) == actor:
            change, actor = "lost", own                              # the side says the other took it: its own loss
    else:
        aligned, claimed_by = said_by, said_by
        if basis == "footage" and not (c.get("geolocated_by") or GEOLOCATED_RE.search(text)):
            basis = "party" if said_by else "unattributed"           # footage nobody says was located
        if basis == "analyst" and post.get("kind") != "analysis":
            basis = "party" if said_by else "unattributed"           # an aggregator's word is not an assessment
        if said_by and basis not in ("footage", "on_scene"):
            basis = "party"
    posted = parse_time(post.get("time"))
    when = parse_time(f"{c['date']}T12:00:00Z") if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(c.get("date") or "")) else None
    if not when or not posted or when > posted or posted - when > timedelta(days=10):
        when = posted
    if not when:
        return None
    note = re.sub(r"\s+", " ", str(c.get("note") or "")).strip()[:140]
    if NOT_A_CLAIM_RE.search(note) or ledger.backwards(conflict, actor, change, note):
        return None
    geo = str(c.get("geolocated_by") or "").strip()[:60]
    summary = f"{post.get('source')}: {note}" + (f" (geolocated by {geo})" if geo and basis == "footage" else "")
    region = str(c.get("region") or "").strip() or None
    local = re.sub(r"\s+", " ", str(c.get("local_name") or "")).strip() or None
    return {"name": name, "local": local, "region": region, "country": country, "conflict": conflict["id"], "hint": None,
            "claim": {"time": iso(when), "actor": actor, "change": change, "claimed_by": claimed_by, "basis": basis,
                      "aligned": aligned, "group": post.get("group") or post.get("source"), "source": post.get("source"),
                      "url": post.get("url"), "summary": summary[:300], "event": None, "via": "social"}}


def run(conflicts: list[dict], state: dict, settings: dict, now, ask, budget: int) -> list[dict]:
    """Read queued posts (at most `budget` calls); returns the claims found, as assess.add takes them."""
    fl = ledger.state_of(state)
    s = _social(fl)
    cutoff = iso(now - READ_MEMORY)
    s["read"] = {k: v for k, v in s["read"].items() if v >= cutoff}
    queue = s["queue"]
    calls = min(budget, 2 if len(queue) > BACKLOG_EXTRA else 1)
    if not queue or calls <= 0:
        if queue:
            log(f"[frontline] social: {len(queue)} posts wait for model budget")
        return []
    system = PROMPT.format(conflicts=_conflicts_text(conflicts))
    found = []
    for _ in range(calls):
        batch = queue[:BATCH]
        if not batch:
            break
        got = ask(system, _payload(batch, conflicts), state, settings, now, max_tokens=6000, purpose="frontline_social")
        if got is None:
            log(f"[frontline] social: no answer from the model; {len(queue)} posts wait")
            break
        n_claims = 0
        for rep in got.get("posts") or []:
            i = rep.get("i") if isinstance(rep, dict) else None
            if not isinstance(i, int) or not 0 <= i < len(batch):
                continue
            for c in rep.get("claims") or []:
                claim = clean(c, batch[i], conflicts)
                if claim:
                    found.append(claim)
                    n_claims += 1
        for p in batch:  # read, whether or not it held a claim
            s["read"][p["id"]] = iso(now)
        queue = queue[len(batch):]
        log(f"[frontline] social: read {len(batch)} posts, {n_claims} control claims; {len(queue)} still waiting")
    s["queue"] = queue
    return found


def _conflicts_text(conflicts: list[dict]) -> str:
    from .claims import _conflicts_text as text
    return text(conflicts)
