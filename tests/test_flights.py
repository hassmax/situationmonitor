"""2026-10-04: the flight agent (adsb.lol open ADS-B data): notable aircraft, tracks, and group
take-offs and landings at watched bases as reports."""
from datetime import datetime, timezone

import config
import flights

T0 = datetime(2026, 10, 4, 10, 0, tzinfo=timezone.utc)
FAIRFORD = {"name": "RAF Fairford", "country": "GB", "country_name": "United Kingdom", "lat": 51.682, "lon": -1.790}


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


def ac(hexid, t, lat, lon, alt, flight="", seen=1.0, r=""):
    return {"hex": hexid, "t": t, "lat": lat, "lon": lon, "alt_baro": alt, "flight": flight, "seen_pos": seen, "r": r, "gs": 300, "track": 270}


def run(state, aircraft, at, hijack=()):
    s = Session(aircraft, hijack)
    s.now = at.timestamp()
    flights.time.sleep = lambda _s: None
    return flights.update(state, s, {}, at, [FAIRFORD])


def test_only_notable_aircraft_outside_the_contiguous_us():
    state = {}
    run(state, [ac("a1", "B1", 51.70, -1.80, "ground", "DARK01"), ac("t1", "TEX2", 50.0, 5.0, 3000),
                ac("c1", "C17", 35.0, -100.0, 30000, "RCH123"),            # over Kansas: left out
                ac("v1", "B752", 38.9, -77.0, 20000, "SAM46"),               # VIP jet inside the US: left out
                ac("v2", "B752", 50.0, 4.4, 20000, "SAM46"),                 # abroad: a government flight
                ac("e4", "B742", 41.0, -96.0, 12000, "CLUB22", r="73-1676"),  # E-4B: kept over the US
                ac("n1", "K35R", 50.0, 10.0, None, seen=500)], T0,          # stale position: skipped
        hijack=[ac("h1", "A320", 41.0, 29.0, 9000, "THY1")])
    got = state["flights"]["aircraft"]
    assert set(got) == {"a1", "v2", "e4", "h1"}
    assert got["v2"]["role"] == "government" and got["v2"]["op"].startswith("US Air Force Special Air Mission")
    assert got["v2"]["label"] == "Boeing 757 (government VIP flight)"
    assert got["e4"]["role"] == "command" and got["e4"]["label"] == "E-4B airborne command post"
    assert got["h1"]["role"] == "emergency" and "7500" in got["h1"]["label"]
    pub = flights.public(state, T0)
    assert [f["hex"] for f in pub["aircraft"]][:1] == ["h1"]  # most notable first
    assert "ODbL" in pub["attribution"]


def test_group_take_off_becomes_one_report_and_grows():
    state = {}
    at = T0
    run(state, [ac("a1", "B1", 51.69, -1.79, "ground", "DARK01"), ac("a2", "B1", 51.68, -1.80, 2000, "DARK02")], at)
    later = datetime.fromtimestamp(at.timestamp() + 1800, timezone.utc)
    items = run(state, [ac("a1", "B1", 53.0, -8.0, 30000, "DARK01"), ac("a2", "B1", 53.2, -8.5, 31000, "DARK02")], later)
    assert len(items) == 1
    it = items[0]
    assert it["text"].startswith("Flight tracking (ADS-B transponder data, adsb.lol): 2 B-1B bombers took off from RAF Fairford, United Kingdom, at 09:59 UTC")
    assert "DARK01, DARK02" in it["text"] and it["url"] == "https://adsb.lol/?icao=a1,a2"
    assert it["group"] == "adsb" and it["kind"] == "osint" and not it["prefilter"] and it["platform"] == "adsb"
    # the same pair is not reported again
    again = datetime.fromtimestamp(at.timestamp() + 2700, timezone.utc)
    assert run(state, [ac("a1", "B1", 54.0, -10.0, 30000, "DARK01"), ac("a2", "B1", 54.2, -10.5, 31000, "DARK02")], again) == []


def test_tankers_need_three_and_landings_count():
    state = {}
    far = [ac(f"k{i}", "K35R", 49.0, 2.0 + i, 25000, f"QID{i}") for i in range(3)]
    run(state, far, T0)
    later = datetime.fromtimestamp(T0.timestamp() + 3600, timezone.utc)
    near = [ac(f"k{i}", "K35R", 51.70, -1.78, 3000, f"QID{i}") for i in range(3)]
    items = run(state, near, later)
    assert len(items) == 1 and "3 KC-135 tankers landed at RAF Fairford" in items[0]["text"]


def test_old_positions_are_dropped_and_aircraft_leave_the_map():
    state = {}
    run(state, [ac("a1", "B52", 51.7, -1.8, 20000)], T0)
    assert flights.public(state, T0)["aircraft"]
    later = datetime.fromtimestamp(T0.timestamp() + 2 * 3600, timezone.utc)
    assert flights.public(state, later)["aircraft"] == []            # not seen for 2 hours: off the map
    run(state, [], datetime.fromtimestamp(T0.timestamp() + 13 * 3600, timezone.utc))
    assert state["flights"]["aircraft"] == {}                         # older than TRACK_HOURS: forgotten


def test_watched_bases_load():
    bases = config.load().flight_bases
    assert any(b["name"] == "RAF Fairford" for b in bases) and all({"lat", "lon", "country_name"} <= set(b) for b in bases)
