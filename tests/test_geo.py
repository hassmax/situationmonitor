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
