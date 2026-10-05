"""The front-line ledger: shared by the agents. One record per settlement, in
state["frontline"]["places"][key]:

  name, region, country, conflict   as the reports give them
  lat, lon                          looked up once (None until found; `tries` counts failed lookups)
  claims                            evidence, newest last (at most MAX_CLAIMS, none older than
                                    MEMORY_DAYS): {time, actor, change, claimed_by, basis, aligned,
                                    group, source, url, summary, event, rejected?}; basis
                                    "described" comes from the standing-control agent
  published                         what the map shows, set only by the reviewer:
                                    {holder, status, since, reviewed, note}
  asked                             the evidence count when the reviewer last saw a change it left
                                    pending (it is asked again only when new evidence arrives)
"""
from __future__ import annotations

import re
import unicodedata

MAX_CLAIMS = 40
MEMORY_DAYS = 60

CHANGES = {"took", "holds", "lost", "contested"}
BASES = {"footage", "on_scene", "both_sides", "analyst", "party", "unattributed"}
DESCRIBED = "described"   # an outlet calls the town held as settled fact ("Russian-occupied Melitopol"; standing.py)
STRONG_BASES = {"footage", "on_scene", "both_sides", "analyst"}


def key(name: str, country: str) -> str:
    """"Mala Rybytsya" / "Mala Rybytsia" in UA -> "ua:mala rybytsya" (accents and punctuation dropped)."""
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s.replace("'", "")).strip()
    s = re.sub(r"\s+", " ", s)
    return f"{(country or '').lower()}:{s}"


def conflict_for(conflicts: list[dict], country: str | None, conflict_id: str | None = None) -> dict | None:
    if conflict_id:
        c = next((c for c in conflicts if c["id"] == conflict_id), None)
        if c and (not country or country.upper() in c["countries"]):
            return c
    return next((c for c in conflicts if country and country.upper() in c["countries"]), None)


def actor_ids(conflict: dict) -> set[str]:
    return {a["id"] for a in conflict["actors"]}


def actor(conflict: dict, actor_id: str | None) -> dict | None:
    return next((a for a in conflict["actors"] if a["id"] == actor_id), None)


def aligned_with(conflict: dict, side: str | None) -> str | None:
    """The actor a source's `side` code makes it speak for in this conflict, if any."""
    if not side:
        return None
    return next((a["id"] for a in conflict["actors"] if side in (a.get("sources") or [])), None)


def other(conflict: dict, actor_id: str | None) -> str | None:
    """The opponent, in a two-sided conflict."""
    ids = [a["id"] for a in conflict["actors"]]
    return next(iter(set(ids) - {actor_id}), None) if len(ids) == 2 and actor_id in ids else None


def state_of(state: dict) -> dict:
    fl = state.setdefault("frontline", {})
    fl.setdefault("places", {})
    fl.setdefault("read", {})      # report key -> when the claims agent read it
    fl.setdefault("scout", {})     # place key -> search times
    fl.setdefault("standing", {})  # listed town key -> when the standing-control agent last searched it
    return fl


def possessive(name: str) -> str:
    """"Russia's", "the Houthis'", "Yemeni government-aligned forces'"."""
    return f"{name}'" if name.endswith("s") else f"{name}'s"


def _names(a: dict) -> list[str]:
    """The words reports use for a side: its id, name, aka and held_as."""
    aka = re.sub(r"\(.*?\)", "", a.get("aka") or "")
    out = [a["id"], a["name"]] + re.split(r",|\bor\b", aka) + list(a.get("held_as") or [])
    out = [re.sub(r"^the\s+", "", x.strip(), flags=re.I).strip(" '\"") for x in out]
    return sorted({x for x in out if len(x) >= 3}, key=len, reverse=True)


def backwards(conflict: dict, actor_id: str | None, change: str | None, note: str) -> bool:
    """A claim that a side took or holds a place whose own note says it was taken FROM that side
    ("Yemeni forces recapture Mokha from Houthis", filed as a Houthi capture, 2026-10-05)."""
    a = actor(conflict, actor_id)
    if change not in ("took", "holds") or not a or not note:
        return False
    names = "|".join(re.escape(x) for x in _names(a))
    return bool(re.search(rf"\bfrom\s+(?:the\s+)?(?:{names})(?:n|s|ns)?\b(?![\s-]*(?:border|frontier|territory|side\b|lines?\b|positions?\b))",
                          note, re.I))
