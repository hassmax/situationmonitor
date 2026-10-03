"""The front-line ledger: shared by the agents. One record per settlement, in
state["frontline"]["places"][key]:

  name, region, country, conflict   as the reports give them
  lat, lon                          looked up once (None until found; `tries` counts failed lookups)
  claims                            evidence, newest last (at most MAX_CLAIMS, none older than
                                    MEMORY_DAYS): {time, actor, change, claimed_by, basis, aligned,
                                    group, source, url, summary, event, rejected?}
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
    return fl


def possessive(name: str) -> str:
    """"Russia's", "the Houthis'", "Yemeni government-aligned forces'"."""
    return f"{name}'" if name.endswith("s") else f"{name}'s"
