"""Turn raw posts into structured events with a free LLM.

Default provider: Google's Gemini API free tier (key from https://aistudio.google.com),
called through its OpenAI-compatible endpoint. The key comes from the LLM_API_KEY
environment variable (the GEMINI_API_KEY repository secret in GitHub Actions).
Posts are batched (~25 per request) and a daily budget is spread evenly across the
day's runs. Posts that do not fit in this run wait in a queue.
"""
from __future__ import annotations

import json
import math
import os
import re
import time
from datetime import datetime, timedelta
from urllib.parse import urljoin

import requests

from common import hours_since, iso, log, parse_time


EVENT_TYPES = {
    "airstrike": "strike by aircraft",
    "missile_drone": "missile or drone attack",
    "artillery": "shelling, rockets, mortars",
    "ground": "ground fighting or raids",
    "territory": "a place changing hands, advances or withdrawals",
    "air_defense": "interceptions and shoot-downs",
    "naval": "incident at sea involving ships or submarines",
    "explosion": "blast or sabotage with unclear cause",
    "deployment": "troop or ship buildups, exercises, shows of force (a move between two named places, such as aircraft returning home from a region, is arms_transfer)",
    "diplomacy": "ceasefires, peace talks, signed agreements, summits, visits or meetings between leaders, alliance or defense-pact meetings and invocations, UN Security Council action, or formal escalations such as declarations of war; also a country's formal change in its international commitments or relations: leaving, suspending, or joining a treaty, alliance, or international body, expelling diplomats, recalling an ambassador, closing an embassy, cutting relations, or a new sanctions package against another state",
    "hybrid": "sabotage, arson, undersea cable or pipeline damage, GPS jamming, cyberattacks with physical effects, or foiled plots of these",
    "incursion": "airspace violations, drone incursions, border provocations, or military buildups at a border",
    "arms_transfer": "major arms deliveries, air or sea bridges (surges of cargo flights or ships carrying weapons), military aid deliveries, intercepted weapons shipments, or a country moving its own aircraft, ships or units between named places (deploying to a region, or returning home from one: supplier and recipient are both that country, with from and to). A port call, goodwill or business visit, or exercise abroad by a country's own ships or aircraft is deployment, placed at the port or exercise area, not arms_transfer. A purchase or sales deal between two countries (a contract signed or approved, nothing reported delivered) is diplomacy, with both countries in parties; a country's own arms production or orders from its own industry is production; neither is arms_transfer",
    "production": "a country's own arms production or orders from its own industry: new production lines, factory output targets, contracts with its own manufacturers, approvals or budgets for them, equipment newly received from its own industry; placed where the work happens, else the capital",
    "legal": "formal legal steps about a use of force or the conduct of hostilities: Article 51 letters to the UN Security Council, War Powers Resolution reports or votes, Security Council resolutions, ICJ or ICC orders, warrants, or rulings, and official statements of the legal basis for a strike",
}

SYSTEM_PROMPT = """You turn raw posts from OSINT accounts, official military channels, and news feeds into structured records for a live armed-conflict map. Reply with one JSON object and nothing else.

For every input item (identified by "i"), decide whether it reports a specific, new, concrete development in an armed conflict or a notable military action. Relevant: strikes, attacks, shelling, battles, territorial gains or losses, air-defense interceptions, missile or drone launches, naval or air incidents, significant troop deployments or military exercises, casualty reports tied to a specific attack, and hybrid-warfare incidents (sabotage, arson, cable or pipeline damage, GPS jamming, airspace or border violations, drone incursions) when a state is blamed or suspected. Arrests or charges count when they reveal a specific incident or plot. Credible reports of preparations for military action also count (units ordered or put on notice, operational planning reported by officials, force buildups), typed as deployment. Also relevant: major diplomatic developments that bear on these conflicts, such as ceasefire or peace talks, signed agreements, summits between parties or mediators, alliance or defense-pact meetings and invocations (for example NATO Article 4 consultations or meetings under the Saudi-Pakistan-Turkey Mecca defense pact), UN Security Council votes, and visits or meetings between heads of state or government, or foreign or defense ministers, that bear on these conflicts, even when no outcome is announced (for example Israel's prime minister visiting the UAE, or Ukraine's president meeting the US president; a secret, unannounced, or first-ever visit is especially significant). Also relevant: major arms transfers and air or sea bridges to parties in these conflicts, notable arms production by parties to these conflicts or by major military powers (see the production type), and formal legal steps about uses of force (see the legal type). Also relevant, anywhere in the world, typed as diplomacy: a country's formal change in its international commitments or relations, such as withdrawing from, suspending, or joining a treaty, alliance, or international body (for example the US leaving the Council of Europe's anti-corruption body GRECO, or a country quitting the ICC or a UN agency), expelling diplomats, recalling an ambassador, closing an embassy, or cutting diplomatic relations, and new sanctions packages imposed by a state or bloc on another state, its government, or its armed forces (for example a new EU package against Russia); designations of individual people or companies count only when tied to a conflict below. Announcements that a step is being considered don't count; formal notice, a signed decision, or the step taking effect does. Not relevant: opinion, analysis with no new event, fundraising, memes, anniversaries, domestic politics, routine condemnations or statements of concern, routine phone calls, visits by lower-level officials with no stated outcome, and items that fit none of the theaters below.

Theater ids:
- ukraine: Russia-Ukraine war, including strikes inside Russia or Belarus and the Black Sea
- nato_east: Russia or Belarus versus NATO and the EU outside Ukraine: incidents on or over the borders of Finland, Estonia, Latvia, Lithuania, Poland, and Romania; Kaliningrad; the Baltic Sea; and Russian-linked sabotage or hybrid attacks anywhere in Europe
- mideast: Israel, Gaza, West Bank, Lebanon, Syria, Iraq, Iran, Yemen, the Gulf states, the Red Sea, the Strait of Hormuz
- horn: Sudan, South Sudan, Ethiopia, Eritrea, Somalia, Djibouti
- drc_sahel: eastern DR Congo, Rwanda, Burundi, Uganda border areas, Mali, Burkina Faso, Niger, Nigeria, Chad, Mauritania
- indopac: China, Taiwan, Japan, the Koreas, the Philippines, the South and East China Seas, Vietnam, Myanmar, Thailand, Cambodia, India, Pakistan
- global: only for the changes in international commitments or relations described above when they concern none of the conflicts listed here (the US leaving GRECO or the WHO); a step that bears on one of these conflicts takes that conflict's theater (Russia quitting a treaty over Ukraine is ukraine). Never used for strikes, fighting, or anything else.
- latam: Latin America and the Caribbean: Cuba, Venezuela, Colombia, Ecuador, Mexico, Central America, Haiti, Guyana, the Caribbean Sea and the eastern Pacific, including US military operations there (strikes on boats, strikes on cartel or armed-group targets, deployments, planning for action against Cuba or Venezuela) and armed-group violence with political or military significance. Ordinary crime is not relevant.

Event types:
{types}

Output: {{"events": [one object per input item, in any order]}}
Irrelevant item: {{"i": <n>, "relevant": false}}
Relevant item:
{{"i": <n>, "relevant": true, "type": "<event type id>", "happened": "<when the event itself happened: YYYY-MM-DD or YYYY-MM-DDTHH:MM in UTC>" or null, "summary": "<max 25 words>", "place": "<most specific place named, English spelling>" or null, "admin1": "<province, oblast, or state>" or null, "country": "<ISO 3166-1 alpha-2>" or null, "lat": <number> or null, "lon": <number> or null, "attacker": "<ISO alpha-2 of the country whose forces carried it out>" or null, "parties": ["<ISO alpha-2 of each country, or UN, EU, NATO, AU, ICC, ICJ, or another body's short name, whose officials take part>"], "origins": [{{"place": "<launch or firing area named in the item>", "lat": <number>, "lon": <number>}}], "launched": <int> or null, "intercepted": <int> or null, "alert": true or false, "transfer": <transfer object, arms_transfer only>, "legal_basis": "<max 12 words>" or null, "theater": "<theater id>", "severity": <1, 2, or 3>, "claim": "report" or "official_claim", "killed": <int> or null, "injured": <int> or null}}

Optional key for any item (relevant or not): if the item says where a US Navy aircraft carrier (hull CVN-##) is, or that one departed, arrived, or is heading somewhere, add
"carrier": {{"hull": "CVN-78", "status": "departed" | "underway" | "operating" | "arrived" | "in port", "place": "<where it is now>", "lat": <number>, "lon": <number>, "heading_to": {{"place": "<stated destination>", "lat": <number>, "lon": <number>}} or null}}
Include it even when the item is otherwise not relevant (then keep "relevant": false). Only US aircraft carriers; ignore other ships.

Transfer object: {{"kind": "delivery" | "pledge" | "interdiction", "supplier": "<ISO alpha-2>", "recipient": "<ISO alpha-2>", "mode": "air" | "sea" | "land" | "unspecified", "from": {{"place": "...", "lat": <number>, "lon": <number>}} or null, "to": {{"place": "...", "lat": <number>, "lon": <number>}} or null, "via": [{{"place": "<named transit hub>", "lat": <number>, "lon": <number>}}], "what": "<max 8 words>", "flights": <int> or null, "value_usd": <number> or null, "money": true or false}}

Rules:
- Freshness: an item is relevant only if the event it reports happened within about 24 hours before the item was posted ("posted"). Articles that recap, react to, or analyze something older are not relevant, unless they reveal significant new facts about it (new casualty figures, a new attribution, a new official response); in that case the new facts are the event. Always fill "happened" when the item states or clearly implies when it happened ("on Tuesday", "overnight", "yesterday"), working from the posted date. News headlines are often undated and written in the present tense even when a story is republished months later: if you know from your own knowledge that the event happened earlier, put that date in "happened" (the item will then be treated as old).
- Photos and photo captions (a military website's photo gallery, "U.S. Navy photo by …", DVIDS): the event is what the photo shows, when it was taken. Use the date the caption gives for "happened"; if it gives none, the item is not relevant (a photo posted today may be months old).
- Write the summary yourself in plain, neutral English. Translate non-English items. Do not copy sentences from the item.
- When the source is a party to the conflict, attribute the claim in the summary (for example "Russian MoD claims...", "IDF says...").
- For hybrid incidents and incursions, say who blames whom exactly as the item does (for example "Polish officials suspect Russian involvement"). Never state attribution the item does not make.
- Never add facts that are not in the item. Unknown casualty numbers are null.
- place: where the event itself happened. Never the dateline, the city of the outlet or reporter, or the place a statement about it was made (diplomacy and legal steps have their own rule below). When the item names only a country or region ("fighting in Ethiopia intensifies"), put that country or region in place; do not substitute its capital. For an incident aboard a ship, the ship's stated location.
- lat/lon: your best estimate for the named place; null if you cannot place it at least at city or district level. Incidents at sea always get coordinates: work them out from the stated reference ("23 nautical miles northeast of Khasab", "off Fujairah"), or use the center of the named strait or sea. Put the sea area's name (for example "Strait of Hormuz") in place.
- Incidents involving merchant ships: UKMTO (UK Maritime Trade Operations) and JMIC notices are the primary authority. When the item cites one, take the position, time, and description from it, and name it in the summary ("UKMTO reports a vessel was hit by an unknown projectile 40 nautical miles east of Aden").
- Ship attacks: when a report says "unknown projectile", keep it unknown; name an attacker only when a source does (for example "US Central Command says an Iranian drone struck the tanker").
- severity 3 = major (10 or more killed, strike on a capital or critical infrastructure, large territorial change, direct combat between major powers, attack with dozens of missiles or drones, a ceasefire or peace deal signed or collapsing, an alliance invoked); 2 = notable (including high-level talks or emergency alliance meetings, a major power leaving, suspending, or joining a treaty, alliance, or international body, or cutting diplomatic relations); 1 = minor or local (including routine sanctions and expulsions of a few diplomats).
- attacker: the country whose forces carried out a strike, launch, raid, or incursion, when the item states or clearly implies it ("Russian drones" = RU, "Ukrainian drones hit a refinery" = UA, Houthi missiles = YE, Hezbollah rockets = LB, Iranian missiles = IR). For interceptions, the side whose weapons were intercepted. Otherwise null.
- parties: for diplomacy and legal items only, the countries (ISO alpha-2) or bodies (UN, EU, NATO, AU, ICC, ICJ, or another body's own short name, such as WHO, OSCE, GRECO) whose officials take part: who meets whom, who signs, who files or rules (Netanyahu visiting the UAE = ["IL", "AE"]; Iran proposing a deal to the US = ["IR", "US"]). Otherwise [].
- origins: launch or firing areas the item actually names (for example "launched from Kursk and Primorsko-Akhtarsk"), at most 6, with your coordinate estimate for each. Use [] when none are named. Never guess a launch site. For a launch, the place is where the missiles went (the target, or the sea they fell into), and the launch site goes in origins ("North Korea fired a ballistic missile from Wonsan into the East Sea": place East Sea, origins Wonsan).
- launched / intercepted: totals for a mass air attack when the item gives them ("Russia launched 120 drones, 98 were shot down" = 120 / 98). Otherwise null.
- alert: true when the item is only a real-time warning or tracking update about drones or missiles still in flight (for example an air force post that a drone is heading toward, approaching, or passing a place), with no hit, interception, damage, or casualties reported. Put place and lat/lon at the place named as the target or current position, and type it missile_drone. Otherwise false. Drones or aircraft entering another country's airspace are incursions, not alerts.
- The type is what happened, not the vehicle or setting: airstrike only for strikes by military aircraft, missile_drone only for missiles or drones, naval only for incidents at sea. An attack, hijacking or stabbing aboard a civilian airliner, train or bus is hybrid when a state is blamed or suspected, otherwise not relevant. Consequences of an incident (flights suspended or rerouted, repatriation flights, travel advisories, investigations continuing, "updates") are not events of their own; only a new fact about the incident itself counts, typed as the incident.
- For preparations or buildups aimed at a country or an armed group, place the event in that country, or where the group holds territory (the Houthis: Yemen, at the area named, else Sanaa), not at a sea or route the operation is meant to secure ("a Saudi plan to attack the Houthis to break the Red Sea chokehold" is placed in Yemen, not in the Red Sea); its capital if nothing more specific; say in the summary that it is planning or preparation, not action.
- For diplomacy, place the event where the meeting or signing happened; if no place is given, use the capital of the main party. For a withdrawal, suspension, expulsion, or sanctions, use the capital of the state taking the step (Washington for the US leaving GRECO); for a bloc, its seat (Brussels for the EU). Use the theater of the conflict it concerns, even if the meeting is elsewhere.
- transfer kind: "delivery" = weapons observed or reported moving or arriving (tracked flights, imaged ships, confirmed arrivals); "pledge" = a package announced, approved, or sold but not yet reported delivered; "interdiction" = a shipment seized, intercepted, or destroyed in transit.
- transfer money: true when what is given is money (grants, loans, fund allocations, budget support, compensation payments), false for weapons, equipment, or services (a military aid package of weapons is false; €6.6 billion released from the European Peace Facility is true).
- transfer from / to: only departure and arrival points the item names (airfield, port, city). Use null when none is named; never substitute a capital. When a country moves its own forces or aircraft to a military command's area ("F-16s moved from Aviano to CENTCOM"), put the command's name (CENTCOM, EUCOM, AFRICOM, INDOPACOM, SOUTHCOM) in to.place; it is a region, not the command's headquarters. via: transit hubs the item names (for example Ramstein, Rzeszow), else [].
- For an arms_transfer, put the event's own place and lat/lon at the named arrival point, or for an interdiction where it was seized; if none is named, use the recipient country's capital. Use the theater of the conflict the weapons are for.
- legal_basis: only when the item states the justification the acting state gives for using force (for example "self-defense under UN Charter Article 51", "host-state consent", "2001 AUMF"). Never infer one. For legal-type events, place them where the step happened (UN headquarters, The Hague, Washington) but use the theater of the conflict concerned.
- claim = "official_claim" when the item is a government, military, or armed-group statement about its own actions or results; otherwise "report".
- Items from "ISW Map Room" are findings read from a map by the Institute for the Study of War (ISW): each names a place printed on the map and the date. Relevant when it reports an event in the map's own period; use its date for "happened" and its place; keep ISW's wording and attribution in the summary ("ISW assesses Russian forces advanced near Kupyansk"). A change in who controls ground or where the front line runs is territory.
""".format(types="\n".join(f"- {k}: {v}" for k, v in EVENT_TYPES.items()))

# International commitments and relations (added 2026-09-30): leaving or joining treaties and bodies,
# expulsions, sanctions. Posts that matched only these were rejected before then.
_COMMITMENTS = (r"withdr[ae]w(?:s|n|al|ing)?|pull(?:s|ed|ing)? out|quits?|quitting|rejoin(?:s|ed)?|"
                r"membership|suspend(?:s|ed|ing)?|conventions?|expel(?:s|led|ling)?|expulsions?|"
                r"ambassadors?|embass(?:y|ies)|diplomats?|diplomatic (?:ties|relations)|sanctions?")
# German, Italian, Dutch, Spanish, Portuguese and Indonesian words (added 2026-10-02 with searches of
# Der Spiegel, NZZ, Corriere della Sera, NRC, El País, Reforma, Grupo Globo and Kompas).
_EUROPE_ASIA = (r"angriffe?|luftangriffe?|raketen?|drohnen?|soldaten|getötet|waffenruhe|kriege?s?|"
                r"attacc(?:o|hi)|missili|droni|soldati|uccis[io]|tregua|bombardament[io]|guerra|"
                r"aanval(?:len)?|raketten|gedood|oorlog|wapenstilstand|"
                r"misil(?:es)?|dron|tropas|alto el fuego|"
                r"bombardeios?|m[ií]ssil|m[ií]sseis|mortos|cessar-fogo|"
                r"serangan|rudal|militer|tewas|perang|gencatan senjata")
_EN = (r"air ?strikes?|strikes?|struck|missiles?|drones?|uavs?|shahed|shell(?:ing|ed)|artillery|rockets?|"
       r"mortars?|attack(?:s|ed)?|explosions?|blasts?|killed|dead|casualt(?:y|ies)|wounded|injur(?:ed|ies)|"
       r"clash(?:es|ed)?|fighting|offensive|advanc(?:e|es|ed)|captur(?:e|ed)|seiz(?:e|ed)|liberat(?:e|ed)|"
       r"intercept(?:s|ed|ion)?|shot down|air defen[cs]e|ceasefire|truce|bomb(?:s|ing|ed)?|raids?|ambush(?:ed)?|"
       r"troops|soldiers|warships?|destroyers?|frigates?|carriers?|submarines?|navy|naval|coast guard|pla|"
       r"drills?|exercises?|incursions?|adiz|blockade|sorties?|houthis?|hezbollah|idf|irgc|hamas|rsf|m23|"
       r"jnim|al-shabaab|tplf|fano|junta|militants?|insurgents?|gunmen|sabotage|saboteurs?|arson|"
       r"undersea|cables?|pipelines?|jamming|jammed|spoofing|gps|airspace|violat(?:e|ed|es|ion|ions)|"
       r"border guards?|provocations?|shadow fleet|hybrid|cyber ?attacks?|balloons?|espionage|spies|spy|"
       r"wars?|wartime|visit(?:s|ed|ing)?|trip|met|meets?|meeting|hosts?|hosted|"
       r"talks|negotiat(?:e|es|ed|ing|ions?)|agreements?|accords?|summits?|mediat(?:e|ed|or|ors|ion)|envoys?|"
       r"peace|pact|treaty|security council|article 4|article 5|foreign ministers?|defen[cs]e ministers?|"
       r"chiefs of staff|delegations?|military|pentagon|southcom|southern command|deploy(?:s|ed|ing|ment|ments)?|"
       r"build-?up|mobili[sz](?:e|ed|ation)|on notice|cartels?|guerrillas?|eln|farc|gangs?|"
       r"ataques?|bombardeos?|enfrentamientos?|militares|ej[eé]rcito|muertos|fuerzas armadas|"
       r"carriers?|strike group|cvn|uss|airlift|air ?bridge|shipments?|deliver(?:y|ies|ed)|military aid|"
       r"c-17|il-76|antonov|arms|weapons|munitions|ammunition|article 51|war powers|aumf|icj|icc|"
       r"self-defen[cs]e|warrants?|rulings?|provisional measures|legal basis|"
       r"tankers?|vessels?|ships?|shipping|cargo|freighters?|bulk carrier|container ship|merchant|mariners?|seafarers?|"
       r"crew|ukmto|ambrey|jmic|projectiles?|hormuz|bab el-mandeb|red sea|gulf of aden|hijack(?:ed|ing)?|boarded|"
       r"mines?|limpet|sank|sinking|ablaze|adrift|hits?|" + _COMMITMENTS + "|" + _EUROPE_ASIA)
# Military aircraft and their bases, as OSINT accounts post force movements ("At least 10 of the
# B-1s have departed RAF Fairford", OSINTtechnical, 2026-10-04): added 2026-10-04 (prefilter version 7).
_AIR = (r"bombers?|fighter jets?|warplanes?|combat aircraft|military aircraft|military planes?|squadrons?|"
        r"air ?bases?|airbase|airfields?|raf|usaf|redeploy(?:s|ed|ing|ment|ments)?|"
        r"b-?1b?s?|b-?2s?|b-?21s?|b-?52h?s?|f-?(?:15|16|18|22|35)[a-z]?s?|a-?10s?|kc-?(?:10|46|135)s?|"
        r"e-?(?:3|7|8|11)[a-z]?s?|p-?8[a-z]?s?|rc-?135[a-z]?s?|mq-?9[a-z]?s?|rq-?4[a-z]?s?|c-?(?:5|130)[a-z]?s?|"
        r"tu-?(?:22m?3?|95|160)s?|su-?(?:24|25|27|30|34|35|57)s?|mig-?(?:29|31)s?")
# Ground changing hands, as regional outlets headline it ("Sudan Army Captures Mazroub", "resistance
# forces take control of"), and French for the Sahel and eastern DRC (Radio Okapi, actualite.cd,
# lefaso.net, RFI Afrique): added 2026-10-04 with those feeds (prefilter version 6).
_CAPTURE = (r"captur(?:e|es|ed|ing)|recaptur\w*|retak(?:e|es|en|ing)|retook|overr(?:an|un|uns|unning)|"
            r"(?:take|takes|took|taken|taking|seiz\w*|regain\w*|wrest\w*|lose|loses|lost|losing)\s+control")
_FRENCH = (r"attaques?|frappes?|combats?|affrontements?|tu[ée]s|tu[ée]es|morts|bless[ée]s|rebelles|"
           r"jihadistes?|djihadistes?|terroristes?|embuscades?|offensive|assauts?|empar[ée]e?s?|"
           r"reprennent|reprend|repris|reprise|FARDC|wazalendo|enl[èe]vements?|bombardements?")
CONFLICT_RE = re.compile(
    rf"\b(?:{_EN})\b"
    rf"|\b(?:{_CAPTURE}|{_FRENCH})\b"
    rf"|\b(?:{_AIR})\b"
    r"|удар|обстр|ракет|дрон|бпла|шахед|атак|вибух|взрыв|штурм|наступ|звільн|освобо|ппо|пво|загибл|погиб|"
    r"поранен|ранен|збит|сбит|знищ|уничтож|окупант|оккупан"
    r"|غارة|غارات|قصف|صاروخ|صواريخ|مسيرة|مسيّرة|اشتباك|انفجار|استهداف|قتلى|جرحى"
    r"|هجوم|هجمات|ضربة|ضربات|باليستي|اعتراض|إسقاط|مسيرات"
    r"|ירי|טיל|רקט|יירוט|פיגוע"
    r"|演习|军演|解放军|导弹|战机|军舰|台海",
    re.IGNORECASE,
)


class RateLimited(Exception):
    pass


class Busy(RuntimeError):
    """The provider is overloaded or down (HTTP 5xx). Such calls do nothing and are not counted
    against the daily budget; an outage used to drain it with failed attempts."""


class OutOfCredit(RuntimeError):
    """The provider refused for billing (HTTP 402, "prepayment credits are depleted"): nothing was
    done, it is not counted, posts keep their place in the queue, and the model is left alone for
    CREDIT_RECHECK (one probe then) instead of every model being probed every run."""


CREDIT_RECHECK = timedelta(hours=1)


def _paused(state: dict, now: datetime) -> bool:
    """The provider refused for billing less than CREDIT_RECHECK ago."""
    t = parse_time(state.get("llm_out_of_credit"))
    return bool(t and now - t < CREDIT_RECHECK)


def _out_of_credit(state: dict, now: datetime, r) -> None:
    if not state.get("llm_out_of_credit"):
        log(f"[extract] the model provider refused for billing (HTTP 402: credits depleted); nothing is counted, "
            f"posts wait in the queue, and the model is checked again hourly: {_describe(r)[:160]}")
    state["llm_out_of_credit"] = iso(now)
    state.pop("llm_model", None)


# Words added to the filter on 2026-09-27. Posts that matched only these were rejected before
# then; run.py uses this once to give them another look.
_ADDED_WORDS = {
    2: re.compile(r"\b(?:wars?|wartime|visit(?:s|ed|ing)?|trip|met|meets?|meeting|hosts?|hosted)\b", re.IGNORECASE),
    3: re.compile(rf"\b(?:{_COMMITMENTS})\b", re.IGNORECASE),  # 2026-09-30: treaties, bodies, expulsions, sanctions
    4: re.compile(rf"\b(?:{_EUROPE_ASIA})\b", re.IGNORECASE),  # 2026-10-02: German, Italian, Dutch, Spanish, Portuguese, Indonesian
    5: re.compile(r"هجوم|هجمات|ضربة|ضربات|باليستي|اعتراض|إسقاط|مسيرات"),  # 2026-10-02: Arabic attack words
    6: re.compile(rf"\b(?:{_CAPTURE}|{_FRENCH})\b", re.IGNORECASE),  # 2026-10-04: captures, and French
    7: re.compile(rf"\b(?:{_AIR})\b", re.IGNORECASE),  # 2026-10-04: military aircraft and air bases
}
PREFILTER_VERSION = max(_ADDED_WORDS)


def rejected_before_added_words(item: dict, version: int = 1) -> bool:
    """Would the keyword filter at `version` have rejected this post, which the current one accepts?"""
    text = item.get("text", "")
    if not item.get("prefilter", True) or not CONFLICT_RE.search(text):
        return False
    for v, words in _ADDED_WORDS.items():
        if v > version:
            text = words.sub(" ", text)
    return not CONFLICT_RE.search(text)


def is_candidate(item: dict) -> bool:
    if not item.get("prefilter", True):
        return True
    return bool(CONFLICT_RE.search(item.get("text", "")))


def _ts(item: dict) -> float:
    t = parse_time(item.get("time"))
    return t.timestamp() if t else 0.0


REJECTED_MEMORY = timedelta(hours=24)


def headline_key(text: str) -> str:
    """A headline without its " - Outlet" ending, case or punctuation."""
    head = (text or "").split("\n", 1)[0].rsplit(" - ", 1)[0]
    return re.sub(r"\W+", " ", head.lower()).strip()[:160]


def skip_rejected(items: list[dict], state: dict, now: datetime) -> list[dict]:
    """Leave out news items whose headline the model already judged irrelevant in the last day (the
    same headline relisted by another search or outlet): 1 in 11 rejections was a repeat."""
    since = iso(now - REJECTED_MEMORY)
    memo = {k: v for k, v in (state.get("rejected_heads") or {}).items() if v >= since}
    state["rejected_heads"] = memo
    keep = [it for it in items if it.get("platform") != "rss" or headline_key(it.get("text", "")) not in memo]
    if len(keep) < len(items):
        log(f"[filter] {len(items) - len(keep)} headlines already judged irrelevant today; not sent again")
    return keep


def build_queue(pending: list[dict], fresh: list[dict], now: datetime, settings: dict) -> list[dict]:
    by_id: dict[str, dict] = {}
    for it in pending + fresh:
        # backfilled items carry their own, longer age limit
        if hours_since(it.get("time"), now) <= it.get("max_age_h", settings["max_item_age_hours"]):
            by_id[it["id"]] = it
    queue = sorted(by_id.values(), key=lambda it: (-int(it.get("weight", 1)), -_ts(it)))
    return queue[: settings["pending_max"]]


def calls_remaining(state: dict, settings: dict, now: datetime) -> int:
    """Model calls left today under the daily budget (all model use shares this one counter)."""
    day = now.strftime("%Y-%m-%d")
    usage = state.setdefault("llm_calls", {"date": day, "count": 0})
    if usage.get("date") != day:
        usage.update(date=day, count=0, by={})
    return int(settings["daily_llm_calls"]) - int(usage["count"])


def _spend(state: dict, purpose: str, n: int = 1) -> None:
    """Count (or, with n=-1, uncount) a model call, in the day's total and under its purpose."""
    usage = state.setdefault("llm_calls", {"count": 0})
    usage["count"] = int(usage.get("count", 0)) + n
    by = usage.setdefault("by", {})
    by[purpose] = max(0, int(by.get(purpose, 0)) + n)


def used_today(state: dict, purpose: str) -> int:
    return int(((state.get("llm_calls") or {}).get("by") or {}).get(purpose, 0))


# Daily shares of the model budget for the checks that run after extraction, so reading new posts
# always has most of it. On 2026-09-30 the same-story check took most of the day's 400 calls (up to
# three a run, every run, while its backlog never emptied) and extraction stopped with 358 posts
# waiting. Each share is paced over the day: by noon, about half of it (plus SHARE_BURST).
SHARES = {"dedupe": "dedupe_daily_max", "recency": "recency_daily_max", "maproom": "maproom_daily_max",
          "frontline": "frontline_daily_max", "frontline_review": "frontline_review_daily_max",
          "frontline_isw": "frontline_isw_daily_max"}
SHARE_DEFAULTS = {"dedupe_daily_max": 140, "recency_daily_max": 48, "maproom_daily_max": 16,
                  "frontline_daily_max": 40, "frontline_review_daily_max": 60, "frontline_isw_daily_max": 60}
SHARE_BURST = 4
# Not paced over the day: ISW's maps come out together, around 01:00 UTC, and are read as they come;
# ISW's written reports are read up front (two weeks' backlog first), then as they come.
UNPACED = {"maproom", "frontline_isw"}


def share_left(state: dict, settings: dict, now: datetime, purpose: str) -> int:
    """Calls `purpose` may still make now under its paced daily share (a large number if it has none)."""
    key = SHARES.get(purpose)
    if not key:
        return 10 ** 6
    cap = int(settings.get(key, SHARE_DEFAULTS[key]))
    elapsed = (now.hour * 60 + now.minute) / (24 * 60)
    paced = cap if purpose in UNPACED else min(cap, int(cap * elapsed) + SHARE_BURST)
    today = (state.get("llm_calls") or {}).get("date") == now.strftime("%Y-%m-%d")
    return paced - (used_today(state, purpose) if today else 0)


def calls_allowed(state: dict, settings: dict, now: datetime, reserve: int = 0) -> int:
    """Calls this run may make, spreading what is left over the day's remaining runs.
    `reserve` calls are held back (extraction leaves room for the situation brief)."""
    remaining = calls_remaining(state, settings, now) - reserve
    if remaining <= 0:
        return 0
    minutes_left = 24 * 60 - (now.hour * 60 + now.minute)
    runs_left = max(1, minutes_left // 15)
    per_run = max(1, math.ceil(remaining / runs_left))
    return min(per_run, int(settings["max_calls_per_run"]), remaining)


def _estimate_tokens(text: str) -> int:
    ascii_share = sum(1 for ch in text if ord(ch) < 128) / max(1, len(text))
    return int(len(text) / (3.6 if ascii_share > 0.8 else 1.8)) + 30


def make_batches(queue: list[dict], settings: dict) -> list[list[dict]]:
    batches, current, tokens = [], [], 0
    for it in queue:
        cost = _estimate_tokens(it["text"][:700])
        if current and (len(current) >= settings["batch_max_items"] or tokens + cost > settings["batch_token_budget"]):
            batches.append(current)
            current, tokens = [], 0
        current.append(it)
        tokens += cost
    if current:
        batches.append(current)
    return batches


def _repairs(text: str):
    """The reply as is, then with the slips models make in JSON mended, one more at each step:
    raw line breaks or tabs inside strings (strict=False), trailing commas, Python's None/True/False,
    botched \\u character codes (seen 2026-10-03 in an Ethiopian place name)."""
    yield text
    text = re.sub(r",\s*([}\]])", r"\1", text)
    yield text
    text = re.sub(r"(?<=[:\[,\s])(None|True|False)(?=\s*[,}\]])", lambda m: {"None": "null", "True": "true", "False": "false"}[m.group(1)], text)
    yield text
    # a botched character code ("\u12" for a Ge'ez letter) breaks the whole reply: drop just it
    yield re.sub(r"(?<!\\)\\u(?![0-9a-fA-F]{4})[0-9a-fA-F]{0,3}", "", text)


def json_error(content: str) -> str:
    """Where a reply stops being JSON, for the log."""
    try:
        json.loads(content, strict=False)
        return "parses"
    except ValueError as exc:
        pos = getattr(exc, "pos", 0) or 0
        return f"{exc.args[0] if exc.args else exc} near {content[max(0, pos - 60):pos + 40]!r}"


def _parse_json_object(content: str) -> dict | None:
    """Parse the model's reply, tolerating code fences or stray text around the JSON, and the
    usual slips (see _repairs)."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", (content or "").strip()).strip()
    for candidate in (text, text[text.find("{"): text.rfind("}") + 1] if "{" in text else ""):
        if not candidate:
            continue
        for attempt in _repairs(candidate):
            try:
                value = json.loads(attempt, strict=False)
            except ValueError:
                continue
            if isinstance(value, dict):
                return value
            if isinstance(value, list):
                return {"events": value}
    return None


def _content_from_response(r: requests.Response) -> str:
    """Return the assistant text from a normal JSON reply or a streamed (SSE) reply."""
    body = r.text or ""
    if body.lstrip().startswith("data:"):
        parts = []
        for line in body.splitlines():
            line = line.strip()
            if not line.startswith("data:") or line == "data: [DONE]":
                continue
            try:
                chunk = json.loads(line[5:].strip())
                delta = chunk["choices"][0].get("delta") or chunk["choices"][0].get("message") or {}
                parts.append(delta.get("content") or "")
            except (ValueError, KeyError, IndexError, TypeError):
                continue
        return "".join(parts)
    try:
        data = r.json()
    except ValueError:
        raise RuntimeError(
            f"non-JSON reply (HTTP {r.status_code}, {r.headers.get('content-type')}): {body[:300]!r}"
        ) from None
    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError(f"unexpected reply shape: {str(data)[:300]}") from None
    content = message.get("content") or ""
    if isinstance(content, list):
        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    if not content:
        finish = (data["choices"][0] or {}).get("finish_reason")
        raise RuntimeError(f"empty reply (finish_reason={finish}); refusal={message.get('refusal')!r}")
    return content


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _post(url: str, body: dict, token: str) -> requests.Response:
    """POST without letting a redirect silently turn the request into a GET."""
    r = requests.post(url, headers=_headers(token), json=body, timeout=180, allow_redirects=False)
    hops = 0
    while r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location") and hops < 3:
        url = urljoin(url, r.headers["location"])
        log(f"[extract] redirected (HTTP {r.status_code}) to {url}; re-sending as POST")
        r = requests.post(url, headers=_headers(token), json=body, timeout=180, allow_redirects=False)
        hops += 1
    return r


def _describe(r: requests.Response) -> str:
    return f"HTTP {r.status_code} {r.headers.get('content-type', '?')}: {(r.text or '')[:200]!r}"


FALLBACK_RECHECK = timedelta(hours=1)  # a backup model in use: try the preferred ones again after this


def _candidates(settings: dict) -> list[str]:
    """Preferred models, then the backups tried when all of those are busy or gone."""
    main = list(settings["llm_models"])
    return main + [m for m in settings.get("llm_fallback_models") or [] if m not in main]


def _find_model(token: str, settings: dict, state: dict, skip=()) -> dict | None:
    """Send a tiny request with each candidate model and keep the first that really answers.
    The preferred (Flash-Lite) models come first; the backups (regular Flash, also free, with a
    separate and smaller daily limit) are tried only when those are busy or unavailable."""
    url = settings["llm_url"]
    for model in [m for m in _candidates(settings) if m not in skip]:
        for json_mode in (True, False):
            body = {
                "model": model,
                "max_tokens": 200,
                "temperature": 0,
                "messages": [{"role": "user", "content": 'Reply with the JSON object {"ok": true} and nothing else.'}],
            }
            if json_mode:
                body["response_format"] = {"type": "json_object"}
            _spend(state, "probe")
            try:
                r = _post(url, body, token)
            except Exception as exc:  # noqa: BLE001
                _spend(state, "probe", -1)  # never reached the model
                log(f"[extract] probe {model}: {exc}")
                break
            if r.status_code == 429:
                raise RateLimited(r.text[:200])
            if r.status_code == 402:
                _spend(state, "probe", -1)  # refused for billing: nothing was done, and the other models share the bill
                _out_of_credit(state, datetime.now().astimezone(), r)
                return None
            if r.status_code >= 500:
                _spend(state, "probe", -1)  # overloaded or down: not counted
                log(f"[extract] probe {model}: busy ({_describe(r)[:60]}), trying the next model")
                break
            try:
                works = bool(r.json().get("choices"))
            except (ValueError, AttributeError):
                works = False
            log(f"[extract] probe {model} json_mode={json_mode}: {_describe(r)}")
            if works:
                backup = model not in settings["llm_models"]
                found = {"url": url, "model": model, "json_mode": json_mode, "fallback": backup,
                         "checked": iso(datetime.now().astimezone())}
                state["llm_model"] = found
                if state.pop("llm_out_of_credit", None):
                    log("[extract] the model provider answers again")
                log(f"[extract] using {model} (json_mode={json_mode})"
                    + (" as a backup while the preferred models are busy" if backup else ""))
                return found
            if r.status_code in (401, 403):
                log("[extract] the API key was rejected; check the GEMINI_API_KEY secret")
                return None
            if r.status_code == 404 or "not found" in (r.text or "").lower():
                _spend(state, "probe", -1)  # no such model: nothing was done
                break  # unknown model name: try the next one
    return None


def _call_model(batch: list[dict], token: str, chosen: dict, settings: dict) -> dict:
    payload = [
        {"i": n, "source": it["source"], "platform": it["platform"], "posted": it["time"], "text": it["text"][:700]}
        for n, it in enumerate(batch)
    ]
    body = {
        "model": chosen["model"],
        "temperature": 0.1,
        "max_tokens": int(settings["max_output_tokens"]),
        "stream": False,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps({"items": payload}, ensure_ascii=False)},
        ],
    }
    if chosen.get("json_mode", True):
        body["response_format"] = {"type": "json_object"}
    r = _post(chosen["url"], body, token)
    if r.status_code == 429:
        raise RateLimited(r.text[:200])
    if r.status_code == 402:
        raise OutOfCredit(r)
    if r.status_code >= 500:
        raise Busy(_describe(r))
    if r.status_code >= 400:
        raise RuntimeError(_describe(r))
    content = _content_from_response(r)
    parsed = _parse_json_object(content)
    if parsed is None:
        raise RuntimeError(f"model reply was not JSON: {content[:300]!r}")
    return parsed


def _num(v, lo, hi):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if lo <= x <= hi and not math.isnan(x) else None


def _int_or_none(v):
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


def _place(o) -> dict | None:
    if not isinstance(o, dict):
        return None
    lat, lon = _num(o.get("lat"), -90, 90), _num(o.get("lon"), -180, 180)
    if lat is None or lon is None:
        return None
    return {"place": (str(o.get("place")).strip()[:80] if o.get("place") else None), "lat": round(lat, 3), "lon": round(lon, 3)}


def _iso2(v) -> str | None:
    v = str(v or "").upper().strip()
    return v if re.fullmatch(r"[A-Z]{2}", v) else None


def _clean_transfer(t) -> dict | None:
    if not isinstance(t, dict):
        return None
    via = [v for v in (_place(x) for x in (t.get("via") or [])[:3]) if v]
    out = {
        "kind": t.get("kind") if t.get("kind") in ("delivery", "pledge", "interdiction") else "delivery",
        "via": via,
        "value_usd": _num(t.get("value_usd"), 0, 1e13),
        "supplier": _iso2(t.get("supplier")),
        "recipient": _iso2(t.get("recipient")),
        "mode": t.get("mode") if t.get("mode") in ("air", "sea", "land") else "unspecified",
        "from": _place(t.get("from")),
        "to": _place(t.get("to")),
        "what": (str(t["what"]).strip()[:80] if t.get("what") else None),
        "flights": _int_or_none(t.get("flights")),
        "money": t.get("money") if isinstance(t.get("money"), bool) else None,
    }
    return out if out["supplier"] and out["recipient"] else None


def clean_carrier(obj: dict, item: dict) -> dict | None:
    """A carrier position report, independent of whether the item is a map event."""
    c = obj.get("carrier") if isinstance(obj, dict) else None
    if not isinstance(c, dict):
        return None
    m = re.search(r"(\d{2})", str(c.get("hull") or ""))
    here = _place(c)
    if not m or not here:
        return None
    status = c.get("status") if c.get("status") in ("departed", "underway", "operating", "arrived", "in port") else "operating"
    return {"hull": f"CVN-{m.group(1)}", "status": status, **here, "heading_to": _place(c.get("heading_to")),
            "time": item["time"], "source": item["source"], "url": item["url"]}


def _happened(value, item: dict) -> str | None:
    """When the event happened, clamped to no later than the post itself. Date-only values
    are read as the end of that day (the latest the event could have happened)."""
    if not value:
        return None
    v = str(value).strip()
    t = parse_time(v + "T23:59:00Z" if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) else v)
    posted = parse_time(item.get("time"))
    if not t or not posted:
        return None
    return iso(min(t, posted))


def _clean_record(obj: dict, item: dict) -> dict | None:
    if not obj.get("relevant"):
        return None
    summary = str(obj.get("summary") or "").strip()
    if not summary:
        return None
    etype = obj.get("type")
    etype = "diplomacy" if etype == "ceasefire" else etype
    etype = etype if etype in EVENT_TYPES else "explosion"
    country = str(obj.get("country") or "").upper().strip()
    attacker = str(obj.get("attacker") or "").upper().strip()
    origins = []
    raw_origins = obj.get("origins") if isinstance(obj.get("origins"), list) else []
    if not raw_origins and obj.get("origin_lat") is not None:  # older single-origin shape
        raw_origins = [{"place": obj.get("origin_place"), "lat": obj.get("origin_lat"), "lon": obj.get("origin_lon")}]
    for o in raw_origins[:6]:
        if not isinstance(o, dict):
            continue
        olat, olon = _num(o.get("lat"), -90, 90), _num(o.get("lon"), -180, 180)
        if olat is not None and olon is not None:
            origins.append({"place": (str(o.get("place")).strip()[:80] if o.get("place") else None),
                            "lat": round(olat, 3), "lon": round(olon, 3)})
    try:
        severity = min(3, max(1, int(obj.get("severity") or 1)))
    except (TypeError, ValueError):
        severity = 1
    return {
        "type": etype,
        "happened": _happened(obj.get("happened"), item),
        "summary": summary[:240],
        "place": (str(obj["place"]).strip()[:120] if obj.get("place") else None),
        "admin1": (str(obj["admin1"]).strip()[:120] if obj.get("admin1") else None),
        "country": country if re.fullmatch(r"[A-Z]{2}", country) else None,
        "lat": _num(obj.get("lat"), -90, 90),
        "lon": _num(obj.get("lon"), -180, 180),
        "origins": origins,
        "attacker": attacker if re.fullmatch(r"[A-Z]{2}", attacker) else None,
        "parties": sorted({str(x).upper().strip() for x in (obj.get("parties") if isinstance(obj.get("parties"), list) else [])
                           if re.fullmatch(r"[A-Z]{2,8}", str(x).upper().strip())})[:6],
        "launched": _int_or_none(obj.get("launched")),
        "intercepted": _int_or_none(obj.get("intercepted")),
        "alert": str(obj.get("alert")).lower() == "true",
        "transfer": _clean_transfer(obj.get("transfer")) if etype == "arms_transfer" else None,
        "legal_basis": (str(obj["legal_basis"]).strip()[:120] if obj.get("legal_basis") else None),
        "theater": str(obj.get("theater") or "").strip(),
        "severity": severity,
        "claim": "official_claim" if obj.get("claim") == "official_claim" else "report",
        "killed": _int_or_none(obj.get("killed")),
        "injured": _int_or_none(obj.get("injured")),
        "item": item,
    }


def run(queue: list[dict], state: dict, settings: dict, now: datetime, disabled: bool = False):
    """Returns (records, leftover_queue, calls_used, carrier_reports)."""
    token = os.environ.get("LLM_API_KEY", "").strip()
    if disabled or not token:
        if not token:
            log("[extract] LLM_API_KEY is not set; add the GEMINI_API_KEY repository secret (see README)")
        return [], queue, 0, []
    allowed = calls_allowed(state, settings, now, reserve=int(settings.get("extraction_reserve", 30)))
    batches = make_batches(queue, settings)
    log(f"[extract] queue={len(queue)} batches={len(batches)} allowed_calls={allowed}")
    if not allowed or not batches:
        return [], queue, 0, []
    if _paused(state, now):
        log(f"[extract] the model provider refused for billing at {state['llm_out_of_credit']}; "
            f"{len(queue)} posts wait in the queue until it is checked again")
        return [], queue, 0, []

    chosen = state.get("llm_model")
    checked = parse_time(chosen.get("checked")) if chosen else None
    stale = (not chosen or not checked or now - checked > timedelta(hours=24)
             or chosen.get("url") != settings["llm_url"] or chosen.get("model") not in _candidates(settings)
             or (chosen.get("fallback") and now - checked > FALLBACK_RECHECK))
    if stale:
        before = state["llm_calls"]["count"]
        try:
            chosen = _find_model(token, settings, state)
        except RateLimited as exc:
            log(f"[extract] rate limited while checking models: {exc}")
            return [], queue, state["llm_calls"]["count"] - before, []
        if chosen is None:
            log("[extract] no model returned a usable answer; see the probe lines above")
            return [], queue, state["llm_calls"]["count"] - before, []
        allowed = max(0, allowed - (state["llm_calls"]["count"] - before))

    records: list[dict] = []
    carriers: list[dict] = []
    done: set[str] = set()
    used = 0
    failures = 0
    switched = False
    todo = list(batches[:allowed])
    n = 0
    while n < len(todo):
        batch = todo[n]
        if n:
            time.sleep(float(settings.get("seconds_between_calls", 0)))  # stay under the per-minute limit
        used += 1
        _spend(state, "extract")
        try:
            out = _call_model(batch, token, chosen, settings)
        except RateLimited as exc:
            log(f"[extract] rate limited, stopping for this run: {exc}")
            break
        except OutOfCredit as exc:
            used -= 1
            _spend(state, "extract", -1)  # not counted: refused for billing, the posts keep their place
            _out_of_credit(state, now, exc.args[0])
            break
        except Busy as exc:
            used -= 1
            _spend(state, "extract", -1)  # not counted: the model never did the work
            if not switched:
                switched = True
                log(f"[extract] {chosen['model']} is busy (not counted); looking for another model")
                try:
                    other = _find_model(token, settings, state, skip={chosen["model"]})
                except RateLimited:
                    other = None
                if other:
                    chosen = other
                    continue  # the same batch again, with the other model
            log(f"[extract] the model is busy, stopping for this run (not counted): {exc}")
            break
        except Exception as exc:  # noqa: BLE001
            log(f"[extract] batch failed: {exc}")
            for it in batch:
                it["attempts"] = int(it.get("attempts", 0)) + 1
            failures += 1
            if failures >= 2:
                state.pop("llm_model", None)  # re-check models next run
                log("[extract] two failures in a row; stopping for this run to save the daily budget")
                break
            n += 1
            continue
        n += 1
        failures = 0
        for it in batch:
            done.add(it["id"])
        for obj in out.get("events", []) if isinstance(out, dict) else []:
            if not isinstance(obj, dict):
                continue
            i = obj.get("i")
            if isinstance(i, int) and 0 <= i < len(batch):
                cv = clean_carrier(obj, batch[i])
                if cv:
                    carriers.append(cv)
                rec = _clean_record(obj, batch[i])
                if rec:
                    records.append(rec)
                elif batch[i].get("platform") == "rss":
                    # Headlines the model set aside, kept briefly so a missed story can be traced.
                    # News headlines and links only: post text from other platforms is never stored.
                    state.setdefault("model_rejected", []).append({
                        "time": batch[i]["time"], "source": batch[i]["source"], "url": batch[i]["url"],
                        "headline": batch[i]["text"].split("\n", 1)[0][:200], "at": iso(now)})
                    state.setdefault("rejected_heads", {})[headline_key(batch[i]["text"])] = iso(now)
    state["model_rejected"] = state.get("model_rejected", [])[-400:]
    leftover = [it for it in queue if it["id"] not in done and int(it.get("attempts", 0)) < 3]
    return records, leftover[: settings["pending_max"]], used, carriers


def ask_json(system_prompt: str, user_text: str, state: dict, settings: dict, now: datetime,
             max_tokens: int = 4000, _retry: bool = True, purpose: str = "other",
             images: list[str] | None = None) -> dict | None:
    """One budgeted model call outside the batch loop. Returns parsed JSON or None. If the model
    is busy, another model (a backup if need be) is tried once. `purpose` names what the call is
    for in the day's tally (state["llm_calls"]["by"]) and is checked against its share (share_left).
    `images` (data: URLs) are shown to the model after the text."""
    token = os.environ.get("LLM_API_KEY", "").strip()
    chosen = state.get("llm_model")
    if (not token or not chosen or _paused(state, now) or calls_allowed(state, settings, now) <= 0
            or share_left(state, settings, now, purpose) <= 0):
        return None
    body = {
        "model": chosen["model"], "temperature": 0, "max_tokens": max_tokens, "stream": False,
        "messages": [{"role": "system", "content": system_prompt},
                     {"role": "user", "content": [{"type": "text", "text": user_text}]
                      + [{"type": "image_url", "image_url": {"url": u}} for u in images] if images else user_text}],
    }
    if chosen.get("json_mode", True):
        body["response_format"] = {"type": "json_object"}
    _spend(state, purpose)
    try:
        r = _post(chosen["url"], body, token)
        if r.status_code >= 500:
            _spend(state, purpose, -1)  # overloaded or down: not counted
            log(f"[extract] one-off call: the model is busy (not counted): {_describe(r)[:120]}")
            if _retry:
                try:
                    other = _find_model(token, settings, state, skip={chosen["model"]})
                except RateLimited:
                    other = None
                if other:
                    return ask_json(system_prompt, user_text, state, settings, now, max_tokens, _retry=False,
                                    purpose=purpose, images=images)
            return None
        if r.status_code == 402:
            _spend(state, purpose, -1)  # refused for billing: nothing was done, not counted
            _out_of_credit(state, now, r)
            return None
        if r.status_code == 429:
            _spend(state, purpose, -1)  # refused for the rate limit: nothing was done, not counted
            log(f"[extract] one-off call ({purpose}) refused for the rate limit (not counted): {_describe(r)[:120]}")
            return None
        if r.status_code >= 400:
            log(f"[extract] one-off call ({purpose}) failed: {_describe(r)}")
            return None
        content = _content_from_response(r)
        parsed = _parse_json_object(content)
        if parsed is None:
            log(f"[extract] one-off call ({purpose}): the reply was not JSON ({len(content)} characters): {json_error(content)}")
        return parsed
    except requests.RequestException as exc:
        _spend(state, purpose, -1)  # never reached the model
        log(f"[extract] one-off call failed (not counted): {exc}")
        return None
    except Exception as exc:  # noqa: BLE001
        log(f"[extract] one-off call ({purpose}) failed: {exc}")
        return None