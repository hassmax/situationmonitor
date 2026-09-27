# Global Situation Monitor: notes for Claude Code

A self-updating 3D globe of armed-conflict events, built from public OSINT posts and news feeds, running entirely on free services. The owner is a law student focused on national security, not a developer: explain changes in plain language in PR descriptions, and keep the tool simple, fast, and honest about uncertainty.

## How it runs

- `.github/workflows/update.yml` runs every 15 minutes (plus on pushes to `main` that touch `site/`, `pipeline/`, or the workflow, and manually with an optional `lookback_days` backfill input).
- It loads state from the orphan `data` branch (`state.json`, `events.json`), runs `pipeline/run.py`, force-pushes the new state back to `data`, copies `site/` plus the generated `out/events.json` into `_site/`, stamps `__BUILD__` in `index.html` with the commit SHA (cache-busting), and deploys to GitHub Pages.
- Secrets: `GEMINI_API_KEY` (free Gemini API, OpenAI-compatible endpoint), `TG_API_ID`, `TG_API_HASH`, `TG_SESSION` (Telegram via Telethon). Optional variable `NOMINATIM_EMAIL`. Never print, log, or commit secret values.
- Free-tier limits matter: Gemini Flash-Lite allows roughly 500 requests/day (the pipeline budgets 400, batching ~25 posts per request); Actions minutes are free because the repo is public.

## Layout

- `pipeline/run.py`: orchestrator. Fetch → keyword prefilter → model extraction (batched, budgeted, queued) → geocode (OSM Nominatim, cached) → merge → confidence scoring → write `events.json`.
- `pipeline/sources/`: `bluesky.py` (public API, no login), `telegram.py`, `rss.py` (incl. Google News searches), `gdelt.py` (used only to corroborate, not drawn).
- `pipeline/extract.py`: the model prompt (`SYSTEM_PROMPT`), event types, keyword prefilter (`CONFLICT_RE`), model probing and batching.
- `pipeline/geo.py`: geocoding, theater assignment, named-sea fallback coordinates.
- `pipeline/recency.py`: drops old stories that arrive with a fresh date. News-only events with 2+ different current headlines on the topic always stay; only without current coverage does the model compare against coverage 2+ weeks older, and it must name the matching older headline. Bump `CHECK_VERSION` when changing the rule (drops are put back and everything is re-checked).
- `pipeline/merge.py`: merging reports into events, attack waves, drone and missile alert groups, confidence rules.
- `pipeline/fleet.py`: US aircraft carriers (USNI Fleet Tracker feed + movement reports; home-port baseline in `CARRIERS` and `HOME`).
- `pipeline/config/theaters.yaml`, `pipeline/config/sources.yaml`: theaters and sources. If a theater is added or renamed, also update the theater list in `SYSTEM_PROMPT`.
- `site/`: static front end. `index.html`, `styles.css`, `app.js` (vanilla JS, no build step), `vendor/globe.gl.min.js` (2.46.2, vendored), `assets/countries-110m.json`, `data/demo-events.json` (clearly labeled sample data for `?demo`).

## Testing before you push

- Python: `pip install -r pipeline/requirements.txt`, then `python -m pyflakes pipeline` and `python pipeline/run.py --state /tmp/state --out /tmp/out --no-llm` (needs network; skips the model).
- Front end: `python -m http.server --directory site 8000`, open `http://localhost:8000/?demo`. Check the browser console for errors, desktop and a ~390 px mobile width. To test real-shaped data, copy a generated `events.json` to `site/data/events.json`, but never commit that file.
- If startup breaks, the page shows the error in the feed panel (see `fatal()` in `app.js`); keep that behavior.

## Rules that must not be broken

- **Honesty over drama.** Summaries are written by the model in its own words; only summaries and links are published, never full post text. Never add or imply attribution a source doesn't make (a UKMTO "unknown projectile" stays unknown until someone attributes it). The legal layer records stated legal bases; it never assesses them.
- **Confidence rules** (`merge.apply_status`): corroborated = 2+ independent source groups with at least one unaligned (or opposing sides agree); unconfirmed = one unaligned source; claimed = only sources aligned with one side. Sources with the same `side` never corroborate each other. GDELT (3+ outlets nearby) counts as one unaligned source. Google News results count per outlet for outlets listed under `outlets:` in `sources.yaml` (tier 1 = reputable, weight 3; state or partisan media carry a `side`); unlisted outlets share the `google-news` group, which only counts when no listed outlet has reported the event (syndicated copy must not corroborate its original).
- **Time.** Events are timed by when they happened (`time`, from the model's `happened` field, else the post time). `updated` is only the latest report. The merge window is measured from `time` and must never slide forward with later reports (that bug made old events reappear as new). The front end uses `time` for the window, sorting, and "new".
- **Positions are never estimated.** Carriers show their last reported position with its date; supply routes only use named endpoints (country-level routes are drawn faint); launch lines from assumed launch areas are drawn faint.
- **Map language.** Icon and color show what happened; marker style shows confidence (solid / outline / dashed). Zoomed out, 3+ nearby events collapse into the lead event's icon with a count; closer in, overlapping markers fan out. Only recent events animate (at most `MAX_ANIMATED` at once). Respect `prefers-reduced-motion`. Warnings that drones or missiles are in flight ("heading toward X", nothing hit) are grouped into one siren marker per country per day; they never become places hit in an attack wave.
- **Performance.** Keep it light: no frameworks, no build step, no new heavy layers. Cap marker and line counts as the code already does.
- UI copy: plain, sentence case, no jargon. Update `README.md` when behavior changes.

## Owner preferences

- Default time window: 24 hours, opening on the busiest area of activity.
- Everything on one screen (no separate views).
- Coverage priorities: Russia–Ukraine, NATO flank and Russian hybrid attacks, Middle East (incl. shipping in Hormuz and the Red Sea), Sudan and the Horn, eastern DRC and the Sahel, Indo-Pacific, Latin America; diplomacy, legal steps, arms transfers, US carrier movements.
