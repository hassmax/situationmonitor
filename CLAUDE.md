# Global Situation Monitor: notes for Claude Code

A self-updating 3D globe of armed-conflict events, built from public OSINT posts and news feeds, running entirely on free services. The owner is a law student focused on national security, not a developer: explain changes in plain language in PR descriptions, and keep the tool simple, fast, and honest about uncertainty.

## How it runs

- `.github/workflows/update.yml` runs every 15 minutes (plus on pushes to `main` that touch `site/`, `pipeline/`, or the workflow, and manually with an optional `lookback_days` backfill input).
- It restores the whole orphan `data` branch (`state.json`, `events.json`, `archive/`), stopping if the branch exists but cannot be fetched (saving without it would wipe the archive), runs `pipeline/run.py`, force-pushes everything back to `data` as one commit, copies `site/` plus the generated `out/events.json` into `_site/`, stamps `__BUILD__` in `index.html` with the commit SHA (cache-busting), and deploys to GitHub Pages.
- Secrets: `GEMINI_API_KEY` (free Gemini API, OpenAI-compatible endpoint), `TG_API_ID`, `TG_API_HASH`, `TG_SESSION` (Telegram via Telethon). Optional variable `NOMINATIM_EMAIL`. Optional Telegram alerts: secrets `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, variable `DASHBOARD_URL`. Never print, log, or commit secret values.
- Free-tier limits matter: Gemini Flash-Lite allows roughly 500 requests/day (the pipeline budgets 400, batching ~25 posts per request); Actions minutes are free because the repo is public. All model use shares one daily counter (`extract.calls_remaining`): extraction keeps a reserve of `extraction_reserve` (30) calls, and the brief is skipped below `brief_min_calls` (5). New features must not add model calls without budgeting them here.

## Layout

- `pipeline/run.py`: orchestrator. Fetch → keyword prefilter → model extraction (batched, budgeted, queued) → geocode (OSM Nominatim, cached) → merge → confidence scoring → write `events.json`.
- `pipeline/sources/`: `bluesky.py` (public API, no login), `telegram.py`, `rss.py` (incl. Google News searches), `gdelt.py` (used only to corroborate, not drawn).
- `pipeline/extract.py`: the model prompt (`SYSTEM_PROMPT`), event types, keyword prefilter (`CONFLICT_RE`), model probing and batching.
- `pipeline/geo.py`: geocoding, theater assignment, named-sea fallback coordinates.
- `pipeline/recency.py`: drops old stories that arrive with a fresh date. News-only events with 2+ different current headlines on the topic always stay; only without current coverage does the model compare against coverage 2+ weeks older, and it must name the matching older headline. Bump `CHECK_VERSION` when changing the rule (drops are put back and everything is re-checked).
- `pipeline/brief.py`: the "What changed in the last 6 hours" brief (published as `brief` in events.json). One `extract.ask_json` call at most hourly, only when the last 6 hours of events changed, from the dashboard's own events only; bullets citing unknown event ids, or citing uncorroborated events without attribution wording, are dropped; on failure the previous brief stays with its timestamp.
- `pipeline/alerts.py`, `pipeline/config/alerts.yaml`: Telegram alerts (Bot API sendMessage, plain text). Dedupe by (event id, rule) in `state["alerts"]`; first run records everything as seen and sends only "Alerts are on"; at most `max_messages_per_run` messages, else one digest; the bot token must never be logged.
- `pipeline/archive.py`: permanent history on the data branch, `archive/<day>.json` (published events by UTC day of `time`) and `archive/fleet/<day>.json`. Day files are merged, not rebuilt (events aged out of the working set stay; taken-down ids are removed) and written only when their contents change. Keep archive files forever.
- `pipeline/hunter.py`: corroboration hunter. Up to 8 severity-2/3 single-source or one-sided events from the last 12 h get a Google News search (place + type keywords, last day), at most twice per event and 3 h apart (`state["hunter"]`). Results enter the extraction queue at weight 4 as "Google News (corroboration search)" in the `google-news` group. It must make no model calls.
- `pipeline/corrections.py`, `pipeline/config/corrections.yaml`: hand corrections by event id, each with a required `note`: `hide`, `edit` (summary, place, lat, lon, type, severity), `drop_report` (by URL). Hides and edits are applied to the published copy (reversible, labeled "Corrected"); dropped reports are removed before scoring; reports of hidden events and dropped URLs are filtered out of candidates so nothing is re-created. Event ids are shown at the bottom of the detail view.
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
- **Time.** Events are timed by when they happened (`time`, from the model's `happened` field, else the post time). `updated` is only the latest report. The merge window is measured from `time` and must never slide forward with later reports (that bug made old events reappear as new). The front end uses `time` for the window, sorting, and "new". No time may be later than the moment the post was read (`common.make_item` clamps it; `run.py` repairs stored events), because some feeds give date-only stamps set to a UTC midnight that hasn't happened yet.
- **Positions are never estimated.** Carriers show their last reported position with its date; supply routes only use named endpoints (country-level routes are drawn faint); launch lines from assumed launch areas are drawn faint.
- **Map language.** Icon and color show what happened; marker style shows confidence (solid / outline / dashed). Zoomed out, 3+ nearby events collapse into the lead event's icon with a count; closer in, overlapping markers fan out. Only recent events animate (at most `MAX_ANIMATED` at once). Respect `prefers-reduced-motion`. Warnings that drones or missiles are in flight ("heading toward X", nothing hit) are grouped into one siren marker per country per day; they never become places hit in an attack wave.
- **Performance.** Keep it light: no frameworks, no build step, no new heavy layers. Cap marker and line counts as the code already does.
- UI copy: plain, sentence case, no jargon. Update `README.md` when behavior changes.

## Owner preferences

- Default time window: 24 hours, opening on the busiest area of activity.
- Everything on one screen (no separate views).
- Coverage priorities: Russia–Ukraine, NATO flank and Russian hybrid attacks, Middle East (incl. shipping in Hormuz and the Red Sea), Sudan and the Horn, eastern DRC and the Sahel, Indo-Pacific, Latin America; diplomacy, legal steps, arms transfers, US carrier movements.
