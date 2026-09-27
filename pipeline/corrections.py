"""Corrections made by hand in config/corrections.yaml, applied on every run.

  hide         take an event off the dashboard (and the brief, alerts, and archive)
  edit         change summary, place, lat, lon, type, or severity
  drop_report  remove one report (by its URL) from an event; its confidence is recomputed

Every entry needs an id and a short note; entries without a note are ignored. Hides and edits
are applied to what is published, not to the stored event, so deleting an entry undoes it.
A dropped report is removed from the stored event before confidence is scored. Reports of
hidden events, and dropped reports, can never create or join an event again. Edited events
show a "Corrected" label with the note.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from common import log

EDITABLE = ("summary", "place", "lat", "lon", "type", "severity")


def load(path: Path) -> list[dict]:
    if not Path(path).exists():
        return []
    raw = (yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}).get("corrections") or []
    out = []
    for n, c in enumerate(raw):
        if not isinstance(c, dict) or not c.get("id"):
            log(f"[corrections] entry {n + 1} has no id; ignored")
            continue
        if not str(c.get("note") or "").strip():
            log(f"[corrections] entry for {c['id']} has no note; ignored (every correction needs a short note)")
            continue
        out.append({**c, "id": str(c["id"]).strip(), "note": str(c["note"]).strip()})
    return out


def hidden_ids(entries: list[dict]) -> set[str]:
    return {c["id"] for c in entries if c.get("hide")}


def blocked_urls(events: list[dict], entries: list[dict]) -> set[str]:
    """Report URLs that may never create or join an event again."""
    hidden = hidden_ids(entries)
    urls = {str(c["drop_report"]).strip() for c in entries if c.get("drop_report")}
    for e in events:
        if e["id"] in hidden:
            urls |= {r["url"] for r in e.get("reports", [])}
    return urls


def drop_reports(events: list[dict], entries: list[dict]) -> list[dict]:
    """Remove dropped reports from stored events (before confidence is scored). An event left
    with no reports is removed."""
    drops: dict[str, set[str]] = {}
    for c in entries:
        if c.get("drop_report"):
            drops.setdefault(c["id"], set()).add(str(c["drop_report"]).strip())
    kept = []
    for e in events:
        urls = drops.get(e["id"])
        if urls:
            before = len(e["reports"])
            e["reports"] = [r for r in e["reports"] if r["url"] not in urls]
            if len(e["reports"]) < before:
                log(f"[corrections] removed {before - len(e['reports'])} report(s) from {e['id']}")
            if not e["reports"]:
                log(f"[corrections] {e['id']} has no reports left; removed")
                continue
        kept.append(e)
    return kept


def _edit(e: dict, changes: dict, event_types) -> list[str]:
    done = []
    changes = changes if isinstance(changes, dict) else {}
    for k in EDITABLE:  # fixed order, so the label reads naturally
        if k not in changes:
            continue
        v = changes[k]
        if k in ("lat", "lon"):
            try:
                v = float(v)
            except (TypeError, ValueError):
                continue
            if not (-90 <= v <= 90 if k == "lat" else -180 <= v <= 180):
                continue
        elif k == "severity":
            try:
                v = int(v)
            except (TypeError, ValueError):
                continue
            if v not in (1, 2, 3):
                continue
        elif k == "type":
            if v not in event_types:
                continue
        else:
            v = str(v).strip()
            if not v:
                continue
        e[k] = v
        done.append(k)
    return done


def publish(published: list[dict], entries: list[dict], event_types) -> list[dict]:
    """Apply hides and edits to the published events and label what was corrected."""
    by_id: dict[str, list[dict]] = {}
    for c in entries:
        by_id.setdefault(c["id"], []).append(c)
    out = []
    for e in published:
        mine = by_id.get(e["id"])
        if not mine:
            out.append(e)
            continue
        if any(c.get("hide") for c in mine):
            continue
        e = dict(e)
        labels = []
        for c in mine:
            if c.get("edit"):
                fields = _edit(e, c["edit"], event_types)
                if fields:
                    labels.append({"note": c["note"], "change": "Edited " + ", ".join(fields)})
            if c.get("drop_report"):
                labels.append({"note": c["note"], "change": "Removed a report"})
        if labels:
            e["corrected"] = labels
        out.append(e)
    return out
