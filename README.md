# Global Situation Monitor

A self-updating 3D globe of armed-conflict events, built from public OSINT posts and news feeds. It runs entirely on free services: GitHub Actions fetches and processes new reports every 15 minutes, Google's free Gemini API turns posts into structured events, and GitHub Pages hosts the site.

Coverage: Russia–Ukraine, NATO's eastern flank and Russian-linked hybrid attacks in Europe (sabotage, cable cuts, GPS jamming, drone and airspace incursions), the Middle East (Iran, Israel, Gaza, Lebanon, Yemen, Red Sea, Hormuz), Sudan and the Horn of Africa (including Ethiopia–Tigray), eastern DRC and the Sahel, the Indo-Pacific, and Latin America and the Caribbean (US operations around Venezuela and Cuba, boat strikes, and armed-group conflict in Colombia, Ecuador, Mexico, and Haiti). Major diplomatic developments about these conflicts (ceasefires, peace talks, agreements, alliance and defense-pact meetings, Security Council action, and visits or meetings between leaders, such as a prime minister's trip to a regional capital) are tracked too, and appear as flat discs you can hide with the Diplomacy layer. So are formal changes in countries' international commitments and relations anywhere in the world: leaving, suspending or joining a treaty, alliance or international body (the US leaving the Council of Europe's anti-corruption body, GRECO), expelling diplomats, recalling ambassadors or cutting relations, and new sanctions packages against a state. Announcements that a step is being considered don't count. A step that bears on one of the conflicts above goes under that conflict; the rest go under "Treaties, sanctions and relations", placed at the capital of the country taking the step.

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

### Telegram alerts (optional)

The update job can message you on Telegram when something important happens. This uses a bot, which is separate from the Telegram account above.

1. In Telegram, open a chat with **@BotFather**, send `/newbot`, and follow the prompts (any name; the username must end in `bot`). BotFather replies with a **token** like `123456789:AAH...`. Keep it private.
2. Open a chat with your new bot and send it any message (for example "hi"). To get alerts in a group or channel instead, add the bot there (as an admin, for a channel) and post a message.
3. In a browser, open `https://api.telegram.org/bot<TOKEN>/getUpdates`, with your token in place of `<TOKEN>`. Find `"chat":{"id":` in the page: that number is your **chat id** (group and channel ids start with `-`).
4. In the repo: Settings → Secrets and variables → Actions → New repository secret. Add `TELEGRAM_BOT_TOKEN` (the token) and `TELEGRAM_CHAT_ID` (the chat id).
5. Optional: on the Variables tab of the same page, add `DASHBOARD_URL` if the dashboard is not at `https://<owner>.github.io/<repo>/`.

On the next run, the bot sends a single "Alerts are on" message. Everything already on the map counts as seen, so there is no backlog. After that, you get a message when a new event matches a rule in `pipeline/config/alerts.yaml`:

- a severity-3 event that is corroborated, in any theater;
- an attack wave with 100+ reported launched, or 8+ locations;
- a US aircraft carrier that departs, starts heading to a stated destination, or moves 500+ km;
- a new air or sea bridge: 3+ reported deliveries on one supplier-to-recipient route within 72 hours;
- any new legal step (Article 51 letter, War Powers report, Security Council resolution, ICJ or ICC action).
- an event reported both by Shin (`@shin_persian`) and by at least one other OSINT Telegram channel (any Telegram source with `kind: osint` in `sources.yaml`, such as War Monitors, `@warmonitors`).

Each message says what happened, where, the confidence label and number of sources, and links to the event on the dashboard. You never get the same alert twice; an event alerts again only if it later matches a rule it did not match before (a wave that grows past 100 launched, say). If more than 8 alerts come due at once, you get one digest instead. To change a threshold, limit alerts to some theaters, or set quiet hours, edit `pipeline/config/alerts.yaml`; every setting there has a comment. For example, to hear only about the Middle East and Ukraine, and nothing between 22:00 and 07:00 UTC:

```yaml
theaters: [mideast, ukraine]
quiet_hours: {start: 22, end: 7}
```

To switch alerts off, delete either secret.

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
- **Region names.** Region names such as "Middle East" or "Europe" are never looked up on the map (the lookup once put "Middle East" in a Baltimore neighborhood of that name); such events sit at a marked point for the region and are shown as approximate. A strike, battle or incident at sea whose looked-up place lands far outside its theater is not placed there.
- **Geocoding.** The model's place name is checked against OpenStreetMap. If the two disagree by more than 300 km, the model's estimate is kept and the event is marked approximate.
- **Merging.** Reports of the same kind of event within 30 km and 12 hours of each other are merged into one event with several sources (wider radius for naval and deployment reports). Diplomatic and legal steps merge by who takes part, not just by place: the model lists the countries or bodies involved, and reports merge only when they share two of them (or the same one with matching wording), so a leader's visit to Abu Dhabi and an Iranian proposal on Hormuz the same day stay separate events. Which map section (theater) a diplomatic or legal report is filed under, and whether it is called diplomacy or a legal step, doesn't keep it apart: a German minister's visit to the ICC was filed once under the NATO flank and once under Ukraine. A meeting or statement reported from different cities (the venue, the capital that spoke) merges when the same parties are named and the wording closely matches. Reports of one country's forces preparing around another ("the US military is laying groundwork for action around Cuba") merge when they name the same two countries and use similar wording, even if one report is pinned to Havana and another to the middle of Cuba. More generally, for deployments, hybrid attacks, naval incidents, and incursions, a report pinned only to a whole country, region, or sea ("England", "South China Sea") joins a report naming the exact spot ("RAF Fairford", "Scarborough Shoal") when their wording is similar, and the event moves to the exact spot. Two reports that share only a region or sea pin need similar wording too, so separate incidents in the Strait of Hormuz stay separate.
- **Same-story check.** Some duplicates still get through: one outlet pins a story to London and another to the air base, one files an Iran-linked plot under the Middle East and another under the NATO flank, and follow-up coverage keeps arriving for days. So recent events (last 3 days) of the same kind in the same country are shown to the model together, and it says which describe the same specific incident, follow-up coverage included. Those are folded into the earliest event; similar but separate incidents (two strikes on the same city, two drills, arrests in two different cases) stay apart. Sabotage and hybrid attacks, deployments, incursions and naval incidents in one country are always compared as a group; strikes and fighting only when their wording overlaps. Diplomatic and legal events are compared by who takes part (the same countries or bodies, one list within the other such as "EU" and "EU and Russia", or two in common, and some shared wording), wherever they were pinned; statements filed as a hybrid attack or deployment (the EU's top diplomat warning of Russian sabotage) are compared with them too. A reaction by another government (Estonia criticizing a German–Russian meeting) stays its own event. Big groups are shown in overlapping runs of 20, so the first reports of a story are compared with the later ones. When the model says two events are different, the pair gets one second look six hours later: when Google's usual model is busy, a weaker backup answers, and it once called nearly everything different. Groups are asked about in the order they started waiting, so a small group isn't pushed aside by big ones that keep gaining new events. When it folds events, the model also writes one headline from what they agree on (for example the latest death toll), which leads until a new report arrives; a headline with a number none of the events gives is thrown out. Stories told again days later (the RAF Fairford arrests came back 62 hours after the first reports) are compared too: each new event is checked against older events of the same kind and country, up to 14 days back, including the archive, so a retold story joins the original and keeps its date instead of showing up as new. This uses a model call at most every 30 minutes, or every update (up to three calls, the extra ones only while the day's budget is comfortable) while a backlog clears.
- **Model outages.** When Google's model is overloaded or down, failed attempts are not counted against the daily budget. The pipeline switches to another model: first the other Flash-Lite versions, then regular Gemini Flash (also free, with its own smaller daily limit) as a backup, going back to Flash-Lite after an hour. If every model is down, it tries again on the next run.

### Reading the map

Everything is on one globe. "On the map", at the top of the left panel, explains every symbol, and doubles as a filter: tap an entry to hide or show that kind of event (or launch paths, arms transfers, and carriers); "Show everything" brings them all back.

- **Icon and color show what happened.** Red for strikes (drone or missile, airstrike, explosion; interceptions by air defense use the drone or missile icon, and "Drone or missile" in the legend also covers the siren markers for warnings of drones or missiles in flight), amber for fighting on the ground (shelling, ground fighting, territory changing hands), blue for naval, violet for hybrid attacks and incursions, white for diplomacy and legal steps, slate for deployments, teal for arms transfers, green for financial aid (grants, loans, fund allocations: money rather than weapons), pale blue chips for US aircraft carriers.
- **The marker's style shows confidence.** Solid = corroborated by two or more independent sources (or by opposing sides). Outline = a single unaligned source. Dashed outline = only sources aligned with one side. Nearby news coverage from 3+ outlets (via GDELT) counts as one independent source. Google News results count per outlet: each outlet on the list in `sources.yaml` (Reuters, AP, the BBC, Axios, Haaretz, and so on) is its own source, and state or partisan media (TASS, Press TV, Xinhua, Anadolu, and others) are marked with the side they are aligned with. Results from outlets not on the list count together as one source, and only when no listed outlet has the story, so dozens of sites republishing one wire report never look like corroboration. A local station carrying a network's story (WINK News for CBS, say) is not the network, so it stays in that shared group; the network's own report counts once it is found.
- **Size shows severity and scale.** More serious events are bigger, and the bigger an incident's reported numbers (killed, injured, drones or missiles launched), the bigger its marker. Older events fade in longer time windows.
- **Recent events (last 6 hours, or new since your last visit) are animated by type:** impact bursts for drone and missile strikes, a diving chevron for airstrikes, rapid double flashes for shelling, a clash flicker for ground fighting, ripples at sea. To keep the page smooth, only the 20 newest animate at once. Once you open an event, it stops animating and loses its "new" mark (your browser remembers this for a week).
- **Moving lines:** red dashed lines run from launch areas to targets (faint when the launch area wasn't named and the line starts from the nearest known one); teal lines are supply routes (arms deliveries, and a country moving its own aircraft, ships or units between named places), shown for the same time window as everything else, thicker with more deliveries, solid when corroborated, with moving particles; pale dashed lines flow from a carrier toward its stated destination, with a faint line back to where it was last reported. Carrier lines follow the usual sea lanes rather than a straight line over land: San Diego to the Middle East crosses the Pacific and goes through the Malacca Strait, Norfolk to the Middle East through Gibraltar and Suez (carriers can't use the Panama Canal). The line shows the likely way, not a reported track. Routes and carrier lines answer hover and taps a little way either side, so you don't have to be exactly on the line. US forces sent to a military command (for example fighter jets moved from Aviano, Italy, "to CENTCOM") are drawn faint to that command's region, labeled "Middle East (CENTCOM area)", not to the United States; an event placed at a command is shown in its region, never at its headquarters. Forces leaving a command's area are drawn the other way: four KC-135 tankers returning home "from CENTCOM bases" run faint from "Middle East (CENTCOM area)" to Eielson Air Force Base, Alaska. A country moving its own forces is labeled by places, not "United States → United States".
- **Live updates.** The page checks every minute (and as soon as you come back to it) whether new data has been published, and adds new events without a refresh, with a short "new events added" note. The data itself is refreshed every 15 minutes.
- **On a phone,** tapping an event raises the list only as far as its headline and place, so the map stays in view; drag the list up for the rest.
- **Numbers until you zoom in.** Where three or more events sit close together, they collapse into the icon of the most important one with a count, until you zoom in to about a single province; click the count to zoom in. Closer in, overlapping markers fan out around their shared spot, with a thin line back to each true location. You can zoom in to about the scale of a city.
- **Opening view.** The dashboard opens on the last 24 hours (3 days if the last 24 were quiet) and flies to the busiest, most serious area of activity, with a short note naming it.
- **Times are when events happened,** not when the latest article about them appeared. The model reads the event date from each report and drops articles that only recap older news, so a two-day-old Security Council vote stays two days old. News feeds, Google News above all, sometimes list an old article with a fresh date (a site republished or updated it), and old stories get talked about again (a new claim about who was hurt in a strike months ago). First, for notable events that rest on a single news report, the article itself is opened and its own publication date read (news pages carry it in hidden "published" tags): items from military and government websites are checked too, whatever their importance, since they often post photos long after they were taken, and for a photo the date it was taken counts. An article first published (or a photo taken) more than two weeks before the date the feed gave is an old story and is dropped, and one published a few days earlier moves the event back to that date. Where the page can't be read (some sites block automated visits) nothing changes. Then every event built only from news feeds is checked, attack waves included (a months-old report of Iranian strikes on Bahrain once came back as a wave of its own). The pipeline searches for coverage around the event's own date and for coverage at least two weeks older. With no older coverage on the topic, the event is new and stays. Otherwise, current headlines that repeat an older headline are set aside as republished copies, and the model compares the event with the dated current and older headlines. The event is dropped only when the model points to an older headline about the same specific incident; new claims or details about an old incident count as old, because the map shows incidents when they happened, while another incident of the same kind (another strike on the same city, another coast guard drill) does not. Each drop is written to the run log with the matching headline, and the dropped event is kept so it can be put back if the rule changes. Some headlines describe things that happen again and again ("Yemen's Houthis launched ballistic missiles at Israel"), so the model can't tell a March article relisted today from a new attack. But a real attack that size is covered widely within hours. So a notable event that rests on a single source, where the search finds older coverage of the topic and either no current coverage from any other outlet or an article whose own date couldn't be read (the page blocked the check twice), is marked "Possibly an old story": it stays on the map, faded and not animated, with a note explaining why, and sends no alert, until a second, independent source reports it. It is not hidden, because the search can miss current coverage that is worded differently or comes from places few outlets cover. Later reports still attach to the original event; the detail view shows when the latest one arrived.

**Attack waves.** Missile, drone, and interception reports with a known attacker are grouped into one event per direction (Russia → Ukraine, Ukraine → Russia, Iran → Israel, and so on), covering reports within 18 hours of when the wave began, so an overnight attack stays together. A strike report that names no attacker ("drones hit Erbil") joins a wave on the same country when it hit one of the wave's places. The wave's icon sits on the main target, the other locations hit are small red dots, and the panel lists every location, launch and intercept totals, and launch areas.

**Shipping incidents.** UKMTO (UK Maritime Trade Operations) and JMIC notices are the primary authority on attacks on merchant ships. The pipeline can't read UKMTO's own site, so it searches the news for reports of UKMTO and JMIC notices; every result goes to the model, which takes the position and description from the notice and names UKMTO in the summary, and a report citing UKMTO leads the incident's headline.

**Drone and missile alerts.** Air force channels post a running stream of warnings that a drone or missile is heading toward a place. Warnings with nothing reported hit are grouped into one siren marker per country per day (same 09:00 UTC days), labeled with the number of alerts. Click it to list the places named and see them as faint dots. Alerts never count as places hit in an attack wave, and they never add attribution the posts don't make. Over NATO's eastern flank, every airspace alert stays its own event.

**Aircraft carriers.** All 11 active US carriers (U.S.S. Nimitz, U.S.S. Gerald R. Ford, and so on) are listed in the left panel. Positions come from USNI News' daily Fleet and Marine Tracker (read from its own feed, so no edition is missed) and from departure and arrival reports in between. The globe shows carriers at sea, plus any that moved this week; the tracker lists every deployed carrier, so the rest are listed as in home waters. When a carrier moves, its icon sails from the old position to the new one. Positions are never estimated between reports. News reports are checked before they move a carrier: a vague place ("the Middle East") is not a position, a carrier reported at another carrier's home port (Ford "departing San Diego") is almost always a report about the other carrier, and a move faster than a carrier can sail since its last report waits for a second, different report to confirm it. The dashed line flowing out of a carrier points to its stated destination; a faint still line shows where it came from. USNI refuses article pages to GitHub's servers, so the tracker is read from the text in USNI's feed, directly (by hull number and the named sea area or port), without the model, so it works even when the model is down. The tracker outranks news: a later news report placing a carrier somewhere it couldn't have sailed to since the tracker is replaced. A tracker more than three days old is not used to say which carriers are home. When Kennedy (CVN-79) commissions or a carrier changes home port, update `CARRIERS` and `HOME` in `pipeline/fleet.py`.

**Arms transfers.** Deliveries become supply routes on the globe and in the Supply routes list (left panel): one line per supplier and recipient over 30 days, through any named hubs (Ramstein, Rzeszów). When reports only name countries, the line runs between the two countries and is drawn faint. Pledged packages are listed but not drawn. Intercepted shipments are markers.

**Legal steps.** Article 51 letters, War Powers reports, Security Council resolutions, and ICJ/ICC actions appear with the scales icon. Any event whose sources state the legal basis the acting state gives for using force shows it in a highlighted box. The dashboard records claimed justifications; it does not assess them.

**About GDELT.** GDELT is a free database that reads news sites worldwide and logs each report of violence with a location, every 15 minutes. It is not drawn on the map. It is used for one thing: when three or more separate outlets report violence near a place where only one source has posted, that report is upgraded to corroborated. Set `gdelt: false` in `sources.yaml` to turn it off.

**Looking for corroboration.** Important events (severity 2 or 3) from the last 12 hours that still rest on a single source or one side's claim get a targeted Google News search: the place name plus words for that kind of event (for a naval incident near Hormuz, `"Strait of Hormuz" (ship OR vessel OR tanker)`), limited to the last day. Up to 8 events a run, each searched at most twice and at least 3 hours apart. The results go through the same extraction, merging, and confidence rules as everything else, so they upgrade an event only if they really describe the same incident. They are credited like any other Google News result: a listed outlet (CBS News, Reuters, and so on) counts as its own source, while outlets not on the list count together as one.

**What changed in the last 6 hours.** A short brief sits at the top of the event list. It is machine-written, at most once an hour and only when events changed, from the dashboard's own corroborated events of the last 6 hours and nothing else: no single-source reports or one-sided claims, no outside knowledge, no predictions. Every bullet cites the events it rests on (click a place name to open one), keeps the confidence explicit ("a single-source report says…", "Russia's MoD claims…"), and bullets citing events that don't exist, or stating a single-source or one-sided report without saying so, are thrown out. There are no per-theater summaries: they tended to fold several single-source reports into one sentence stated as fact. The brief ignores the time and theater filters. If the model is unavailable, the previous brief stays, with the time it was written.

**Using it.** Click any marker, carrier, or route for details and sources. **Key developments** (severe and corroborated) are pinned at the top of the feed. The small bar charts next to each theater show events per day over the past week. Press **H** to hide the panels, **/** to search, **Esc** to go back. On phones, drag or tap the bar at the top of the event list to collapse or expand it.

## Customizing

- **Sources:** `pipeline/config/sources.yaml`. The `outlets` list there says how Google News results from each outlet count: tier 1 for established outlets (processed first and preferred for headline summaries), tier 2 for other known outlets, and a `side` for state or partisan media. Each source has a `kind` and optionally a `side`; that is what drives the confidence colors, so label partisan and official channels honestly. The dashboard's Sources panel shows which ones are failing. The starter lists are thinnest for the Middle East, Africa, and the Indo-Pacific.
- **Theaters:** `pipeline/config/theaters.yaml` (countries, map boxes, camera positions). If you add or rename a theater, update the theater list in the prompt in `pipeline/extract.py` too.
- **Corrections:** `pipeline/config/corrections.yaml`. Every event's id is shown at the bottom of its detail view (also the part after `#` in the page address). Each entry needs the id and a short `note`, and does one of three things:

  ```yaml
  corrections:
    - id: 74974a38ada6
      hide: true
      note: Old news. The frigate Dena was sunk on 4 March 2026.

    - id: 3fb55a726b74
      edit: {place: Kupiansk, lat: 49.71, lon: 37.62}
      note: The model placed this in the wrong town.

    - id: 5b0f6f6736f9
      drop_report: https://news.google.com/rss/articles/CBMi...
      note: This article is about a different meeting.
  ```

  `hide` takes the event off the dashboard, the brief, alerts, and the archive, and its reports can never bring it back. `edit` can change `summary`, `place`, `lat`, `lon`, `type`, and `severity` (1 to 3); the event then shows a small "Corrected" label with your note. `drop_report` removes one report (copy its "Open the original post" link) and recomputes the event's confidence without it. Hides and edits are undone by deleting the entry. Commit the file and the next update applies it. (The older `removed.yaml` still works, but new removals belong in `corrections.yaml`.)
- **Model, provider, and budget:** the `settings` block at the top of `sources.yaml`. Every model call counts against `daily_llm_calls` (400). Extraction stops when fewer than `extraction_reserve` (30) calls are left for the day, and the brief is skipped when fewer than `brief_min_calls` (5) are left. Any OpenAI-compatible provider works (Groq, OpenRouter, Mistral): change `llm_url` and `llm_models` and put that provider's key in the `GEMINI_API_KEY` secret.
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

## History

From the day this feature was added, every run also keeps a permanent archive on the `data` branch, in an `archive/` folder:

- `archive/2026-09-27.json` (one file per UTC day): that day's published events, by when they happened. Events stay here after they drop off the dashboard's 7-day view. Events taken down (hidden by a correction, or removed as old news) are taken out of the archive too.
- `archive/fleet/2026-09-27.json`: each day's latest aircraft carrier positions.

History starts from the day this merged; nothing earlier was recorded. There is no page for browsing it yet, but you can open the files on GitHub (switch the branch selector to `data`).

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
