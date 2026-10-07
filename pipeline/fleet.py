"""Track US Navy aircraft carriers.

Two inputs:
  1. USNI News' Fleet and Marine Tracker (published daily). When a new edition appears in the USNI
     feed, the article is read once and the model lists every carrier's location.
  2. Carrier mentions in ordinary posts and news (departures, arrivals, transits), picked
     up during normal extraction.

Each carrier keeps its latest reported position, where it was before (so the map can
animate the move), a stated destination if any, and a short track of past positions.
Positions are never extrapolated: the map shows the last report and its date.

News reports are checked before they move a carrier (the tracker is trusted):
  - a vague place ("Middle East", "the Pacific") is not a position; it is ignored
  - a carrier reported at another carrier's home port (Ford "departing San Diego") is almost
    always a report about the other carrier; it is ignored
  - a move faster than a carrier can sail since its last report is held until a second,
    different report puts it in the same area (one misread article can't teleport it)
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from common import UTC, clean_text, haversine_km, iso, log

# Active carriers as of late September 2026. CVN-79 (Kennedy) is not due to commission until 2027;
# add it here when it does. Nimitz shifted home port to Norfolk in July 2026 ahead of inactivation.
CARRIERS = {
    "CVN-68": ("U.S.S. Nimitz", "U.S.S. Nimitz"),
    "CVN-69": ("U.S.S. Dwight D. Eisenhower", "U.S.S. Eisenhower"),
    "CVN-70": ("U.S.S. Carl Vinson", "U.S.S. Vinson"),
    "CVN-71": ("U.S.S. Theodore Roosevelt", "U.S.S. Roosevelt"),
    "CVN-72": ("U.S.S. Abraham Lincoln", "U.S.S. Lincoln"),
    "CVN-73": ("U.S.S. George Washington", "U.S.S. Washington"),
    "CVN-74": ("U.S.S. John C. Stennis", "U.S.S. Stennis"),
    "CVN-75": ("U.S.S. Harry S. Truman", "U.S.S. Truman"),
    "CVN-76": ("U.S.S. Ronald Reagan", "U.S.S. Reagan"),
    "CVN-77": ("U.S.S. George H.W. Bush", "U.S.S. Bush"),
    "CVN-78": ("U.S.S. Gerald R. Ford", "U.S.S. Ford"),
}
NORFOLK = ("Norfolk, Va.", 36.95, -76.33)
SAN_DIEGO = ("San Diego (North Island)", 32.70, -117.19)
HOME = {
    "CVN-68": NORFOLK,
    "CVN-69": NORFOLK,
    "CVN-70": SAN_DIEGO,
    "CVN-71": SAN_DIEGO,
    "CVN-72": SAN_DIEGO,
    "CVN-73": ("Yokosuka, Japan", 35.29, 139.67),
    "CVN-74": ("Newport News shipyard (refueling overhaul)", 36.98, -76.43),
    "CVN-75": NORFOLK,
    "CVN-76": ("Bremerton, Wash.", 47.56, -122.63),
    "CVN-77": NORFOLK,
    "CVN-78": NORFOLK,
}
FLEET_TRACKER_FEED = "https://news.usni.org/category/fleet-tracker/feed"
USNI_FEED = "https://news.usni.org/feed"
MOVE_KM = 150          # smaller changes are treated as the same position
TRACK_LEN = 12
FLEET_VERSION = 3      # bump to re-check stored positions against the rules below
MAX_KM_PER_DAY = 900   # about 20 knots, a fast sustained transit
SLACK_KM = 400         # rough coordinates for sea areas and ports
HOME_KM = 60           # "at" a home port
CONFIRM_KM = 500       # a second report this close confirms a held move
HOLD = timedelta(hours=72)
ELSEWHERE_KM = 1000    # another carrier reported this close: an impossible move is probably about it
ELSEWHERE_DAYS = 7     # ... if it was placed there within this many days
TRACKER_MAX_AGE = timedelta(days=3)  # the tracker is daily; an older edition can't say who is home now
VAGUE = {"middle east", "the middle east", "indo-pacific", "the indo-pacific", "pacific", "the pacific",
         "pacific ocean", "atlantic", "the atlantic", "atlantic ocean", "europe", "asia", "africa", "at sea",
         "overseas", "the region", "region", "gulf region", "central command", "centcom", "5th fleet",
         "6th fleet", "7th fleet", "2nd fleet", "3rd fleet", "unknown", "undisclosed", "deployment"}

TRACKER_PROMPT = """You read a USNI News Fleet and Marine Tracker article and list every US Navy aircraft carrier (hull CVN-##) it mentions, with its current location. Reply with one JSON object and nothing else:
{"as_of": "YYYY-MM-DD", "carriers": [{"hull": "CVN-78", "deployed": <true if on a deployment, including one returning home>, "status": "underway" | "operating" | "in port" | "in maintenance", "place": "<sea area or port, as stated>", "lat": <number>, "lon": <number>, "heading_to": {"place": "...", "lat": <number>, "lon": <number>} or null}]}
Rules: use only what the article says. "underway" = at sea in transit; "operating" = on station in a named area; "in port" = pierside; "in maintenance" = in a shipyard or major maintenance. Give your best coordinate estimate for each named place (a sea area's center is fine). heading_to only if the article states a destination; a carrier "returning from deployment" is heading to its home port if the article names it."""


def _hull(v) -> str | None:
    m = re.search(r"(\d{2})", str(v or ""))
    hull = f"CVN-{m.group(1)}" if m else None
    return hull if hull in CARRIERS else None


def _vague(place) -> bool:
    p = re.sub(r"\s*\(.*?\)", "", str(place or "")).strip().lower()
    return not p or p in VAGUE


def _known(c: dict) -> bool:
    return c.get("lat") is not None and not str(c.get("as_of") or "1970").startswith("1970")


def _days(a: str, b: str) -> float:
    ta, tb = datetime.fromisoformat(a.replace("Z", "+00:00")), datetime.fromisoformat(b.replace("Z", "+00:00"))
    return abs((tb - ta).total_seconds()) / 86400


def _too_fast(a: dict, b: dict) -> bool:
    """Could a carrier have sailed from a (lat, lon, as_of/time) to b in the time between?"""
    ta, tb = a.get("as_of") or a.get("time"), b.get("as_of") or b.get("time")
    if not ta or not tb or str(ta).startswith("1970"):
        return False
    return haversine_km(a["lat"], a["lon"], b["lat"], b["lon"]) > MAX_KM_PER_DAY * _days(ta, tb) + SLACK_KM


def _other_home(hull: str, lat: float, lon: float) -> str | None:
    """The home port this spot belongs to, if it is another carrier's and not this one's."""
    own = HOME.get(hull)
    if own and haversine_km(own[1], own[2], lat, lon) < HOME_KM:
        return None
    for place, hlat, hlon in set(HOME.values()):
        if haversine_km(hlat, hlon, lat, lon) < HOME_KM:
            return place
    return None


def _other_there(fleet: dict, hull: str, r: dict) -> str | None:
    """Another carrier placed within ELSEWHERE_KM of the report in the ELSEWHERE_DAYS before it:
    a move this carrier couldn't have made is then probably that carrier's (on 2026-10-05/06 two
    outlets' "second US carrier in Thailand" and The War Zone's weekly carrier roundup put the Ford
    and the Eisenhower, both at Norfolk, where the Bush was making a port call in Phuket)."""
    for h, o in (fleet or {}).items():
        if h == hull or not isinstance(o, dict) or o.get("lat") is None or not o.get("as_of"):
            continue
        if str(o["as_of"]).startswith("1970") or o["as_of"] > r["time"] or _days(o["as_of"], r["time"]) > ELSEWHERE_DAYS:
            continue
        if haversine_km(o["lat"], o["lon"], r["lat"], r["lon"]) <= ELSEWHERE_KM:
            return o.get("name") or CARRIERS.get(h, (h,))[0]
    return None


def _check(c: dict, r: dict, hull: str, fleet: dict | None = None) -> str | None:
    """Why a news report can't move this carrier now (None: it can)."""
    if _vague(r.get("place")):
        return f"'{r.get('place')}' is not a position"
    port = _other_home(hull, r["lat"], r["lon"])
    if port and not (c.get("lat") is not None and haversine_km(c["lat"], c["lon"], r["lat"], r["lon"]) < MOVE_KM):
        return f"{port} is another carrier's home port; the report is probably about that carrier"
    if _known(c) and _too_fast(c, r):
        # The position it can't be squared with may itself be an unconfirmed news report (an Iranian
        # channel put the Bush in the Strait of Hormuz; ship imagery had it entering the Malacca Strait
        # three days later, which fits USNI's tracker position from two days before that): a report
        # the carrier could have reached from its last tracker position replaces it.
        lt = c.get("last_trusted")
        if not c.get("trusted") and lt and r["time"] > lt["as_of"] and not _too_fast(lt, r):
            return "fits_tracker"
        other = _other_there(fleet, hull, r)
        if other:
            return f"{other} was reported there; the report is probably about it"
        held = [h for h in c.get("held", []) if _days(h["time"], r["time"]) * 86400 <= HOLD.total_seconds()]
        if any(h["url"] != r.get("url") and haversine_km(h["lat"], h["lon"], r["lat"], r["lon"]) < CONFIRM_KM
               for h in held):
            c.pop("held", None)
            return None  # a second report confirms the move
        held.append({"lat": r["lat"], "lon": r["lon"], "place": r.get("place"), "time": r["time"], "url": r.get("url"),
                     "source": r.get("source")})
        c["held"] = held[-5:]
        return "hold"
    return None


def update(state: dict, reports: list[dict]) -> int:
    """Apply position reports (oldest first). Returns how many carriers changed."""
    fleet = state.setdefault("fleet", {})
    changed = 0
    for r in sorted(reports, key=lambda x: x["time"]):
        hull = _hull(r.get("hull"))
        if not hull:
            continue
        name, short = CARRIERS[hull]
        c = fleet.setdefault(hull, {"hull": hull, "name": name, "short": short, "track": []})
        replacing = False
        if c.get("as_of") and r["time"] < c["as_of"]:
            # older than what we already know. The tracker still wins over a later news
            # report the carrier couldn't have sailed to from the tracker's position in time
            # (that report was misread or about an older voyage).
            if not (r.get("trusted") and r.get("status") != "home" and not c.get("trusted")
                    and _too_fast({**r, "as_of": r["time"]}, c)):
                continue
            log(f"[fleet] {name}: the tracker ({r.get('place')}, {r['time'][:10]}) replaces a later news report "
                f"({c.get('place')}, {c['as_of'][:10]}) it couldn't have sailed to")
            replacing = True
        over = None
        if not r.get("trusted"):
            why = _check(c, r, hull, fleet)
            if why == "fits_tracker":
                over, why = c.get("last_trusted"), None
                log(f"[fleet] {name}: {r.get('place')} fits the last tracker position ({over.get('place')}, "
                    f"{over['as_of'][:10]}); the unconfirmed {c.get('place')!r} report is set aside")
            if why == "hold":
                log(f"[fleet] {name}: {r.get('place')} is too far to have sailed since {c.get('place')}; "
                    "waiting for a second report")
                continue
            if why:
                log(f"[fleet] {name}: report ignored ({why})")
                h = r.get("heading_to")
                if why.endswith("is not a position") and h and c.get("lat") is not None \
                        and haversine_km(h["lat"], h["lon"], c["lat"], c["lon"]) >= MOVE_KM:
                    c["heading_to"] = h  # "set for a Middle East deployment": where it is going, not where it is
                continue
        if r["status"] != "home":
            c["at_home"] = False
        # a corrected position is not a move: no line from the report it replaces
        moved = not replacing and c.get("lat") is not None and haversine_km(c["lat"], c["lon"], r["lat"], r["lon"]) > MOVE_KM
        if replacing:
            for k in ("prev", "moved_at", "held"):
                c.pop(k, None)
        if over:
            # the line is drawn from the tracker's position, not from the report set aside
            c["prev"] = {k: over[k] for k in ("lat", "lon", "place", "as_of")}
            c["moved_at"] = r["time"]
            c.pop("held", None)
            c["track"] = [t for t in c.get("track", []) if t["time"] <= over["as_of"]]
        elif moved:
            c["prev"] = {"lat": c["lat"], "lon": c["lon"], "place": c.get("place"), "as_of": c.get("as_of")}
            c["moved_at"] = r["time"]
        heading = r.get("heading_to")
        if heading is None and r["status"] in ("departed", "underway") and c.get("heading_to"):
            heading = c["heading_to"]  # a transit update without a destination keeps the old one
        if heading and haversine_km(heading["lat"], heading["lon"], r["lat"], r["lon"]) < MOVE_KM:
            heading = None  # arrived
        c.update(lat=r["lat"], lon=r["lon"], place=r.get("place"), status=r["status"], as_of=r["time"],
                 source=r.get("source"), url=r.get("url"), heading_to=heading, trusted=bool(r.get("trusted")))
        if r.get("trusted") and not r["time"].startswith("1970"):  # an undated home-port start is no anchor
            c["last_trusted"] = {"lat": r["lat"], "lon": r["lon"], "place": r.get("place"), "as_of": r["time"]}
        if r["status"] == "departed":
            c["departed_at"] = r["time"]
        track = c["track"]
        if not track or haversine_km(track[-1]["lat"], track[-1]["lon"], r["lat"], r["lon"]) > 50:
            track.append({"lat": r["lat"], "lon": r["lon"], "place": r.get("place"), "time": r["time"]})
        c["track"] = track[-TRACK_LEN:]
        changed += 1
    return changed


# Sea areas and ports named in USNI's tracker, most specific first. Only named places: "the
# Pacific" or "the Atlantic" alone are not positions.
TRACKER_PLACES = [
    (r"pearl harbor", "Pearl Harbor, Hawaii", 21.35, -157.95), (r"hawaii", "near Hawaii", 21.0, -158.5),
    (r"san diego|north island", "San Diego", 32.70, -117.19), (r"southern california|socal|off california", "off Southern California", 32.5, -118.5),
    (r"newport news", "Newport News, Va.", 36.98, -76.43), (r"norfolk", "Norfolk, Va.", 36.95, -76.33),
    (r"mayport", "Mayport, Fla.", 30.39, -81.40), (r"yokosuka", "Yokosuka, Japan", 35.29, 139.67),
    (r"bremerton|puget sound", "Bremerton, Wash.", 47.56, -122.63), (r"everett", "Everett, Wash.", 47.98, -122.22),
    (r"\bguam\b", "Guam", 13.44, 144.66), (r"okinawa", "off Okinawa", 26.3, 127.8), (r"busan", "Busan, South Korea", 35.10, 129.04),
    (r"manila", "Manila", 14.58, 120.97), (r"singapore", "Singapore", 1.26, 103.82), (r"da nang", "Da Nang, Vietnam", 16.05, 108.20),
    (r"bahrain|manama", "Bahrain", 26.20, 50.60), (r"souda bay|crete", "Souda Bay, Crete", 35.49, 24.08),
    (r"naples", "Naples, Italy", 40.84, 14.25), (r"\bsplit\b", "Split, Croatia", 43.50, 16.44),
    (r"philippine sea", "Philippine Sea", 20.0, 131.0), (r"south china sea", "South China Sea", 12.0, 114.0),
    (r"east china sea", "East China Sea", 29.0, 125.0), (r"sea of japan|east sea", "Sea of Japan", 40.0, 135.0),
    (r"yellow sea", "Yellow Sea", 35.0, 123.0), (r"celebes sea", "Celebes Sea", 3.0, 122.0), (r"sulu sea", "Sulu Sea", 8.0, 120.0),
    (r"coral sea", "Coral Sea", -18.0, 155.0), (r"tasman sea", "Tasman Sea", -40.0, 160.0), (r"timor sea", "Timor Sea", -10.0, 127.0),
    (r"western pacific", "Western Pacific", 15.0, 140.0), (r"eastern pacific", "Eastern Pacific", 25.0, -125.0),
    (r"gulf of alaska", "Gulf of Alaska", 57.0, -145.0), (r"bay of bengal", "Bay of Bengal", 15.0, 88.0),
    (r"north arabian sea", "North Arabian Sea", 20.0, 63.0), (r"arabian sea", "Arabian Sea", 16.0, 63.0),
    (r"gulf of oman", "Gulf of Oman", 24.5, 58.5), (r"strait of hormuz", "Strait of Hormuz", 26.57, 56.25),
    (r"persian gulf|arabian gulf", "Persian Gulf", 27.0, 51.5), (r"gulf of aden", "Gulf of Aden", 12.5, 47.5),
    (r"red sea", "Red Sea", 20.0, 38.5), (r"indian ocean", "Indian Ocean", -5.0, 75.0),
    (r"eastern mediterranean|eastern med\b", "Eastern Mediterranean", 33.5, 33.5), (r"adriatic", "Adriatic Sea", 42.5, 16.0),
    (r"aegean", "Aegean Sea", 38.5, 25.0), (r"ionian sea", "Ionian Sea", 38.0, 19.0), (r"mediterranean", "Mediterranean Sea", 35.0, 18.0),
    (r"norwegian sea", "Norwegian Sea", 68.0, 5.0), (r"north sea", "North Sea", 56.0, 3.0), (r"baltic", "Baltic Sea", 57.0, 19.0),
    (r"north atlantic", "North Atlantic", 45.0, -35.0), (r"western atlantic", "Western Atlantic", 35.0, -70.0),
    (r"caribbean", "Caribbean Sea", 15.0, -75.0), (r"gulf of mexico|gulf of america", "Gulf of Mexico", 25.0, -90.0),
    (r"california", "California", 33.5, -118.5), (r"virginia", "Virginia", 36.9, -76.0),
]
# carrier names as USNI writes them: "George H. W. Bush" and "George H.W. Bush", with ordinary or no-break spaces
_HULL_NAMES = {h: re.escape(n.replace("U.S.S. ", "")).replace(r"\ ", r"\s+").replace(r"\.", r"\.\s?")
               for h, (n, _) in CARRIERS.items()}
# a clause about where a carrier came from or is based, not where it is
_ORIGIN = re.compile(r"(?i)\b(?:depart\w*|left|leaves?|leaving|sailed from|pulled out of|homeported|home-?ported|"
                     r"based (?:at|in|out of)|from)\b")
_CLAUSES = re.compile(r"(?i)(?:,?\s+and\s+(?=(?:is|are|was|were|has|have|will|remains?|then|continued|continues)\b)|;\s*|,\s+(?=(?:is|are|was|were|has|remains?)\b))")
_DEST = re.compile(r"(?:en route to|heading (?:to|toward|for)|bound for|transiting to|on (?:its|their) way to|"
                   r"returning to|headed (?:to|for|toward))\s+(?:the\s+)?([^.;,]{3,60})", re.IGNORECASE)


def _tracker_place(text: str):
    low = (text or "").lower()
    for pat, place, lat, lon in TRACKER_PLACES:
        if re.search(pat, low):
            return {"place": place, "lat": lat, "lon": lon}
    return None


TRACKER_PARSER = 4  # bump to re-read the current edition after changing parse_tracker
_CAPTION = re.compile(r"(?i)navy photo|photo(?:graph)? (?:by|courtesy)|\bphoto\b\s*$|file photo|\bimage\b")


def _is_heading(tag: str, attrs: str, text: str) -> bool:
    if tag.lower().startswith("h"):
        return True
    # some pages set section titles as a short bold paragraph: "<p><strong>In the Philippine Sea</strong></p>"
    return (len(text) <= 60 and not text.rstrip().endswith(".") and bool(re.search(r"(?i)<(strong|b)\b", attrs))
            and bool(re.match(r"(?i)(?:in|near|off|at|around)\b", text)))


def _clause_places(sentence: str):
    """(current place, departure place, destination) named in a sentence, clause by clause:
    "departed last Sunday from Apra Harbor, Guam" | "is transiting ... en route to California"."""
    here = departed = None
    d = _DEST.search(sentence)
    dest = _tracker_place(d.group(1)) if d else None
    for clause in _CLAUSES.split(sentence):
        cd = _DEST.search(clause)
        where = clause[:cd.start()] if cd else clause
        place = _tracker_place(where)
        if not place:
            continue
        if _ORIGIN.search(where):
            if re.search(r"(?i)\bdepart|\bleft\b|\bleav|sailed from|pulled out", where):
                departed = departed or place
            continue  # where it came from or is based, not where it is
        here = here or place
    return here, departed, dest


_ABOUT_LAST = re.compile(r"(?i)\b(?:the|this) (?:carrier|strike group|csg|flattop|ship)\b|\bthe group\b|^(?:it|she)\b")


def parse_tracker(html: str, evidence: list | None = None) -> list[dict]:
    """Read the carriers from a Fleet and Marine Tracker without the model.

    USNI groups the tracker by place: a section title ("In the Philippine Sea", "Near Hawaii")
    followed by the ships there, each carrier named with its hull number. Everything the article
    says about each carrier is collected first: every sentence naming it (anywhere in the
    article) and the follow-on sentences in its section about "the strike group" or "the
    carrier". Then, in order: the section title if it names a sea area or port; else a sentence
    giving where it is now ("is operating near Hawaii"); else, as status "departed", the place it
    left. Where a carrier came from or is based ("departed from Apra Harbor, Guam", "homeported at
    Norfolk") is never read as where it is. Photo captions are skipped (they often describe older
    photos from elsewhere). Carriers named without a usable place are returned without
    coordinates, so they still count as listed. `evidence`, if given, collects a note per carrier."""
    blocks = re.findall(r"(?is)<(h[1-6]|p|li)\b([^>]*)>(.*?)</\1>", html or "")
    if not blocks:  # plain text: one block per line
        blocks = [("p", "", line) for line in (html or "").splitlines()]
    mentions = lambda sent: [h for h, n in _HULL_NAMES.items()  # noqa: E731
                             if re.search(rf"\(CVN[- ]?{h[-2:]}\)|\b{n}\b", sent)]
    info: dict[str, dict] = {}  # hull -> {section, current, departed, dest, sentence, text}
    order: list[str] = []
    heading, last = None, None
    for tag, attrs, inner in blocks:
        text = clean_text(inner)
        if not text:
            continue
        if _is_heading(tag, attrs + inner[:40], text):
            heading, last = text, None
            continue
        if "caption" in attrs.lower() or _CAPTION.search(text):
            continue
        # sentence ends: not after initials or abbreviations ("H.W. Bush", "U.S. official", "Sept. 21")
        for sentence in re.split(r"(?<=[a-z0-9)][.!?])\s+(?=[A-Z])", text):
            named = mentions(sentence)
            about = named if named else ([last] if last and _ABOUT_LAST.search(sentence) else [])
            for hull in about:
                if hull not in info:
                    info[hull] = {"section": _tracker_place(heading or ""), "heading": heading, "sentence": sentence,
                                  "current": None, "current_sentence": None, "departed": None, "dest": None, "text": []}
                    order.append(hull)
                c = info[hull]
                here, departed, dest = _clause_places(sentence)
                if here and not c["current"]:
                    c["current"], c["current_sentence"] = here, sentence
                c["departed"] = c["departed"] or departed
                c["dest"] = c["dest"] or dest
                c["text"].append(sentence)
            if len(named) == 1:
                last = named[0]
    out, unplaced = [], []
    for hull in order:
        c = info[hull]
        if c["section"]:
            here, source = c["section"], f"section '{c['heading']}'"
        elif c["current"]:
            here, source = c["current"], f"sentence {c['current_sentence'][:120]!r}"
        elif c["departed"]:
            here, source = c["departed"], "departure point (no current position given)"
        else:
            here, source = None, "nothing"
        if evidence is not None:
            evidence.append(f"{hull}: {here['place'] if here else 'no named place'} (from {source}; "
                            f"first mention {c['sentence'][:120]!r})")
        if not here:
            unplaced.append(hull)
            continue
        low = " ".join(c["text"]).lower()
        status = ("departed" if source.startswith("departure point")
                  else "in port" if re.search(r"in port|pierside|moored|at (?:its|her) homeport", low)
                  else "underway" if re.search(r"underway|transit|sailing|en route|heading|bound for", low)
                  else "operating")
        home = HOME.get(hull)
        at_home = bool(home and haversine_km(home[1], home[2], here["lat"], here["lon"]) < HOME_KM)
        dest = c["dest"] if c["dest"] and haversine_km(c["dest"]["lat"], c["dest"]["lon"], here["lat"], here["lon"]) >= MOVE_KM else None
        out.append({"hull": hull, "status": status, "deployed": not at_home, "maintenance": "maintenance" in low,
                    **here, "heading_to": dest})
    # named without a usable place: still listed (so not sent home), position left as it was
    out += [{"hull": h, "status": "operating", "deployed": True, "place": None, "lat": None, "lon": None}
            for h in unplaced]
    return out


def _article_text(html: str) -> str:
    html = re.sub(r"(?is)<(head|script|style|nav|header|footer|aside|form)[^>]*>.*?</\1>", " ", html)
    text = clean_text(html)
    low = text.lower()
    start = low.find("these are the approximate positions")
    if start < 0:
        start = max(0, low.find("fleet and marine tracker"))
    end = low.find("in addition to these major formations", start)
    return text[start:(end if end > 0 else start + 24000)][:24000]


def _latest_edition(items: list[dict], session) -> dict | None:
    """Newest Fleet and Marine Tracker from USNI's feeds (the Fleet Tracker category, then the main
    feed), with the article text the feed carries; else a tracker headline seen in the news."""
    import feedparser
    best = None
    for feed_url in (FLEET_TRACKER_FEED, USNI_FEED):
        try:
            # the query string asks caches along the way for a fresh copy
            r = session.get(f"{feed_url}?t={int(datetime.now().timestamp()) // 900}", timeout=25)
            r.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            log(f"[fleet] tracker feed {feed_url}: {exc}")
            continue
        for e in feedparser.parse(r.content).entries[:20]:
            title = e.get("title", "")
            st = e.get("published_parsed") or e.get("updated_parsed")
            if "tracker" not in title.lower() or not st or not e.get("link"):
                continue
            t = iso(datetime(*st[:6], tzinfo=UTC))
            if best is None or t > best["time"]:
                content = " ".join(c.get("value", "") for c in e.get("content") or [])
                best = {"url": e["link"], "time": t, "title": title, "html": content}
    if best:
        return best
    editions = [it for it in items if it["platform"] == "rss" and "fleet and marine tracker" in it["text"][:160].lower()]
    if not editions:
        return None
    it = max(editions, key=lambda x: x["time"])
    return {"url": it["url"], "time": it["time"], "title": it["text"][:120]}


def read_tracker(state: dict, items: list[dict], session, settings: dict, now: datetime, ask_json) -> list[dict]:
    """If USNI published a new Fleet and Marine Tracker, turn it into position reports (one model call)."""
    meta = state.setdefault("fleet_meta", {})
    latest = _latest_edition(items, session)
    if not latest:
        return []
    if latest["url"] == meta.get("tracker_url") and meta.get("tracker_parser") == TRACKER_PARSER:
        return []
    if latest["url"] == meta.get("tracker_url"):
        # re-reading with a changed parser: drop what the old reading placed, so nothing it got wrong stays
        fleet = state.setdefault("fleet", {})
        for hull in [h for h, c in fleet.items() if str(c.get("source", "")).startswith("USNI News Fleet")]:
            fleet.pop(hull)
        log("[fleet] re-reading the tracker with the updated reader")
    # USNI refuses GitHub's servers (HTTP 403) for article pages, so the text the feed carries is
    # used first; the page is opened only when the feed has none.
    html = latest.get("html") or ""
    if len(clean_text(html)) < 1500:
        try:
            r = session.get(latest["url"], timeout=30)
            r.raise_for_status()
            html = r.text
        except Exception as exc:  # noqa: BLE001
            log(f"[fleet] could not open the tracker article ({latest['time'][:10]}) and the feed has no text: {exc}")
            return []
    log(f"[fleet] reading the Fleet and Marine Tracker of {latest['time'][:10]}")
    evidence: list[str] = []
    parsed = parse_tracker(html, evidence)
    if sum(c["lat"] is not None for c in parsed) >= 2:
        out = {"carriers": parsed}  # read directly: no model call, works while the model is down
        log(f"[fleet] read {len(parsed)} carriers from the tracker directly:")
        for line in evidence:
            log(f"[fleet]   {line}")
    else:
        out = ask_json(TRACKER_PROMPT, _article_text(html), state, settings, now, purpose="fleet")
    if not isinstance(out, dict) or not isinstance(out.get("carriers"), list):
        log("[fleet] tracker article could not be read this run; will retry")
        return []
    meta["tracker_url"] = latest["url"]
    meta["tracker_parser"] = TRACKER_PARSER
    meta["tracker_time"] = latest["time"]
    meta["tracker_hulls"] = sorted({h for h in (_hull(c.get("hull")) for c in out["carriers"] if isinstance(c, dict)) if h})
    status_map = {"deployed": "operating", "in maintenance": "in port"}  # older model replies may still say "deployed"
    reports = []
    for c in out["carriers"]:
        if not isinstance(c, dict):
            continue
        try:
            lat, lon = float(c["lat"]), float(c["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        heading = c.get("heading_to") if isinstance(c.get("heading_to"), dict) else None
        if heading:
            try:
                heading = {"place": heading.get("place"), "lat": float(heading["lat"]), "lon": float(heading["lon"])}
            except (KeyError, TypeError, ValueError):
                heading = None
        raw_status = c.get("status", "operating")
        reports.append({
            "hull": c.get("hull"), "status": status_map.get(raw_status, raw_status),
            "deployed": bool(c.get("deployed", raw_status == "deployed")), "maintenance": raw_status == "in maintenance",
            "place": c.get("place"), "lat": lat, "lon": lon, "heading_to": heading,
            "time": latest["time"], "source": "USNI News Fleet and Marine Tracker", "url": latest["url"],
            "trusted": True,
        })
    log(f"[fleet] tracker: {len(reports)} carriers")
    return reports


def apply_home_baseline(state: dict, now: datetime | None = None) -> None:
    """Carriers the latest tracker does not list as deployed are shown at home port.

    USNI's tracker lists every deployed carrier strike group, so absence means the carrier is
    in home waters (in port, in maintenance, or training locally). A carrier reported somewhere
    by news more recently than the tracker keeps that newer position.
    """
    fleet = state.setdefault("fleet", {})
    meta = state.get("fleet_meta") or {}
    listed = set(meta.get("tracker_hulls") or [])
    tracker_time = meta.get("tracker_time")
    if tracker_time and now and now - datetime.fromisoformat(tracker_time.replace("Z", "+00:00")) > TRACKER_MAX_AGE:
        listed, tracker_time = set(), None  # too old to say who is home now
    reports = []
    for hull, (place, lat, lon) in HOME.items():
        c = fleet.get(hull)
        if hull in listed:
            if c and c.get("lat") is not None:
                continue
            # listed by the tracker without a position, and nothing stored: show its home port, not nothing
            reports.append({"hull": hull, "status": "home", "place": place, "lat": lat, "lon": lon, "heading_to": None,
                            "trusted": True, "time": "1970-01-01T00:00:00Z",
                            "source": "Home port (USNI's tracker lists it without a position)", "url": meta.get("tracker_url")})
            continue
        if c and c.get("lat") is not None:
            newer_news = tracker_time and (c.get("as_of") or "") > tracker_time and not c.get("at_home")
            # a carrier that just departed its home port is not "at home"
            at_home = haversine_km(c["lat"], c["lon"], lat, lon) < MOVE_KM and c.get("status") not in ("departed", "underway")
            if newer_news or at_home or not tracker_time:
                c["at_home"] = at_home
                continue
        reports.append({"hull": hull, "status": "home", "place": place, "lat": lat, "lon": lon, "heading_to": None,
                        "trusted": True, "time": tracker_time or "1970-01-01T00:00:00Z",
                        "source": "Home port (not listed as deployed in USNI's latest Fleet Tracker)" if tracker_time
                        else "Home port (no position reports yet)",
                        "url": meta.get("tracker_url")})
    update(state, reports)
    for r in reports:
        c = fleet.get(r["hull"])
        if c:
            c["at_home"] = True
            c["deployed"] = False
    for hull in listed:
        if hull in fleet:
            fleet[hull]["at_home"] = False


def public(state: dict, now: datetime) -> list[dict]:
    out = []
    for c in (state.get("fleet") or {}).values():
        if c.get("lat") is None or c.get("hull") not in CARRIERS:
            continue
        row = {k: c.get(k) for k in ("hull", "name", "short", "lat", "lon", "place", "status", "as_of",
                                     "source", "url", "heading_to", "prev", "moved_at", "departed_at", "track",
                                     "deployed", "maintenance", "at_home")}
        row["name"], row["short"] = CARRIERS[c["hull"]]  # names always from the list above
        out.append(row)
    return sorted(out, key=lambda c: c["hull"])


def release_held(state: dict, sources: dict[str, str] | None = None) -> int:
    """Re-check held news reports against each carrier's last tracker position (see _check): one
    held because it was too far from an unconfirmed position is applied once it fits the tracker.
    Carriers stored before the tracker position was kept get it from their track (the entry dated
    with the tracker edition). `sources` names the source of a report URL, for held reports stored
    without one. Returns how many carriers moved."""
    fleet = state.get("fleet") or {}
    tracker_time = (state.get("fleet_meta") or {}).get("tracker_time")
    due = []
    for hull, c in fleet.items():
        if not isinstance(c, dict) or hull not in CARRIERS:
            continue
        if not c.get("last_trusted") and tracker_time:
            t = next((t for t in c.get("track", []) if t.get("time") == tracker_time), None)
            if t:
                c["last_trusted"] = {"lat": t["lat"], "lon": t["lon"], "place": t.get("place"), "as_of": t["time"]}
        lt = c.get("last_trusted")
        if c.get("trusted") or not lt or not c.get("held"):
            continue
        fits = [h for h in c["held"] if h["time"] > (c.get("as_of") or "") and not _too_fast(lt, h)]
        if fits:
            h = max(fits, key=lambda h: h["time"])
            due.append({"hull": hull, "lat": h["lat"], "lon": h["lon"], "place": h.get("place"), "time": h["time"],
                        "url": h.get("url"), "status": "underway",
                        "source": h.get("source") or (sources or {}).get(h.get("url")) or "News report"})
    return update(state, due) if due else 0


def drop_held(state: dict, urls: set[str]) -> None:
    """Forget held position reports whose article was taken down by a correction (an old photo
    must not later confirm a move)."""
    for c in (state.get("fleet") or {}).values():
        if isinstance(c, dict) and c.get("held"):
            c["held"] = [h for h in c["held"] if h.get("url") not in urls]
            if not c["held"]:
                c.pop("held")


def repair(state: dict) -> None:
    """Once per FLEET_VERSION: re-check stored positions against the rules for news reports.
    A carrier whose position fails them goes back to its home-port baseline; a previous position
    that is vague or couldn't have been sailed from is dropped (so no line is drawn from it)."""
    if state.get("fleet_version", 1) >= FLEET_VERSION:
        return
    fleet = state.setdefault("fleet", {})
    for hull, c in list(fleet.items()):
        if hull not in CARRIERS or not _known(c) or str(c.get("source", "")).startswith(("USNI News Fleet", "Home port")):
            continue
        if _vague(c.get("place")) or _other_home(hull, c["lat"], c["lon"]):
            log(f"[fleet] {CARRIERS[hull][0]}: stored position {c.get('place')!r} fails the checks; back to home port")
            fleet.pop(hull)
            continue
        lt = c.get("last_trusted")
        if lt and not c.get("trusted") and _too_fast(lt, c):
            other = _other_there({h: o for h, o in fleet.items() if o is not c}, hull, {**c, "time": c["as_of"]})
            if other:
                log(f"[fleet] {CARRIERS[hull][0]}: stored position {c.get('place')!r} is where {other} was reported, "
                    f"and too far from its last tracker position ({lt.get('place')}); back to that")
                c.update(lat=lt["lat"], lon=lt["lon"], place=lt.get("place"), as_of=lt["as_of"], trusted=True, heading_to=None,
                         source="USNI News Fleet and Marine Tracker", url=(state.get("fleet_meta") or {}).get("tracker_url"),
                         status="in port" if HOME.get(hull) and haversine_km(HOME[hull][1], HOME[hull][2], lt["lat"], lt["lon"]) < HOME_KM else "operating")
                for k in ("prev", "moved_at", "held"):
                    c.pop(k, None)
                c["track"] = [t for t in c.get("track", []) if t.get("time", "") <= lt["as_of"]][-TRACK_LEN:]
                continue
        p = c.get("prev")
        if p and (_vague(p.get("place")) or _too_fast(p, c)):
            for k in ("prev", "moved_at"):
                c.pop(k, None)
        c["track"] = [t for t in c.get("track", []) if not _vague(t.get("place")) and not str(t.get("time")).startswith("1970")][-1:]
        c.pop("held", None)
    state["fleet_version"] = FLEET_VERSION


def mark_deployment(state: dict, reports: list[dict]) -> None:
    """Remember whether the tracker last listed each carrier as deployed or in maintenance."""
    fleet = state.setdefault("fleet", {})
    for r in reports:
        hull = _hull(r.get("hull"))
        if hull in fleet and ("deployed" in r or "maintenance" in r):
            fleet[hull]["deployed"] = bool(r.get("deployed"))
            fleet[hull]["maintenance"] = bool(r.get("maintenance"))
