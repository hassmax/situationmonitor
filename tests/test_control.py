"""Territorial control: ISW's published shapes, with the source's own date; stale ones left off."""
from datetime import datetime, timedelta, timezone

import control

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
EDITED = int(datetime(2026, 9, 29, 8, 0, tzinfo=timezone.utc).timestamp() * 1000)
SQUARE = [[37.0, 47.0], [38.0, 47.0], [38.0, 48.0], [37.0, 48.0], [37.0, 47.0]]


class Resp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


class Arcgis:
    """A FeatureServer with one polygon layer (id 49) edited on 29 Sept."""
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def get(self, url, timeout=None):
        self.calls.append(url)
        if self.fail:
            raise OSError("unreachable")
        if url.endswith("FeatureServer?f=json"):
            return Resp({"layers": [{"id": 49, "geometryType": "esriGeometryPolygon"}]})
        if url.endswith("/49?f=json"):
            return Resp({"geometryType": "esriGeometryPolygon", "editingInfo": {"lastEditDate": EDITED}})
        if "/49/query?" in url:
            return Resp({"type": "FeatureCollection", "features": [
                {"geometry": {"type": "Polygon", "coordinates": [[[x + 0.00049, y] for x, y in SQUARE]]}},
                {"geometry": {"type": "MultiPolygon", "coordinates": [[SQUARE], [SQUARE[:3]]]}},  # a 3-point ring is dropped
            ]})
        raise AssertionError(url)


LAYER = {"id": "ua-occupied", "label": "Russian-occupied", "style": "occupied", "country": "UA", "max_age_days": 14,
         "url": "https://services5.arcgis.com/x/arcgis/rest/services/VIEW_RussiaCoTinUkraine_V3/FeatureServer",
         "source": "Institute for the Study of War", "link": "https://storymaps.arcgis.com/stories/x"}


def test_a_layer_is_fetched_with_the_sources_own_date_and_published():
    state = {}
    control.update(state, Arcgis(), [LAYER], NOW)
    got = state["control"]["ua-occupied"]
    assert got["as_of"].startswith("2026-09-29") and len(got["polygons"]) == 2
    assert got["polygons"][0][0][0] == [37.0, 47.0]                     # rounded to 3 decimals
    pub = control.public(state, [LAYER], NOW)
    assert [(p["label"], p["source"], p["as_of"][:10]) for p in pub] == [("Russian-occupied", "Institute for the Study of War", "2026-09-29")]
    assert state["health"]["control:ua-occupied"]["ok"]


def test_fetched_at_most_every_three_hours_and_a_failure_keeps_the_last_copy():
    state, server = {}, Arcgis()
    control.update(state, server, [LAYER], NOW)
    n = len(server.calls)
    control.update(state, server, [LAYER], NOW + timedelta(hours=1))
    assert len(server.calls) == n                                          # not due yet
    control.update(state, Arcgis(fail=True), [LAYER], NOW + timedelta(hours=4))
    assert len(state["control"]["ua-occupied"]["polygons"]) == 2           # kept
    assert not state["health"]["control:ua-occupied"]["ok"]


def test_a_stale_layer_is_left_off_the_map_not_shown_as_current():
    state = {}
    control.update(state, Arcgis(), [LAYER], NOW)
    assert control.public(state, [LAYER], NOW + timedelta(days=20)) == []
    static = {**LAYER, "id": "ua-occupied-2014"}
    static.pop("max_age_days")
    control.update(state, Arcgis(), [static], NOW)
    assert len(control.public(state, [static], NOW + timedelta(days=400))) == 1   # areas held before 2022 don't change


def test_bad_config_entries_are_skipped():
    assert control.validate([LAYER, {"id": "x"}, {**LAYER, "id": "y", "style": "guess"}]) == [LAYER]
