#!/usr/bin/env python3
"""One update cycle: fetch sources, extract events, merge, and write the dashboard data.

Usage (GitHub Actions runs this every 15 minutes):
    python pipeline/run.py --state state --out out

Locally, set LLM_API_KEY to your Gemini API key to test extraction.
Add --no-llm to test fetching only.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import alerts  # noqa: E402
import archive  # noqa: E402
import brief  # noqa: E402
import config as config_mod  # noqa: E402
import extract  # noqa: E402
import fleet  # noqa: E402
import geo  # noqa: E402
import merge  # noqa: E402
import recency  # noqa: E402
from common import hours_since, http_session, iso, load_json, log, now, save_json  # noqa: E402
from sources import bluesky, gdelt, rss, telegram  # noqa: E402


def default_state() -> dict:
    return {"version": 1, "seen": {}, "pending": [], "geocache": {}, "health": {}}


def public_cells(cells: list[dict]) -> list[dict]:
    out = []
    for c in cells:
        labels = sorted(c.get("labels", {}).items(), key=lambda kv: -kv[1])
        out.append({
            "lat": c["lat"], "lon": c["lon"], "name": c["name"], "theater": c["theater"],
            "first": c["first"], "last": c["last"], "w": len(c["domains"]), "events": c["events"],
            "label": labels[0][0] if labels else None, "urls": c["urls"][:3],
        })
    return out


def theaters_meta(theaters: list[dict]) -> list[dict]:
    meta = [{
        "id": t["id"], "name": t["name"], "camera": t.get("camera"),
        "highlight": [geo.ISO_NUMERIC[c] for c in t.get("highlight", []) if c in geo.ISO_NUMERIC],
    } for t in theaters]
    return meta


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default="state", help="directory holding state.json and events.json")
    ap.add_argument("--out", default="out", help="directory to write the public events.json")
    ap.add_argument("--no-llm", action="store_true", help="skip the model step")
    ap.add_argument("--lookback-days", type=int, default=0,
                    help="backfill: also process posts up to this many days old (a manual one-off run)")
    args = ap.parse_args()

    started = time.time()
    t0 = now()
    cfg = config_mod.load()
    settings = cfg.settings
    state_dir, out_dir = Path(args.state), Path(args.out)
    lookback_h = max(0, min(args.lookback_days, 14)) * 24
    if lookback_h:
        settings["max_calls_per_run"] = max(settings["max_calls_per_run"], 20)
        log(f"[backfill] looking back {lookback_h // 24} days")

    state = load_json(state_dir / "state.json", default_state())
    for k, v in default_state().items():
        state.setdefault(k, v)
    stored = load_json(state_dir / "events.json", {})
    # Anything filed under a theater that is no longer configured is dropped.
    events: list[dict] = [e for e in stored.get("events", []) if e.get("theater") in cfg.theater_ids]
    # Events taken off the map by hand (pipeline/config/removed.yaml).
    events = [e for e in events if e.get("id") not in cfg.removed]
    cells: list[dict] = [c for c in stored.get("cells", []) if c.get("theater") in cfg.theater_ids]

    session = http_session()
    health: dict = state["health"]

    # 1. Fetch
    items = []
    items += bluesky.fetch(cfg.sources["bluesky"], session, health)
    items += rss.fetch(cfg.sources["rss"], session, health, lookback_h // 24, cfg.outlets)
    items += telegram.fetch(cfg.sources["telegram"], state, health)
    log(f"[fetch] {len(items)} items")

    # 2. Keep only new, recent, conflict-related items
    seen = state["seen"]
    # One-time: posts that the keyword filter rejected before visits, meetings, and "war" were
    # added to it (they were marked seen then) get one more look.
    if state.get("prefilter_version", 1) < 2:
        again = [it["id"] for it in items if it["id"] in seen and extract.rejected_before_added_words(it)]
        for i in again:
            seen.pop(i, None)
        state["prefilter_version"] = 2
        log(f"[filter] re-checking {len(again)} posts rejected by the older keyword filter")
    fresh = []
    for it in items:
        age = hours_since(it["time"], t0)
        older_than_normal = age > settings["max_item_age_hours"]
        # A backfill reconsiders older posts, which normal runs skipped; anything recent was already handled.
        if it["id"] in seen and not (lookback_h and older_than_normal):
            continue
        if older_than_normal:
            if not lookback_h or age > lookback_h:
                seen[it["id"]] = int(t0.timestamp())
                continue
            it["max_age_h"] = lookback_h
        # Posts that fail the keyword filter are not marked seen: feeds are re-read every run anyway,
        # so they are re-checked while still listed, and a better filter reaches recent posts at once.
        if extract.is_candidate(it):
            seen[it["id"]] = int(t0.timestamp())
            fresh.append(it)
    log(f"[filter] {len(fresh)} new candidates")

    # 3. Extract with the model (budgeted); the rest waits in the queue
    queue = extract.build_queue(state["pending"], fresh, t0, settings)
    records, leftover, calls, carrier_reports = extract.run(queue, state, settings, t0, disabled=args.no_llm)
    state["pending"] = leftover
    log(f"[extract] {len(records)} events from {calls} model calls; {len(leftover)} waiting")

    # 4. Carrier strike groups: weekly USNI tracker plus movements seen in today's posts
    if not args.no_llm:
        weekly = fleet.read_weekly_tracker(state, items, session, settings, t0, extract.ask_json)
        fleet.update(state, weekly)
        fleet.mark_deployment(state, weekly)
    moved = fleet.update(state, carrier_reports)
    fleet.apply_home_baseline(state)
    log(f"[fleet] {len(carrier_reports)} carrier reports, {moved} applied; {len(fleet.public(state, t0))} carriers shown")

    # 5. GDELT: used to corroborate reports (3+ outlets reporting violence nearby)
    if settings.get("gdelt", True):
        rows = gdelt.fetch(state, session, cfg.theaters, health)
        cells = gdelt.update_cells(cells, rows, t0, settings["heat_retention_hours"], settings["max_heat_cells"])
        log(f"[gdelt] {len(rows)} rows -> {len(cells)} cells")
    else:
        cells = []
        health.pop("gdelt", None)

    # 6. Geocode, merge, score. Records about events that happened long ago are recaps, not news.
    fresh_records = [r for r in records if hours_since(r.get("happened") or r["item"]["time"], t0)
                     <= r["item"].get("max_age_h", settings["max_item_age_hours"])]
    if len(fresh_records) < len(records):
        log(f"[extract] dropped {len(records) - len(fresh_records)} reports about older events")
    records = fresh_records
    geocoder = geo.Geocoder(state["geocache"], session, settings["geocode_per_run"])
    candidates = [c for c in (geo.place_record(r, geocoder, cfg.theaters) for r in records) if c]
    log(f"[geo] placed {len(candidates)}/{len(records)} ({geocoder.calls} lookups)")
    # One-time repair of diplomacy events that merged unrelated talks (see merge.split_mixed_talks).
    if not args.no_llm:
        events = merge.split_mixed_talks(events, state, extract.ask_json, settings, t0)
    known = {e["id"] for e in events}
    events = merge.merge(events, candidates)
    events = merge.prune(events, t0, settings["event_retention_days"], settings["max_events"])
    merge.apply_status(events, cells)
    # Old stories that arrived with a fresh date are dropped (see recency.py).
    if not args.no_llm:
        events = recency.check(events, {e["id"] for e in events} - known, session, extract.ask_json, state, settings, t0)

    # 7. Situation brief: at most one model call an hour, from the same daily budget
    published = [merge.public_event(e) for e in events]
    if not args.no_llm:
        brief.update(state, published, {t["id"]: t["name"] for t in cfg.theaters}, settings, t0,
                     extract.ask_json, extract.calls_remaining(state, settings, t0))

    # 8. Telegram alerts (skipped unless TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set)
    alerts.run(state, published, fleet.public(state, t0), cfg.alerts, {t["id"]: t["name"] for t in cfg.theaters},
               t0, os.environ)

    # 9. Housekeeping
    cutoff = int((t0 - timedelta(days=8)).timestamp())
    state["seen"] = {k: v for k, v in seen.items() if v >= cutoff}
    configured = {f"bsky:{s['handle'].lstrip('@')}" for s in cfg.sources["bluesky"]}
    configured |= {f"rss:{s.get('id') or s['url']}" for s in cfg.sources["rss"]}
    configured |= {f"tg:{s['username'].lstrip('@')}" for s in cfg.sources["telegram"]}
    configured.add("gdelt")
    state["health"] = {k: v for k, v in health.items() if k in configured}
    state["last_run"] = {
        "at": iso(t0), "seconds": round(time.time() - started, 1), "items": len(items),
        "candidates": len(fresh), "model_calls": calls, "events_added": len(candidates),
        "queue": len(leftover),
    }

    # 10. Archive on the data branch: one file per day, rewritten only when that day changed
    taken_down = {str(i): None for i in cfg.removed}
    for d in state.get("dropped_as_old", []):
        if d.get("event"):
            taken_down[d["event"]["id"]] = archive._day(d["event"])
    written = archive.update(state_dir, published, taken_down, fleet.public(state, t0), t0)
    log(f"[archive] {written} files updated")

    # 11. Write
    save_json(state_dir / "state.json", state)
    save_json(state_dir / "events.json", {"events": events, "cells": cells})
    public = {
        "generated_at": iso(t0),
        "theaters": theaters_meta(cfg.theaters),
        "events": published,
        "brief": state.get("brief"),
        "heat": public_cells(cells),
        "fleet": fleet.public(state, t0),
        "fleet_meta": {k: (state.get("fleet_meta") or {}).get(k) for k in ("tracker_time", "tracker_url")},
        "sources": [dict(id=k, **v) for k, v in sorted(state["health"].items(), key=lambda kv: kv[1]["name"].lower())],
        "run": state["last_run"],
    }
    save_json(out_dir / "events.json", public)
    log(f"[done] {len(events)} events, {len(cells)} cells in {state['last_run']['seconds']}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
