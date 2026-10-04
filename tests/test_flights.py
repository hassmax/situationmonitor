"""The flight agent (adsb.lol open ADS-B data): notable aircraft, and a log of take-offs and landings
at watched bases for the regional analyst. Flights are not drawn on the map (2026-10-04)."""
from datetime import datetime, timezone

import config
import flights

T0 = datetime(2026, 10, 4, 10, 0, tzinfo=timezone.utc)
FAIRFORD = {"name": "RAF Fairford", "country": "GB", "country_name": "United Kingdom", "lat": 51.682, "lon": -1.790}
UDEID = {"name": "Al Udeid Air Base", "country": "QA", "country_name": "Qatar", "lat": 25.117, "lon": 51.315}
INCIRLIK = {"name": "Incirlik Air Base", "country": "TR", "country_name": "Türkiye", "lat": 37.002, "lon": 35.426}
BASES = [FAIRFORD, UDEID, INCIRLIK]


class Resp:
    def __init__(self, data):
        self.data, self.status_code = data, 200

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


class Session:
    def __init__(self, mil, hijack=()):
        self.mil, self.hijack, self.asked = mil, list(hijack), []

    def get(self, url, **kw):
        self.asked.append(url)
        return Resp({"ac": self.hijack if "sqk" in url else self.mil, "now": self.now * 1000})


def ac(hexid, t, lat, lon, alt, flight="", seen=0.0, r=""):
    return {"hex": hexid, "t": t, "lat": lat, "lon": lon, "alt_baro": alt, "flight": flight, "seen_pos": seen, "r": r, "gs": 300, "track": 270}


def at(minutes):
    return datetime.fromtimestamp(T0.timestamp() + minutes * 60, timezone.utc)


def run(state, aircraft, when, hijack=()):
    s = Session(aircraft, hijack)
    s.now = when.timestamp()
    flights.time.sleep = lambda _s: None
    return flights.update(state, s, {}, when, BASES)


def test_only_notable_aircraft_outside_the_contiguous_us():
    state = {}
    run(state, [ac("a1", "B1", 51.70, -1.80, "ground", "DARK01"), ac("t1", "TEX2", 50.0, 5.0, 3000),
                ac("c1", "C17", 35.0, -100.0, 30000, "RCH123"),             # over Kansas: left out
                ac("v1", "B752", 38.9, -77.0, 20000, "SAM46"),                # VIP jet inside the US: left out
                ac("v2", "B752", 50.0, 4.4, 20000, "SAM46"),                  # abroad: a government flight
                ac("e4", "B742", 41.0, -96.0, 12000, "CLUB22", r="73-1676"),   # E-4B: kept over the US
                ac("n1", "K35R", 50.0, 10.0, None, seen=500)], T0,           # stale position: skipped
        hijack=[ac("h1", "A320", 41.0, 29.0, 9000, "THY1")])
    got = state["flights"]["aircraft"]
    assert set(got) == {"a1", "v2", "e4", "h1"}
    assert got["v2"]["role"] == "government" and got["v2"]["label"] == "Boeing 757 (government VIP flight)"
    assert got["e4"]["role"] == "command" and got["e4"]["label"] == "E-4B airborne command post"
    assert got["h1"]["role"] == "emergency"


def test_take_off_and_landing_become_one_plain_movement():
    state = {}
    run(state, [ac("k1", "K35R", 25.12, 51.33, 2000, "PAWN54", r="59-1519")], at(0))      # climbing out of Al Udeid
    run(state, [ac("k1", "K35R", 28.5, 47.0, 31000, "PAWN54", r="59-1519")], at(30))
    move = flights.for_analyst(state, at(30))["movements"][0]
    assert move["text"].startswith("KC-135 tanker (PAWN54, 59-1519) took off from Al Udeid Air Base, Qatar, at 4 Oct 10:00 UTC; last reported at 4 Oct 10:30 UTC,")
    assert "km northwest of the base at 31,000 ft, no landing seen yet." in move["text"]
    assert move["label"] == "KC-135 tanker · Al Udeid Air Base" and move["url"] == "https://adsb.lol/?icao=k1"
    run(state, [ac("k1", "K35R", 36.95, 35.45, 3000, "PAWN54", r="59-1519")], at(180))     # landing at Incirlik
    log = flights.for_analyst(state, at(180))["movements"]
    assert len(log) == 1 and log[0]["id"] == move["id"]
    # lost from view for 2.5 hours (no receivers), then seen low at Incirlik: it landed there
    assert "it was next seen low at Incirlik Air Base, Türkiye, at 4 Oct 13:00 UTC, having landed there unseen." in log[0]["text"]
    state = {}
    for m, (lat, lon, alt) in enumerate([(25.12, 51.33, 2000), (28.5, 47.0, 31000), (33.0, 40.0, 31000), (36.95, 35.45, 3000)]):
        run(state, [ac("k2", "K35R", lat, lon, alt, "PAWN55")], at(m * 45))
    text = flights.for_analyst(state, at(135))["movements"][0]["text"]
    assert "took off from Al Udeid Air Base, Qatar, at 4 Oct 10:00 UTC and landed at Incirlik Air Base, Türkiye, at 4 Oct 12:15 UTC." in text


def test_first_seen_climbing_away_from_a_base_counts_as_a_take_off():
    state = {}
    run(state, [ac("b1", "B52", 52.3, -2.9, 18000, "DOOM11")], at(0))     # ~90 km from Fairford, climbing
    run(state, [ac("b1", "B52", 53.5, -6.0, 33000, "DOOM11")], at(15))
    text = flights.for_analyst(state, at(15))["movements"][0]["text"]
    assert "took off from RAF Fairford" in text and "first seen climbing" in text


def test_aircraft_only_passing_by_are_not_logged_and_old_entries_go():
    state = {}
    run(state, [ac("p1", "C17", 50.0, 10.0, 34000, "RCH1")], at(0))
    run(state, [ac("p1", "C17", 50.5, 12.0, 34000, "RCH1")], at(15))
    assert flights.for_analyst(state, at(15))["movements"] == []
    assert flights.for_analyst(state, at(15))["aircraft"][0]["role"] == "airlift"
    run(state, [ac("k1", "K35R", 25.12, 51.33, 2000)], at(20))
    run(state, [ac("k1", "K35R", 28.5, 47.0, 31000)], at(35))
    assert len(state["flights"]["log"]) == 1
    run(state, [], at(35 + flights.LOG_HOURS * 60 + 60))
    assert state["flights"]["log"] == {} and state["flights"]["aircraft"] == {}


def test_watched_bases_load():
    bases = config.load().flight_bases
    assert any(b["name"] == "RAF Fairford" for b in bases) and all({"lat", "lon", "country_name"} <= set(b) for b in bases)
