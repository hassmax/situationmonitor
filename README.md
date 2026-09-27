# Global Situation Monitor

A self-updating 3D globe of armed-conflict events, built from public OSINT posts and news feeds. It runs entirely on free services: GitHub Actions fetches and processes new reports every 15 minutes, Google's free Gemini API turns posts into structured events, and GitHub Pages hosts the site.

Coverage: Russia–Ukraine, NATO's eastern flank and Russian-linked hybrid attacks in Europe (sabotage, cable cuts, GPS jamming, drone and airspace incursions), the Middle East (Iran, Israel, Gaza, Lebanon, Yemen, Red Sea, Hormuz), Sudan and the Horn of Africa (including Ethiopia–Tigray), eastern DRC and the Sahel, the Indo-Pacific, and Latin America and the Caribbean (US operations around Venezuela and Cuba, boat strikes, and armed-group conflict in Colombia, Ecuador, Mexico, and Haiti). Major diplomatic developments about these conflicts (ceasefires, peace talks, agreements, alliance and defense-pact meetings, Security Council action, and visits or meetings between leaders, such as a prime minister's trip to a regional capital) are tracked too, and appear as flat discs you can hide with the Diplomacy layer.

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
- **Merging.** Reports of the same kind of event within 30 km and 12 hours of each other are merged into one event with several sources (wider radius for naval and deployment reports). Diplomatic and legal steps merge by who takes part, not just by place: the model lists the countries or bodies involved, and reports merge only when they share two of them (or the same one with matching wording), so a leader's visit to Abu Dhabi and an Iranian proposal on Hormuz the same day stay separate events.

### Reading the map

Everything is on one globe. The legend at the top of the left panel explains every symbol.

- **Icon and color show what happened.** Red for strikes (drone or missile, airstrike, air defense, explosion), amber for fighting on the ground (shelling, ground fighting, territory changing hands), blue for naval, violet for hybrid attacks and incursions, white for diplomacy and legal steps, slate for deployments, teal for arms transfers, pale blue chips for US aircraft carriers.
- **The marker's style shows confidence.** Solid = corroborated by two or more independent sources (or by opposing sides). Outline = a single unaligned source. Dashed outline = only sources aligned with one side. Nearby news coverage from 3+ outlets (via GDELT) counts as one independent source. Google News results count per outlet: each outlet on the list in `sources.yaml` (Reuters, AP, the BBC, Axios, Haaretz, and so on) is its own source, and state or partisan media (TASS, Press TV, Xinhua, Anadolu, and others) are marked with the side they are aligned with. Results from outlets not on the list count together as one source, and only when no listed outlet has the story, so dozens of sites republishing one wire report never look like corroboration.
- **Size shows severity**, and older events fade in longer time windows.
- **Recent events (last 6 hours, or new since your last visit) are animated by type:** impact bursts for drone and missile strikes, a diving chevron for airstrikes, rapid double flashes for shelling, a clash flicker for ground fighting, ripples at sea. To keep the page smooth, only the 20 newest animate at once.
- **Moving lines:** red dashed lines run from launch areas to targets (faint when the launch area wasn't named and the line starts from the nearest known one); teal lines are 30-day supply routes, thicker with more deliveries, solid when corroborated, with moving particles when a delivery arrived in the last 72 hours; pale lines are carrier tracks and stated destinations.
- **Numbers on the whole-globe view.** Where three or more events sit close together, they collapse into the icon of the most important one with a count; click it to zoom in. Closer in, overlapping markers fan out around their shared spot, with a thin line back to each true location.
- **Opening view.** The dashboard opens on the last 24 hours (3 days if the last 24 were quiet) and flies to the busiest, most serious area of activity, with a short note naming it.
- **Times are when events happened,** not when the latest article about them appeared. The model reads the event date from each report and drops articles that only recap older news, so a two-day-old Security Council vote stays two days old. News feeds, Google News above all, sometimes list an old article with a fresh date (a site republished or updated it). So every event built only from news feeds is checked. First the pipeline looks for current coverage: if two or more different headlines on the same topic appeared around the event's own date, the event is current and stays, however often that kind of thing happens (another strike after another drone attack, another coast guard drill). Only when there is no current coverage but there is coverage from at least two weeks earlier does the model compare the event with those dated headlines; the event is dropped only when the model points to an older headline about the same specific incident. Each drop is written to the run log with the matching headline, and the dropped event is kept so it can be put back if the rule changes. Later reports still attach to the original event; the detail view shows when the latest one arrived.

**Attack waves.** Missile, drone, and interception reports with a known attacker are grouped into one event per direction per day (Russia → Ukraine, Ukraine → Russia, Iran → Israel, and so on; days run 09:00–09:00 UTC so an overnight attack stays together). The wave's icon sits on the main target, the other locations hit are small red dots, and the panel lists every location, launch and intercept totals, and launch areas.

**Drone and missile alerts.** Air force channels post a running stream of warnings that a drone or missile is heading toward a place. Warnings with nothing reported hit are grouped into one siren marker per country per day (same 09:00 UTC days), labeled with the number of alerts. Click it to list the places named and see them as faint dots. Alerts never count as places hit in an attack wave, and they never add attribution the posts don't make. Over NATO's eastern flank, every airspace alert stays its own event.

**Aircraft carriers.** All 11 active US carriers are listed in the left panel. Positions come from USNI News' weekly Fleet and Marine Tracker (read from its own feed, so no edition is missed) and from departure and arrival reports in between. The globe shows carriers at sea, plus any that moved this week; the tracker lists every deployed carrier, so the rest are listed as in home waters. When a carrier moves, its icon sails from the old position to the new one. Positions are never estimated between reports. When Kennedy (CVN-79) commissions or a carrier changes home port, update `CARRIERS` and `HOME` in `pipeline/fleet.py`.

**Arms transfers.** Deliveries become supply routes on the globe and in the Supply routes list (left panel): one line per supplier and recipient over 30 days, through any named hubs (Ramstein, Rzeszów). When reports only name countries, the line runs between the two countries and is drawn faint. Pledged packages are listed but not drawn. Intercepted shipments are markers.

**Legal steps.** Article 51 letters, War Powers reports, Security Council resolutions, and ICJ/ICC actions appear with the scales icon. Any event whose sources state the legal basis the acting state gives for using force shows it in a highlighted box. The dashboard records claimed justifications; it does not assess them.

**About GDELT.** GDELT is a free database that reads news sites worldwide and logs each report of violence with a location, every 15 minutes. It is not drawn on the map. It is used for one thing: when three or more separate outlets report violence near a place where only one source has posted, that report is upgraded to corroborated. Set `gdelt: false` in `sources.yaml` to turn it off.

**Using it.** Click any marker, carrier, or route for details and sources. **Key developments** (severe and corroborated) are pinned at the top of the feed. The small bar charts next to each theater show events per day over the past week. Press **H** to hide the panels, **/** to search, **Esc** to go back. On phones, drag or tap the bar at the top of the event list to collapse or expand it.

## Customizing

- **Sources:** `pipeline/config/sources.yaml`. The `outlets` list there says how Google News results from each outlet count: tier 1 for established outlets (processed first and preferred for headline summaries), tier 2 for other known outlets, and a `side` for state or partisan media. Each source has a `kind` and optionally a `side`; that is what drives the confidence colors, so label partisan and official channels honestly. The dashboard's Sources panel shows which ones are failing. The starter lists are thinnest for the Middle East, Africa, and the Indo-Pacific.
- **Theaters:** `pipeline/config/theaters.yaml` (countries, map boxes, camera positions). If you add or rename a theater, update the theater list in the prompt in `pipeline/extract.py` too.
- **Removing a wrong event:** add its id to `pipeline/config/removed.yaml` with a short note on why. The id is the part after `#` in the page address when the event is open. The next update takes it off the map.
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

## Backfill

Runs only look at posts from the last 36 hours. To pull in something older (after adding a source, or when the dashboard was down), go to Actions → Update conflict data → Run workflow, and enter a number of days (up to 14) in "Backfill". Older posts are then worked through over the next few runs, within the free model quota, and appear at the time the events happened. Articles that only recap older news are still skipped.

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
