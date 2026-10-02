"""Placing events: when the model's coordinates and the map lookup disagree."""
import geo

BAIDOA = [3.1167, 43.65]


class Resp:
    def __init__(self, data):
        self.status_code, self._data = 200, data

    def json(self):
        return self._data


class Session:
    """Nominatim stand-in: search finds Baidoa; reverse knows Somalia's rough box, else sea."""
    def __init__(self):
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append(url)
        if url == geo.NOMINATIM_REVERSE:
            inside = -2 <= params["lat"] <= 12 and 41 <= params["lon"] <= 51.5
            return Resp({"address": {"country_code": "so"}} if inside else {"error": "Unable to geocode"})
        return Resp([{"lat": str(BAIDOA[0]), "lon": str(BAIDOA[1])}])


import pytest


@pytest.fixture(autouse=True)
def no_wait(monkeypatch):
    monkeypatch.setattr(geo.time, "sleep", lambda s: None)


def geocoder():
    return geo.Geocoder({}, Session(), budget=10)


def test_model_coordinates_at_sea_lose_to_the_lookup():
    assert geocoder().locate("Baidoa", None, "SO", (3.11, 63.65)) == BAIDOA


def test_model_coordinates_inside_the_country_still_win_a_disagreement():
    # 400 km away but still in Somalia: the name may be ambiguous, so the model's estimate stays
    assert geocoder().locate("Baidoa", None, "SO", (6.5, 45.0)) is None


def test_stored_approximate_pins_are_repaired_once():
    g, state = geocoder(), {}
    events = [{"id": "x", "approx": True, "place": "Baidoa", "country": "SO", "lat": 3.11, "lon": 63.65,
               "summary": "Fighting in Baidoa."}]
    assert geo.repair(events, g, state) == 1
    assert (events[0]["lat"], events[0]["lon"], events[0]["approx"]) == (BAIDOA[0], BAIDOA[1], False)
    calls = len(g.session.calls)
    events[0].update(lat=3.11, lon=63.65, approx=True)
    assert geo.repair(events, g, state) == 0 and len(g.session.calls) == calls  # done once per version


def test_repair_waits_when_the_lookup_is_unreachable():
    class Down:
        def get(self, *a, **k):
            raise OSError("unreachable")
    state = {}
    events = [{"id": "x", "approx": True, "place": "Baidoa", "country": "SO", "lat": 3.11, "lon": 63.65,
               "summary": "Fighting in Baidoa."}]
    assert geo.repair(events, geo.Geocoder({}, Down(), budget=10), state) == 0
    assert state["geo_repair"]["done"] == []  # not marked as checked


def test_forces_sent_to_a_command_go_to_its_region_not_its_headquarters():
    jets = {"id": "j", "type": "arms_transfer", "place": "CENTCOM", "country": "US", "lat": 27.86, "lon": -82.49,
            "summary": "US transfers six F-16C fighter jets from Aviano Airbase to CENTCOM operations.",
            "transfer": {"supplier": "US", "recipient": "US", "from": {"place": "Aviano Airbase", "lat": 46.03,
                                                                       "lon": 12.6}, "to": None}}
    statement = {"id": "s", "type": "naval", "place": "US Central Command", "country": "US", "lat": 27.86,
                 "lon": -82.49, "summary": "CENTCOM says it intercepted a drone boat."}
    other = {"id": "o", "type": "arms_transfer", "place": "Rzeszow", "country": "PL", "lat": 50.1, "lon": 22.0,
             "summary": "Weapons delivered to Ukraine via Rzeszow.",
             "transfer": {"supplier": "US", "recipient": "UA", "from": None, "to": {"place": "Rzeszow", "lat": 50.1, "lon": 22.0}}}
    assert geo.pin_commands([jets, statement, other]) == 3
    assert (jets["place"], jets["approx"]) == ("Middle East (CENTCOM area)", True)
    assert jets["transfer"]["to"]["place"] == "Middle East (CENTCOM area)" and jets["transfer"]["to"]["region"]
    assert statement["place"] == "Middle East (CENTCOM area)"
    assert other["place"] == "Rzeszow" and other["transfer"]["to"]["place"] == "Rzeszow"
    assert geo.pin_commands([jets, statement, other]) == 0  # done once


def test_forces_returning_from_a_command_are_a_movement_out_of_its_region():
    kc = {"id": "k", "type": "deployment", "place": "Eielson Air Force Base", "country": "US", "lat": 64.67,
          "lon": -147.08, "summary": "Four KC-135s from the Alaska Air National Guard are returning home from CENTCOM bases."}
    drill = {"id": "d", "type": "deployment", "place": "Eielson Air Force Base", "country": "US", "lat": 64.67,
             "lon": -147.08, "summary": "Red Flag Alaska exercise begins with allied aircraft."}
    assert geo.pin_commands([kc, drill]) == 1
    t = kc["transfer"]
    assert kc["type"] == "arms_transfer" and t["supplier"] == t["recipient"] == "US" and t["mode"] == "air"
    assert t["from"]["place"] == "Middle East (CENTCOM area)" and t["from"]["region"]
    assert t["to"]["place"] == "Eielson Air Force Base" and drill["type"] == "deployment"
    assert geo.pin_commands([kc, drill]) == 0


MIDEAST = [{"id": "mideast", "camera": {"lat": 28.5, "lng": 45, "altitude": 1.25}, "iso2": ["IR", "IL"]}]


def record(place, lat=None, lon=None, type_="missile_drone", country=None):
    return {"item": {"time": "2026-09-29T19:26:20Z", "source": "s", "platform": "rss", "kind": "news",
                     "group": "g", "url": "u"}, "place": place, "admin1": None, "country": country, "lat": lat,
            "lon": lon, "type": type_, "theater": "mideast", "severity": 2, "summary": "s", "claim": "report",
            "killed": None, "injured": None}


def test_a_region_name_goes_to_its_anchor_not_a_lookup():
    class Baltimore(Session):  # Nominatim's "Middle East" is a Baltimore neighborhood
        def get(self, url, params=None, timeout=None):
            return Resp([{"lat": "39.3014", "lon": "-76.5888"}])
    g = geo.Geocoder({}, Baltimore(), budget=10)
    c = geo.place_record(record("Middle East"), g, MIDEAST)
    assert (c["lat"], c["lon"], c["approx"]) == (29.0, 45.0, True)
    assert g.session.calls == []


def test_a_lookup_far_outside_the_theater_is_not_used_for_a_strike():
    class Faraway(Session):
        def get(self, url, params=None, timeout=None):
            return Resp([{"lat": "39.3", "lon": "-76.6"}])
    c = geo.place_record(record("Al Asad", lat=33.8, lon=42.4), geo.Geocoder({}, Faraway(), budget=10), MIDEAST)
    assert (c["lat"], c["lon"], c["approx"]) == (33.8, 42.4, True)   # the model's estimate, in the theater


def test_stored_events_at_a_region_name_move_to_its_anchor():
    e = {"id": "r", "type": "missile_drone", "place": "Middle East", "lat": 39.3, "lon": -76.59, "summary": "s"}
    assert geo.pin_commands([e]) == 1 and (e["lat"], e["lon"], e["approx"]) == (29.0, 45.0, True)


def test_the_indo_pacific_reaches_the_arabian_sea_and_karachi():
    class ArabianSea(Session):
        def get(self, url, params=None, timeout=None):
            return Resp([{"lat": "20.0", "lon": "65.0"}])
    import config
    theaters = config.load().theaters
    rec = dict(record("Arabian Sea", type_="naval"), theater="indopac")
    c = geo.place_record(rec, geo.Geocoder({}, ArabianSea(), budget=10), theaters)
    assert (c["lat"], c["lon"]) == (20.0, 65.0)


def test_model_coordinates_far_outside_the_theater_are_not_used_when_the_lookup_finds_nothing():
    # 1c1b73283412: "Caribbean Sea", country Cuba; the lookup (inside Cuba) found nothing and the
    # model's 15, 75 (a lost minus sign: southern India) was used unchecked
    class Nothing(Session):
        def get(self, url, params=None, timeout=None):
            return Resp([])
    import config
    theaters = config.load().theaters
    rec = dict(record("Caribbean Sea", lat=15.0, lon=75.0, type_="naval", country="CU"), theater="latam")
    c = geo.place_record(rec, geo.Geocoder({}, Nothing(), budget=10), theaters)
    assert (c["lat"], c["lon"], c["approx"]) == (15.0, -75.0, True)  # the named sea's anchor
    # a strike with no usable place at all is left off rather than pinned far away
    rec = dict(record("Somewhere", lat=15.0, lon=75.0, country="CU"), theater="latam")
    assert geo.place_record(rec, geo.Geocoder({}, Nothing(), budget=10), theaters) is None


def test_an_event_in_a_named_sea_is_pinned_near_it_new_and_stored():
    # a deployment is not held to its theater, but "Caribbean Sea" still can't be in India
    class Nothing(Session):
        def get(self, url, params=None, timeout=None):
            return Resp([])
    rec = dict(record("Caribbean Sea", lat=15.0, lon=75.0, type_="deployment", country="CU"))
    c = geo.place_record(rec, geo.Geocoder({}, Nothing(), budget=10), MIDEAST)
    assert (c["lat"], c["lon"]) == (15.0, -75.0)
    stored = {"id": "c", "type": "naval", "place": "Caribbean Sea", "lat": 15.0, "lon": 75.0, "summary": "s"}
    assert geo.pin_commands([stored]) == 1 and (stored["lat"], stored["lon"]) == (15.0, -75.0)
    near = {"id": "h", "type": "naval", "place": "Red Sea off Hodeidah", "lat": 14.8, "lon": 42.9, "summary": "s"}
    assert geo.pin_commands([near]) == 0  # a spot in the sea, not its anchor, stays
