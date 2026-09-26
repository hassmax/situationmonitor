# Conflict Globe

A self-updating 3D globe of armed-conflict events, built from public OSINT posts and news feeds. It runs entirely on free GitHub services: Actions fetches and processes new reports every 15 minutes, GitHub Models (free tier) turns posts into structured events, and GitHub Pages hosts the site.

Coverage: Russia–Ukraine, the Middle East (Iran, Israel, Gaza, Lebanon, Yemen, Red Sea, Hormuz), Sudan and the Horn of Africa (including Ethiopia–Tigray), eastern DRC and the Sahel, the Indo-Pacific, and any US or Chinese military activity elsewhere.

Preview the layout with sample data before any real data exists: open `site/index.html?demo` through a local server (see "Run it locally") or `https://<you>.github.io/<repo>/?demo` once deployed.

## Setup (about 10 minutes)

1. **Create a public repository** on GitHub and add these files to it (drag the folder into "uploading an existing file", or `git push`). It must be public: public repos get unlimited Actions minutes and free Pages. The data it publishes is public anyway.
2. **Turn on Pages.** Settings → Pages → Build and deployment → Source: **GitHub Actions**.
3. **Add Telegram secrets** (optional, but most frontline reporting starts there). See the next section.
4. **Optional:** Settings → Secrets and variables → Actions → Variables → add `NOMINATIM_EMAIL` with your email. OpenStreetMap's geocoder asks heavy users to identify themselves; with one it can contact you instead of blocking you.
5. **Run it.** Actions tab → "Update conflict data" → Run workflow. The first run takes 3–5 minutes. Your globe is at `https://<your-username>.github.io/<repo-name>/`. After that it updates itself every 15 minutes.

If the first automatic run (triggered by your upload) failed at the deploy step, that is because Pages wasn't enabled yet. Just run it again.

### Telegram

The pipeline reads public channels using your own Telegram account.

1. Go to <https://my.telegram.org> → API development tools → create an app (any name). Note the **api_id** and **api_hash**.
2. On your computer: `pip install telethon`, then `python pipeline/make_telegram_session.py`. Enter the api_id, api_hash, your phone number, and the login code Telegram sends. It prints a long session string.
3. In the repo: Settings → Secrets and variables → Actions → New repository secret. Add `TG_API_ID`, `TG_API_HASH`, and `TG_SESSION` (the long string).

The session string works like a password to your Telegram account. It is stored as an encrypted GitHub secret and never written to the site or the data branch, but consider using a secondary Telegram account. You will see an active session in Telegram's settings that connects from GitHub's servers. Revoking it there (or deleting the secret) switches Telegram off; the rest keeps working.

## How it works

```
every 15 min (GitHub Actions)
  Bluesky public API ─┐
  Telegram channels ──┼─> keyword prefilter ─> GitHub Models (batched) ─> geocode (OSM) ─┐
  News RSS feeds ─────┘                                                                   ├─> merge + score ─> events.json ─> GitHub Pages
  GDELT 2.0 event files ──────────────────────> news-intensity cells ─────────────────────┘
state (seen posts, queue, caches, events) is kept on the `data` branch, overwritten each run
```

- **Extraction.** Posts are sent to the model in batches of about 18. It decides whether each describes a concrete, recent military event, then returns a type, a 25-word summary in its own words (translated when needed), a place, and flags for US or Chinese military involvement. Only these summaries and links to the originals are published, never the full posts.
- **Budget.** The free tier allows about 150 model requests a day. The pipeline spreads 140 across the day and queues the rest, so bursts show up with a delay rather than getting dropped.
- **Geocoding.** The model's place name is checked against OpenStreetMap. If the two disagree by more than 300 km, the model's estimate is kept and the event is marked approximate.
- **Merging.** Reports of the same kind of event within 30 km and 12 hours of each other are merged into one event with several sources (wider radius for naval and deployment reports).

### What the colors mean

| On the globe | Meaning |
|---|---|
| Red-orange, filled | **Corroborated.** Two or more independent sources, at least one not aligned with either side (or opposing sides agree). Nearby news coverage from 3+ outlets in GDELT counts as one independent source. |
| Amber, ring | **Single source.** One unaligned source so far. |
| Violet, diamond | **One side's claim.** Only sources aligned with one party, such as a defense ministry and friendly bloggers. |
| Teal hexagons | **News intensity.** Where GDELT's machine-coded news feed is reporting violence, weighted by the number of distinct outlets. |

Taller markers are more severe. Pulsing rings mark events from the last 3 hours. Dashed arcs are launch paths when a report names where a strike came from.

## Customizing

- **Sources:** `pipeline/config/sources.yaml`. Each source has a `kind` and optionally a `side`; that is what drives the confidence colors, so label partisan and official channels honestly. The dashboard's Sources panel shows which ones are failing. The starter lists are thinnest for the Middle East, Africa, and the Indo-Pacific.
- **Theaters:** `pipeline/config/theaters.yaml` (countries, map boxes, camera positions). If you add or rename a theater, update the theater list in the prompt in `pipeline/extract.py` too.
- **Model and budget:** the `settings` block at the top of `sources.yaml`.
- **Look:** `site/styles.css` and `site/app.js`. Pushing changes to `site/` redeploys immediately.

## Run it locally

```bash
pip install -r pipeline/requirements.txt
export MODELS_TOKEN=<a GitHub token with the "Models: read" permission>   # optional; add --no-llm to skip
python pipeline/run.py --state state --out out
mkdir -p site/data && cp out/events.json site/data/events.json
python -m http.server --directory site 8000   # then open http://localhost:8000 (or /?demo)
```

Don't commit `site/data/events.json`; the workflow generates it on every run.

## Limits worth knowing

- **Lag.** Expect 15–30 minutes from a post to the map. GitHub starts scheduled runs late when its servers are busy, and queued posts wait for model budget.
- **Scheduled runs pause after 60 days without repository activity.** GitHub emails you first. Re-enable the workflow from the Actions tab.
- **The model makes mistakes.** It can misplace a village, misread sarcasm, or merge two nearby incidents. Every event links to its sources; check them before relying on one.
- **Telegram and partisan sources are included on purpose,** because that is where fighting is reported first. They are labeled and never corroborate each other.
- **GDELT is noisy.** It machine-codes the world's news, so it is used only as a heat layer and as supporting evidence, never for its own markers.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Site shows "No data yet" | Check the latest run in the Actions tab. The first run must finish before data appears. |
| Deploy step fails with a Pages error | Settings → Pages → Source must be "GitHub Actions". Then re-run. |
| Run log shows `HTTP 403` from the model | GitHub Models may be switched off for the organization that owns the repository. A repository under your personal account usually works. |
| Run log shows `rate limited` | The daily free quota is used up. Events queue and resume the next day; lower `daily_llm_calls` if it happens daily. |
| Telegram sources show "session is not authorised" | The session was revoked. Run `make_telegram_session.py` again and replace `TG_SESSION`. |
| A source shows an error in the Sources panel | The feed moved or the handle changed. Fix or set `disabled: true` in `sources.yaml`. |
