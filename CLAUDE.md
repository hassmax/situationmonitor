# Global Situation Monitor: notes for Claude Code

A self-updating 3D globe of armed-conflict events, built from public OSINT posts and news feeds, running entirely on free services. The owner is a law student focused on national security, not a developer: explain changes in plain language in PR descriptions, and keep the tool simple, fast, and honest about uncertainty.

This file is the short version. **`docs/NOTES.md` holds the full history** (why each rule and setting exists, the incidents behind them, trial results): read its entry for a module before changing that module, and add new history there, not here. Keep this file short; it is loaded in every session.

## How it runs

- `.github/workflows/update.yml` runs every 15 minutes (schedule at :04, :19, :34, :49; on pushes to `main` touching `site/`, `pipeline/` or the workflow; manually with an optional `lookback_days` backfill). Job limit 14 minutes; concurrency group `update`, not cancelling. `.github/workflows/heartbeat.yml` keeps a long job that dispatches an update when the last began 14+ minutes ago (GitHub drops scheduled runs) and cancels an update stuck "waiting" over 15 minutes.
- Each run restores the orphan `data` branch (`state.json`, `events.json`, `archive/`, `embeddings.npz`), stopping if it exists but can't be fetched; runs `pipeline/run.py`; force-pushes everything back to `data` as one commit; copies `site/` plus `out/events.json` and `out/reports/` into `_site/`, stamps `__BUILD__` in `index.html` with the commit SHA (cache-busting; an open page with another build reloads, `checkBuild`), and deploys to GitHub Pages.
- Secrets: `GEMINI_API_KEY`, `TG_API_ID`, `TG_API_HASH`, `TG_SESSION`; optional `FIRMS_MAP_KEY`, `CEREBRAS_API_KEY`, `OPENROUTER_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`; variables `NOMINATIM_EMAIL`, `DASHBOARD_URL`. **Never print, log, or commit secret values.**
- Reachability: Hugging Face, Wikipedia, ISW's site and some outlets refuse the Claude sandbox but work from GitHub's servers; trial such code on Actions (a temporary workflow on the branch, removed before merging). `gh api` log downloads are refused here; use the GitHub MCP `get_job_logs`.

## Model budget (must not be broken)

- Gemini Flash-Lite free tier: the pipeline budgets 470 calls a day (`daily_llm_calls`), batching up to 40 posts a call. All model use shares one counter (`extract.calls_remaining`); 5xx, 402 and 429 answers and calls that never arrive aren't counted. Busy models fall back once (`llm_models`, then `llm_fallback_models`).
- Every call has a purpose (`ask_json(..., purpose=)`, tallied in `state["llm_calls"]["by"]`). Checks after extraction have paced daily shares (`extract.SHARES`, `SHARE_DEFAULTS`): gate 120, dedupe 100, recency 24, maproom 16 (unpaced), frontline 40, frontline_review 60, frontline_isw 60 (unpaced), analysis 24, frontline_social 40, frontline_imagery 8, frontline_news 24, incident_grouping 30, incident_repair 12. Extraction keeps a reserve of 30.
- Free outside providers (`providers.py`): Cerebras (900K tokens/day cap, 27K/minute) and OpenRouter. Routed purposes (`provider_routes` in sources.yaml, else `providers.ROUTES`) go there first; the route shares add up to the 900K cap, and extract's unused share is held back for overflow extraction. `extract.GEMINI_FIRST` purposes (gate) ask Gemini first. Callers gate on `extract.room(purpose)`.
- **New features must not add model calls without budgeting them**: a purpose, and a share if they can run every 15 minutes, in both places above and in sources.yaml's `provider_routes` if routed.

## Layout

`pipeline/run.py` orchestrates: fetch → keyword prefilter → model extraction (batched, budgeted, queued) → geocode → merge → date check → gatekeeper → old-story check → same-story check → incident checks → confidence → publish (`events.json` plus 64 `reports/NN.json` files by event-id hash) → front lines, analyst, Telegram alerts, audit, archive. Modules (details in docs/NOTES.md):

- `sources/`: `bluesky.py` (accounts plus keyword search patrol), `telegram.py`, `rss.py` (incl. Google News searches and per-outlet crediting), `gdelt.py` (corroboration only), `maproom.py` (ISW's map images, read by the model; positions only from place-name lookups).
- `extract.py`: the prompt (`SYSTEM_PROMPT`), event types, keyword prefilter (`CONFLICT_RE`; new words go in a new `_ADDED_WORDS` version), batching, model probing, `ask_json`.
- `geo.py`: geocoding (Nominatim, cached), theaters, sea and region anchors, US command regions (`pin_commands`), sanity checks against the theater and country.
- `merge.py`: joining reports into events, attack waves, alert groups, transfers and routes, confidence (`apply_status`). `consolidate` folds stored events the rules now join.
- `datecheck.py` (article dates, no model), `recency.py` (old stories with fresh dates, `possibly_old` flag), `gate.py` + `embed.py` (the gatekeeper: every new event checked for duplicates and old news in the run that creates it; unfinished ones published `unchecked`, no banner or Telegram alert), `dedupe.py` (same-story check for older events; `different_times` keeps attacks days apart separate unless the later report is findings or reactions), `incidents.py`.
- `analyst.py` (regional judgments, confidence computed from cited events, never the model), `alerts.py` (Telegram), `archive.py` (permanent day files; keep forever), `hunter.py` (corroboration searches, no model), `corrections.py` + `config/corrections.yaml` (hand hides, edits, dropped reports, each with a note), `audit.py` (mistake checks every run; add a check for each new kind of mistake).
- `fleet.py`: US carriers from USNI's Fleet Tracker (parsed without the model) and news; implausible moves held until confirmed; stale editions never read.
- `flights.py`: adsb.lol military flights; not drawn on the map; movements feed the analyst; C-17/KC-135 surges become air routes and Telegram alerts.
- `frontline/` + `config/frontlines.yaml`: this site's own settlement-level control assessment (scout, standing, isw, heat, social, claims, assess, review, wikipedia fill-in, cartographer); imagery agent off. `control.py`: published control layers, **none active** (ISW and Critical Threats licences forbid reuse; don't add a source without checking its licence).
- `config/theaters.yaml`, `config/sources.yaml`: if a theater is added or renamed, update the list in `SYSTEM_PROMPT` too.
- `site/`: `index.html`, `styles.css`, `command.css` (briefing look, loaded last), `app.js` (vanilla JS, no build step), `vendor/globe.gl.min.js` (2.46.2), `assets/countries-110m.json`, `data/demo-events.json` (`?demo`).

## Testing before you push

- Python: `pip install -r pipeline/requirements.txt`, `python -m pyflakes pipeline`, `python -m pytest -q`, and `python pipeline/run.py --state /tmp/state --out /tmp/out --no-llm` (network; no model).
- Front end: `node tests/*.cjs` (incl. `theater-focus`, `briefing-alerts`, `intel-brief`) and `node --check site/app.js`; `python -m http.server --directory site 8000`, open `?demo`, check the console at desktop and ~390 px. Never commit `site/data/events.json` or `site/data/reports/`.
- If startup breaks, the page shows the error in the feed panel (`fatal()`); keep that.
- Read large logs filtered (grep), not whole.

## Rules that must not be broken

- **Honesty over drama.** Summaries are the model's own words; only summaries and links are published, never full post text. Never add or imply attribution a source doesn't make. The legal layer records stated legal bases, never assesses them.
- **Confidence** (`merge.apply_status`): corroborated = 2+ independent source groups with at least one unaligned (or opposing sides agree); unconfirmed = one unaligned source; claimed = only one side's sources. Same `side` never corroborates. Weak groups (`google-news`, `bluesky-search`) count only when nothing else reported the event, together once. GDELT counts as one unaligned source. Listed outlets (`outlets:` in sources.yaml) count on their own; unlisted Google News outlets share `google-news`.
- **Time.** `time` is when it happened (the model's `happened`, else the post time); `updated` is only the latest report. Merge windows are measured from `time` and never slide with later reports. The map, list, counts and "new" use `time`; later coverage can't revive an event outside the window (shown as Updated). Distinct incidents stay separate; nearby places, parties or report times alone never join them. No time later than when the post was read.
- **Positions are never estimated.** Carriers: last reported position with its date. Routes: named endpoints only (country-level or command-region ends drawn faint). Events at a US combatant command go to that command's region, labelled and approximate. A country's forces going into, visiting or exercising in another country are a route of its own forces; purchases from its own industry are `production`; a deal with nothing delivered is diplomacy. Launch lines come only from assumed launch areas (`ANCHORS` in app.js), drawn faint; a whole country is not a launch site; big barrages fan out from several known areas.
- **Map language.** Control shading is a source's published shapes or this site's own assessment (one broad shape per side, flowing dashed edge, only corroborated control, no lone specks); the Wikipedia fill-in only where the agents have nothing, credited in Sources. Internationally named occupations with a published outline (West Bank with East Jerusalem, Golan Heights) are shaded whole, "Israeli-occupied"; never a hand-drawn line. Icon and colour show what happened; marker style shows confidence (solid / outline / dashed). Siren warnings group into one marker per country per day and never become places hit.
- **Look and motion.** The briefing look (`command.css`, Public Sans and IBM Plex Mono, deep red for alerts and new, white for selected) and the window layout stay as they are unless the owner asks. Map icons don't move (no pop, sway or glass). Everything animated stops under `prefers-reduced-motion`. Breaking alerts only for checked severity 2+ events, at most 3 per update, one per incident nearby.
- **Performance.** No frameworks, no build step, no heavy layers. Keep the existing caps (markers, animations; lighter on phones, `PHONE`). Land is painted onto the globe surface. Clicks render in stages (`renderSoon`).
- The header reads "Global Situation Monitor" with the byline "By Huzaifa Khan".
- UI copy: plain, sentence case, no jargon. Carrier names start with "U.S.S.". Update `README.md` when behaviour changes.

## Owner preferences

- Default window 24 hours, opening on the busiest area. Everything on one screen.
- Coverage priorities: Russia–Ukraine, NATO flank and Russian hybrid attacks, Middle East (incl. Hormuz and Red Sea shipping), Sudan and the Horn, eastern DRC and the Sahel, Indo-Pacific, Latin America; diplomacy, legal steps, arms transfers, US carriers; worldwide treaty and membership changes, expulsions, sanctions packages.
- Regional selection is exclusive (`S.region`: `world`, one theater, or null for Hide all); a theater focuses its camera and keeps only its events, routes and brief; Escape leaves it; preserved across live refresh. Clicking a country opens its intelligence brief in place of the list; clicking away closes it.
- World clocks: UTC, Washington, London, Moscow, Tehran, Beijing.
- Ask before merging unless told to merge.
