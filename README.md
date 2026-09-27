# Global Situation Monitor

A self-updating 3D globe of armed-conflict events, built from public OSINT posts and news feeds. It runs entirely on free services: GitHub Actions fetches and processes new reports every 15 minutes, Google's free Gemini API turns posts into structured events, and GitHub Pages hosts the site.

Coverage: Russia–Ukraine, NATO's eastern flank and Russian-linked hybrid attacks in Europe (sabotage, cable cuts, GPS jamming, drone and airspace incursions), the Middle East (Iran, Israel, Gaza, Lebanon, Yemen, Red Sea, Hormuz), Sudan and the Horn of Africa (including Ethiopia–Tigray), eastern DRC and the Sahel, the Indo-Pacific, and Latin America and the Caribbean (US operations around Venezuela and Cuba, boat strikes, and armed-group conflict in Colombia, Ecuador, Mexico, and Haiti). Major diplomatic developments about these conflicts (ceasefires, peace talks, agreements, alliance and defense-pact meetings, Security Council action) are tracked too, and appear as flat discs you can hide with the Diplomacy layer.

Preview the layout with sample data before any real data exists: open `site/index.html?demo` through a local server (see "Run it locally") or `https://<you>.github.io/<repo>/?demo` once deployed.

## Setup (about 10 minutes)

1. **Create a public repository** on GitHub and add these files to it (drag the folder into "uploading an existing file", or `git push`). It must be public: public repos get unlimited Actions minutes and free Pages. The data it publishes is public anyway.
2. **Turn on Pages.** Settings → Pages → Build and deployment → Source: **GitHub Actions**.
3. **Add a free Gemini API key.** Go to <https://aistudio.google.com>, sign in with a Google account, and click **Get API key → Create API key**. In the repo: Settings → Secrets and variables → Actions → New repository secret, named `GEMINI_API_KEY`. No credit card is needed. Google may use free-tier inputs to improve its models; everything the pipeline sends is already-public posts.
4. **Add Telegram secrets** (optional, but most frontline reporting starts there). See the next section.
5. **Optional:** Settings → Secrets and variables → Actions → Variables → add `NOMINATIM_EMAIL` with your email. OpenStreetMap's geocoder asks heavy users to identify themselves; with one it can contact you instead of blocking you.
6. **Run it.** Actions tab → "Update conflict data" → Run workflow. The first run takes 3–5 minutes. Your globe is at `https://<your-username>.github.io/<repo-name>/`. After that it updates itself every 15 minutes.

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
  Telegram channels ──┼─> keyword prefilter ─> Gemini API (batched) ────> geocode (OSM) ─┐
  News RSS feeds ─────┘                                                                   ├─> merge + score ─> events.json ─> GitHub Pages
  GDELT 2.0 event files ──────────────────────> news-intensity cells ─────────────────────┘
state (seen posts, queue, caches, events) is kept on the `data` branch, overwritten each run
```

- **Extraction.** Posts are sent to the model in batches of about 25. It decides whether each describes a concrete, recent military event, then returns a type, a 25-word summary in its own words (translated when needed), and a place. Only these summaries and links to the originals are published, never the full posts.
- **Budget.** Gemini's free Flash-Lite quota is currently about 500 requests a day (Google adjusts it; AI Studio shows your live limit). The pipeline spreads 400 across the day and queues the rest, so bursts show up with a delay rather than getting dropped. Candidate model names are tried in order and the first that answers is used, so a renamed model doesn't break the pipeline.
- **Geocoding.** The model's place name is checked against OpenStreetMap. If the two disagree by more than 300 km, the model's estimate is kept and the event is marked approximate.
- **Merging.** Reports of the same kind of event within 30 km and 12 hours of each other are merged into one event with several sources (wider radius for naval and deployment reports).

### What the colors mean

| On the globe | Meaning |
|---|---|
| Red-orange, filled | **Corroborated.** Two or more independent sources, at least one not aligned with either side (or opposing sides agree). Nearby news coverage from 3+ outlets in GDELT counts as one independent source. |
| Amber, ring | **Single source.** One unaligned source so far. |
| Violet, diamond | **One side's claim.** Only sources aligned with one party, such as a defense ministry and friendly bloggers. |

Taller markers are more severe. Pulsing rings mark events from the last 3 hours. Countries with events in view, and the countries attacking them, get warm borders and brighter land.

**Attack waves.** Missile, drone, and interception reports with a known attacker are grouped into one event per direction per day (Russia → Ukraine, Ukraine → Russia, Iran → Israel, and so on; days run 09:00–09:00 UTC so an overnight attack stays together). A wave lists every location hit, launch and intercept totals when a source gives them, and the launch areas named. Dashed arcs run from launch areas to targets. When no report names a launch area, the arc starts from the nearest known one and is drawn faint.

**Carrier strike groups.** Every US aircraft carrier's last reported position, from USNI News' weekly Fleet and Marine Tracker (read automatically each Monday edition) plus departure and arrival reports in between. When a carrier moves, its icon sails from the old position to the new one when you open the page; a dotted wake shows where it came from and a faint line shows a stated destination. Positions are never estimated between reports; each shows its "as of" date.

**Arms transfers and air bridges.** Deliveries of weapons from one country to another, drawn as fast-moving dotted streams from origin to destination. Repeated flights or sailings on the same route within three days merge into one "air bridge" or "sea bridge."

**Legal steps.** Article 51 letters, War Powers reports, Security Council resolutions, and ICJ/ICC actions appear as flat discs (toggle: Legal steps). Any event whose sources state the legal basis the acting state gives for using force shows it in a highlighted box. The dashboard records claimed justifications; it does not assess them.

**About GDELT.** GDELT is a free database that reads news sites worldwide and logs each report of violence with a location, every 15 minutes. It is not drawn on the map. It is used for one thing: when three or more separate outlets report violence near a place where only one source has posted, that report is upgraded to corroborated. Set `gdelt: false` in `sources.yaml` to turn it off.

**Using it.** Click a carrier icon or a name in the Carrier strike groups list to see its status, recent positions, and events within 600 km. Click anywhere near a marker to open it; if several are close together, the globe zooms in and lists them. **Key developments** (severe and corroborated) are pinned at the top of the feed. The small bar charts next to each theater show events per day over the past week. Press **H** to hide the panels, **/** to search, **Esc** to go back. On phones, drag or tap the bar at the top of the event list to collapse or expand it.

## Customizing

- **Sources:** `pipeline/config/sources.yaml`. Each source has a `kind` and optionally a `side`; that is what drives the confidence colors, so label partisan and official channels honestly. The dashboard's Sources panel shows which ones are failing. The starter lists are thinnest for the Middle East, Africa, and the Indo-Pacific.
- **Theaters:** `pipeline/config/theaters.yaml` (countries, map boxes, camera positions). If you add or rename a theater, update the theater list in the prompt in `pipeline/extract.py` too.
- **Model, provider, and budget:** the `settings` block at the top of `sources.yaml`. Any OpenAI-compatible provider works (Groq, OpenRouter, Mistral): change `llm_url` and `llm_models` and put that provider's key in the `GEMINI_API_KEY` secret.
- **Look:** `site/styles.css` and `site/app.js`. Pushing changes to `site/` redeploys immediately.

## Run it locally

```bash
pip install -r pipeline/requirements.txt
export LLM_API_KEY=<your Gemini API key>   # optional; add --no-llm to skip
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
| Run log says `LLM_API_KEY is not set` | Add the `GEMINI_API_KEY` repository secret. |
| Run log says `the API key was rejected` | The key is wrong or was deleted in AI Studio. Create a new one and update the secret. |
| Run log shows `rate limited` | The day's free quota is used up (it resets at midnight Pacific). Events queue and resume; lower `daily_llm_calls` if it happens daily. |
| Run log says `no model returned a usable answer` | Google renamed its models. Add a current Flash-Lite model name from AI Studio to the front of `llm_models` in `sources.yaml`. |
| Telegram sources show "session is not authorised" | The session was revoked. Run `make_telegram_session.py` again and replace `TG_SESSION`. |
| Telegram sources show "TG_SESSION looks wrong" | Something other than the session line got pasted. Copy only the single line starting with `1`. |
| A source shows an error in the Sources panel | The feed moved or the handle changed. Fix or set `disabled: true` in `sources.yaml`. |
