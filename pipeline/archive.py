"""History kept on the data branch, in archive/.

  archive/<YYYY-MM-DD>.json         that UTC day's published events (by when they happened)
  archive/fleet/<YYYY-MM-DD>.json   the day's latest carrier positions

A day's file is updated, not rebuilt: events that leave the dashboard's 7-day working set stay in
the archive, while events taken down (hidden by a correction, or dropped as old news) are
removed from it. A file is rewritten only when its contents change, so pushes stay small.
Archive files are kept forever.
"""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from common import parse_time


def _day(e: dict) -> str | None:
    t = parse_time(e.get("time") or e.get("updated"))
    return t.strftime("%Y-%m-%d") if t else None


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"


def _write_if_changed(path: Path, text: str) -> bool:
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return True


def recent(root: Path, now, days: int) -> list[dict]:
    """Archived events from the last `days` days (by when they happened), for comparing new events
    with stories already reported (dedupe.late_cases). Unreadable files are skipped."""
    folder, out = Path(root) / "archive", []
    for k in range(days + 1):
        path = folder / f"{(now - timedelta(days=k)).strftime('%Y-%m-%d')}.json"
        try:
            out += json.loads(path.read_text(encoding="utf-8")).get("events", [])
        except (OSError, ValueError):
            continue
    return out


def update(root: Path, published: list[dict], removed: dict[str, str | None], fleet: list[dict], now) -> int:
    """Write changed day files under root/archive. `removed` maps taken-down event ids to their
    day (or None if unknown). Returns the number of files written."""
    folder = Path(root) / "archive"
    current: dict[str, str] = {}
    by_day: dict[str, list[dict]] = {}
    for e in published:
        d = _day(e)
        if d:
            current[e["id"]] = d
            by_day.setdefault(d, []).append(e)
    days = set(by_day) | {d for d in removed.values() if d}
    written = 0
    for day in sorted(days):
        path = folder / f"{day}.json"
        old = []
        if path.exists():
            try:
                old = json.loads(path.read_text(encoding="utf-8")).get("events", [])
            except ValueError:
                old = []
        # keep archived events unless taken down or now dated to another day; then add today's versions
        merged = {e["id"]: e for e in old if e.get("id") not in removed and current.get(e.get("id"), day) == day}
        merged.update({e["id"]: e for e in by_day.get(day, [])})
        if not merged and not path.exists():
            continue
        events = sorted(merged.values(), key=lambda e: (e.get("time") or "", e["id"]))
        written += _write_if_changed(path, _dump({"day": day, "events": events}))
    carriers = [c for c in fleet if c.get("lat") is not None]
    if carriers:
        today = now.strftime("%Y-%m-%d")
        written += _write_if_changed(folder / "fleet" / f"{today}.json", _dump({"day": today, "carriers": carriers}))
    return written
