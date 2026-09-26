#!/usr/bin/env python3
"""One update cycle: fetch sources, extract events, merge, and write the dashboard data.

Usage (GitHub Actions runs this every 15 minutes):
    python pipeline/run.py --state state --out out

Locally, set LLM_API_KEY to your Gemini API key to test extraction.
Add --no-llm to test fetching only.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config as config_mod  # noqa: E402
import extract  # noqa: E402
import geo  # noqa: E402
import merge  # noqa: E402
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
    args = ap.parse_args()

    started = time.time()
    t0 = now()
    cfg = config_mod.load()
    settings = cfg.settings
    state_dir, out_dir = Path(args.state), Path(args.out)

    state = load_json(state_dir / "state.json", default_state())
    for k, v in default_state().items():
        state.setdefault(k, v)
    stored = load_json(state_dir / "events.json", {})
    # Anything filed under a theater that is no longer configured is dropped.
    events: list[dict] = [e for e in stored.get("events", []) if e.get("theater") in cfg.theater_ids]
    cells: list[dict] = [c for c in stored.get("cells", []) if c.get("theater") in cfg.theater_ids]

    session = http_session()
    health: dict = state["health"]

    # 1. Fetch
    items = []
    items += bluesky.fetch(cfg.sources["bluesky"], session, health)
    items += rss.fetch(cfg.sources["rss"], session, health)
    items += telegram.fetch(cfg.sources["telegram"], state, health)
    log(f"[fetch] {len(items)} items")

    # 2. Keep only new, recent, conflict-related items
    seen = state["seen"]
    fresh = []
    for it in items:
        if it["id"] in seen:
            continue
        seen[it["id"]] = int(t0.timestamp())
        if hours_since(it["time"], t0) > settings["max_item_age_hours"]:
            continue
        if extract.is_candidate(it):
            fresh.append(it)
    log(f"[filter] {len(fresh)} new candidates")

    # 3. Extract with the model (budgeted); the rest waits in the queue
    queue = extract.build_queue(state["pending"], fresh, t0, settings)
    records, leftover, calls = extract.run(queue, state, settings, t0, disabled=args.no_llm)
    state["pending"] = leftover
    log(f"[extract] {len(records)} events from {calls} model calls; {len(leftover)} waiting")

    # 4. GDELT news-intensity cells
    rows = gdelt.fetch(state, session, cfg.theaters, health)
    cells = gdelt.update_cells(cells, rows, t0, settings["heat_retention_hours"], settings["max_heat_cells"])
    log(f"[gdelt] {len(rows)} rows -> {len(cells)} cells")

    # 5. Geocode, merge, score
    geocoder = geo.Geocoder(state["geocache"], session, settings["geocode_per_run"])
    candidates = [c for c in (geo.place_record(r, geocoder, cfg.theaters) for r in records) if c]
    log(f"[geo] placed {len(candidates)}/{len(records)} ({geocoder.calls} lookups)")
    events = merge.merge(events, candidates)
    events = merge.prune(events, t0, settings["event_retention_days"], settings["max_events"])
    merge.apply_status(events, cells)

    # 6. Housekeeping
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

    # 7. Write
    save_json(state_dir / "state.json", state)
    save_json(state_dir / "events.json", {"events": events, "cells": cells})
    public = {
        "generated_at": iso(t0),
        "theaters": theaters_meta(cfg.theaters),
        "events": [merge.public_event(e) for e in events],
        "heat": public_cells(cells),
        "sources": [dict(id=k, **v) for k, v in sorted(state["health"].items(), key=lambda kv: kv[1]["name"].lower())],
        "run": state["last_run"],
    }
    save_json(out_dir / "events.json", public)
    log(f"[done] {len(events)} events, {len(cells)} cells in {state['last_run']['seconds']}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
