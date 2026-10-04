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
import corrections  # noqa: E402
import datecheck  # noqa: E402
import dedupe  # noqa: E402
import extract  # noqa: E402
import fleet  # noqa: E402
import frontline  # noqa: E402
import geo  # noqa: E402
import control  # noqa: E402
import hunter  # noqa: E402
import merge  # noqa: E402
import publish  # noqa: E402
import recency  # noqa: E402
from common import hours_since, http_session, iso, load_json, log, now, save_json  # noqa: E402
from sources import bluesky, gdelt, maproom, rss, telegram  # noqa: E402


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
        "id": t["id"], "name": t["name"], "camera": t.get("camera"), "listed": t.get("listed", True),
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
    merge.unique_ids(events)  # two events once shared an id when one roundup article gave both
    # Nothing can have happened after this run started (repairs events stored with a feed's
    # future-dated timestamps, which the map showed as "just now").
    stamp = iso(t0)
    for e in events:
        for k in ("time", "updated"):
            if (e.get(k) or "") > stamp:
                e[k] = stamp
        for r in e.get("reports", []):
            if (r.get("time") or "") > stamp:
                r["time"] = stamp
    # Corrections made by hand (pipeline/config/corrections.yaml), applied every run.
    fixes = corrections.load(config_mod.CONFIG_DIR / "corrections.yaml")
    hidden = corrections.hidden_ids(fixes)

    session = http_session()
    health: dict = state["health"]

    # 1. Fetch
    items = []
    items += bluesky.fetch(cfg.sources["bluesky"], session, health)
    items += rss.fetch(cfg.sources["rss"], session, health, lookback_h // 24, cfg.outlets)
    items += telegram.fetch(cfg.sources["telegram"], state, health)
    log(f"[fetch] {len(items)} items")
    # Google News reports stored before outlets were told apart carry only the search's name;
    # credit them to the outlet that published them (once over the past week, then as seen).
    labels = rss.outlet_labels(items)
    if state.get("outlet_labels_version", 0) < 1:
        labels = {**rss.fetch_outlet_labels(cfg.sources["rss"], session, cfg.outlets, 7), **labels}
        if labels:
            state["outlet_labels_version"] = 1
    relabeled = rss.relabel([r for e in events for r in e.get("reports", [])] + state["pending"], labels)
    relabeled += rss.relabel_by_name([r for e in events for r in e.get("reports", [])] + state["pending"], cfg.outlets)
    if relabeled:
        log(f"[fetch] credited {relabeled} Google News reports to the outlet that published them")

    # 2. Keep only new, recent, conflict-related items
    seen = state["seen"]
    # One-time, after words are added to the keyword filter (visits, meetings and "war"; then
    # treaties, bodies, expulsions and sanctions): posts it rejected before (marked seen then) get
    # one more look.
    before = state.get("prefilter_version", 1)
    if before < extract.PREFILTER_VERSION:
        again = [it["id"] for it in items if it["id"] in seen and extract.rejected_before_added_words(it, before)]
        for i in again:
            seen.pop(i, None)
        state["prefilter_version"] = extract.PREFILTER_VERSION
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
    # Corroboration hunter: targeted searches for important single-source events. No model calls;
    # the results join the normal queue ahead of everything else (weight 4).
    for it in hunter.run([e for e in events if e["id"] not in hidden], state, session, t0, cfg.outlets):
        if it["id"] not in seen and extract.is_candidate(it):
            seen[it["id"]] = int(t0.timestamp())
            fresh.append(it)
    # ISW's Map Room: the list of maps is checked hourly (no model); new maps are read by the model,
    # up to 3 a run under the day's "maproom" share, and what they report joins the queue (weight 4).
    maproom.check(state, session, health, t0)
    budget = 0 if args.no_llm else min(
        extract.share_left(state, settings, t0, "maproom"),
        extract.calls_allowed(state, settings, t0, reserve=int(settings.get("extraction_reserve", 30))))
    for it in maproom.read(state, session, settings, t0, extract.ask_json, budget):
        if it["id"] not in seen:
            seen[it["id"]] = int(t0.timestamp())
            fresh.append(it)
    # Front-line scout: more reporting on settlements resting on one side's claim; and the
    # standing-control agent: towns outlets describe as held ("Russian-occupied Melitopol"), from
    # this run's items and its own searches, filed in the front-line ledger (no model calls)
    for it in frontline.search(state, session, t0, cfg.outlets, cfg.frontlines, items):
        if it["id"] not in seen and extract.is_candidate(it):
            seen[it["id"]] = int(t0.timestamp())
            fresh.append(it)
    log(f"[filter] {len(fresh)} new candidates")

    # 3. Extract with the model (budgeted); the rest waits in the queue
    fresh = extract.skip_rejected(fresh, state, t0)
    queue = extract.build_queue(state["pending"], fresh, t0, settings)
    records, leftover, calls, carrier_reports = extract.run(queue, state, settings, t0, disabled=args.no_llm)
    state["pending"] = leftover
    log(f"[extract] {len(records)} events from {calls} model calls; {len(leftover)} waiting")

    # 4. Carrier strike groups: USNI's daily tracker (read directly, no model needed) plus movements
    #    seen in today's posts
    tracker = fleet.read_tracker(state, items, session, settings, t0, extract.ask_json)
    fleet.update(state, tracker)
    fleet.mark_deployment(state, tracker)
    fleet.repair(state)
    moved = fleet.update(state, carrier_reports)
    # held reports that fit the last tracker position
    moved += fleet.release_held(state, {r["url"]: r["source"] for e in events for r in e.get("reports", [])})
    fleet.apply_home_baseline(state, t0)
    log(f"[fleet] {len(carrier_reports)} carrier reports, {moved} applied; {len(fleet.public(state, t0))} carriers shown")

    # Territorial control: ISW's published control maps (no model calls; at most every 3 hours)
    control_layers = control.validate(cfg.control)
    control.update(state, session, control_layers, t0)

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
    geo.repair(events, geocoder, state)  # stored approximate pins, re-checked a few per run
    # Reports of hidden events, and dropped reports, never create or join an event again.
    blocked = corrections.blocked_urls(events, fixes)
    fleet.drop_held(state, blocked)
    candidates = [c for c in candidates if c["report"]["url"] not in blocked]
    # One-time repair of diplomacy events that merged unrelated talks (see merge.split_mixed_talks).
    if not args.no_llm:
        events = merge.split_mixed_talks(events, state, extract.ask_json, settings, t0)
    known = {e["id"] for e in events}
    events = merge.merge(events, candidates)
    events, folded = merge.consolidate(events, hidden)
    events, launches = merge.launch_sites(events, hidden)  # "fired from Wonsan": a launch area, not a target
    folded += launches
    geo.pin_commands(events)  # events placed at a US command go to its region (not its headquarters)
    merge.own_force_visits(events)  # a navy's port call is a deployment, not a supply route
    merge.own_procurement(events)  # a country buying from its own industry is arms production, not a route
    events = merge.prune(events, t0, settings["event_retention_days"], settings["max_events"])
    events = corrections.drop_reports(events, fixes)  # before scoring, so confidence is recomputed
    merge.apply_status(events, cells)
    # Old articles listed with a fresh date: the article's own publication date (no model calls).
    events = datecheck.check(events, session, state, t0)
    # Old stories that arrived with a fresh date are dropped (see recency.py).
    if not args.no_llm:
        events = recency.check(events, {e["id"] for e in events} - known, session, extract.ask_json, state, settings, t0)
        # Same story reported in different words or places: at most one model call an hour.
        # New events are also compared with older ones, archived ones included (late follow-ups).
        events, same = dedupe.run(events, state, settings, t0, extract.ask_json,
                                  extract.calls_remaining(state, settings, t0), hidden,
                                  archive.recent(state_dir, t0, dedupe.LATE_DAYS),
                                  share=extract.share_left(state, settings, t0, "dedupe"))
        folded += same
        if same:
            merge.apply_status(events, cells)

    # 7. Situation brief: at most one model call an hour, from the same daily budget
    # Hidden events are left out and edits applied; everything below uses this published list.
    # Single-source stories that look like old news stay on the map but are flagged "possibly an
    # old story" (recency.held): faded, not animated, left out of alerts, until a second source joins.
    doubtful = {e["id"] for e in events if recency.held(e)}
    if doubtful:
        log(f"[recency] flagged as possibly old until a second source reports it: {len(doubtful)}")
    published = corrections.publish([{**merge.public_event(e), **({"possibly_old": True} if e["id"] in doubtful else {})}
                                     for e in events], fixes, extract.EVENT_TYPES)
    # Front lines: the claims, assessor and reviewer agents (see frontline/), on this run's events
    reserve = int(settings.get("extraction_reserve", 30))
    frontline.update([e for e in events if e["id"] not in hidden], cfg.frontlines, state, settings, t0, extract.ask_json,
                     geo.Geocoder(state["geocache"], session, 30),
                     lambda purpose: min(extract.share_left(state, settings, t0, purpose),
                                         extract.calls_allowed(state, settings, t0, reserve=reserve)),
                     disabled=args.no_llm, session=session)
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
    configured.add("maproom")
    configured |= {f"control:{l['id']}" for l in control_layers}
    state["health"] = {k: v for k, v in health.items() if k in configured}
    state["last_run"] = {
        "at": iso(t0), "seconds": round(time.time() - started, 1), "items": len(items),
        "candidates": len(fresh), "model_calls": calls, "events_added": len(candidates),
        "queue": len(leftover),
    }

    # 10. Archive on the data branch: one file per day, rewritten only when that day changed
    taken_down = {str(i): None for i in cfg.removed}
    taken_down.update({e["id"]: archive._day(e) for e in events if e["id"] in hidden})
    taken_down.update({e["id"]: archive._day(e) for e in folded})  # now part of another event
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
        "control": control.public(state, control_layers, t0),
        "frontline": frontline.public(state, cfg.frontlines, t0),
        "fleet": fleet.public(state, t0),
        "fleet_meta": {k: (state.get("fleet_meta") or {}).get(k) for k in ("tracker_time", "tracker_url")},
        "sources": [dict(id=k, **v) for k, v in sorted(state["health"].items(), key=lambda kv: kv[1]["name"].lower())],
        "run": state["last_run"],
    }
    size = publish.write(out_dir, public)  # events.json, and the reports apart (see publish.py)
    log(f"[done] {len(events)} events, {len(cells)} cells, events.json {size // 1024} KB, in {state['last_run']['seconds']}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
