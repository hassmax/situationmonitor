"""Shapes traced from a map picture (Yemen): georeferenced by the map's own town dots, traced by
colour, checked against places known to be inside and outside, published as approximate."""
import io
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image, ImageDraw

import control
import mapshapes

POINTS = [[745, 1004, 44.2064, 15.3547], [661, 1041, 42.9545, 14.7978], [411, 573, 39.1925, 21.4858],
          [338, 388, 38.0618, 24.0890], [440, 361, 39.6142, 24.4686]]
SPEC = {"class": "isw_houthi", "points": POINTS, "bbox": [41.5, 12.0, 54.6, 19.2],
        "inside": [[44.2064, 15.3547], [43.76, 16.94], [43.42, 14.36]],
        "outside": [[45.03, 12.80], [45.32, 15.46], [49.12, 14.54]]}
SOL, _ = mapshapes.fit(POINTS)
# Roughly the Houthi-held west of Yemen, as lon/lat corners
HELD = [(42.6, 17.4), (44.4, 17.4), (45.0, 15.9), (44.9, 13.9), (43.3, 12.6), (42.6, 13.0)]


def picture(held=HELD, shift=(0, 0), circle_at=None) -> bytes:
    img = Image.new("RGB", (1638, 2048), (227, 244, 252))  # sea
    d = ImageDraw.Draw(img)
    d.rectangle((300, 200, 1500, 900), fill=(248, 235, 192))  # Saudi sand
    px = [mapshapes.to_px(SOL, lon, lat) for lon, lat in held]
    px = [(x + shift[0], y + shift[1]) for x, y in px]
    d.polygon(px, fill=(249, 226, 225), outline=(230, 40, 50))
    d.text((720, 975), "Sanaa", fill=(0, 0, 0))  # a label inside the fill
    for x, y, _, _ in POINTS:
        x, y = x + shift[0], y + shift[1]
        d.ellipse((x - 6, y - 6, x + 6, y + 6), fill=(255, 255, 255), outline=(20, 20, 20), width=2)
    if circle_at:
        x, y = mapshapes.to_px(SOL, *circle_at)
        d.ellipse((x - 18, y - 18, x + 18, y + 18), fill=(93, 190, 160), outline=(30, 110, 40), width=3)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def test_the_fit_is_tight_and_a_held_area_is_traced_in_place():
    _, rms = mapshapes.fit(POINTS)
    assert rms < 1.5
    got = mapshapes.trace(picture(circle_at=(44.0, 16.5)), SPEC)  # a strike circle inside the fill
    assert got["dots"] == 5 and len(got["polygons"]) == 1
    ring = got["polygons"][0][0]
    lons, lats = [p[0] for p in ring], [p[1] for p in ring]
    assert 42.5 < min(lons) < 42.7 and 44.9 < max(lons) < 45.1 and 12.5 < min(lats) < 12.7 and 17.3 < max(lats) < 17.5
    assert len(got["polygons"][0]) == 1           # no holes: the label and the circle are filled


def test_a_small_shift_of_the_layout_is_followed():
    got = mapshapes.trace(picture(shift=(4, -3)), SPEC)
    ring = got["polygons"][0][0]
    assert 42.5 < min(p[0] for p in ring) < 42.7 and got["dots"] == 5


def test_a_map_that_fails_the_checks_is_not_used():
    wrong = [(42.6, 17.4), (49.5, 17.4), (49.5, 12.5), (42.6, 12.5)]   # takes in Aden and Mukalla
    with pytest.raises(ValueError, match="inside"):
        mapshapes.trace(picture(held=wrong), SPEC)


class Resp:
    def __init__(self, data=None, content=b""):
        self._data, self.content = data, content

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


class MapRoom:
    def __init__(self, maps):
        self.maps, self.images = maps, 0

    def get(self, url, params=None, headers=None, timeout=None):
        if params:
            return Resp([{"id": i, "date_gmt": d, "link": f"https://understandingwar.org/map/{i}/",
                          "title": {"rendered": t},
                          "content": {"rendered": f'<img src="https://understandingwar.org/u/{i}.webp" />'}}
                         for i, d, t, _ in self.maps])
        self.images += 1
        return Resp(content=next(img for i, _, _, img in self.maps if url.endswith(f"/{i}.webp")))


LAYER = {"id": "ye-houthi", "kind": "isw_map", "label": "Houthi-controlled", "style": "occupied", "country": "YE",
         "url": "https://understandingwar.org/analysis/map-room/", "search": "Houthi Attacks in Saudi Arabia",
         "source": "Institute for the Study of War", "link": "https://understandingwar.org/analysis/map-room/",
         "max_age_days": 21, "trace": SPEC}
NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)


def test_the_newest_map_is_traced_published_as_approximate_and_not_traced_twice():
    good = picture()
    site = MapRoom([(7, "2026-10-02T00:58:46", "Houthi Attacks in Saudi Arabia, as of October 1, 2026 at 2:00 PM ET", good)])
    state = {}
    assert control.validate([LAYER]) == [LAYER]
    control.update(state, site, [LAYER], NOW)
    pub = control.public(state, [LAYER], NOW)
    assert len(pub) == 1 and pub[0]["approx"] and pub[0]["link"] == "https://understandingwar.org/map/7/"
    assert pub[0]["as_of"] == "2026-10-01T18:00:00Z" and pub[0]["label"] == "Houthi-controlled"
    control.update(state, site, [LAYER], NOW + timedelta(hours=4))
    assert site.images == 1                                       # same map: not fetched again


def test_a_new_map_with_another_layout_is_skipped_and_the_last_copy_stays():
    good = picture()
    site = MapRoom([(7, "2026-10-02T00:58:46", "Houthi Attacks in Saudi Arabia, as of October 1, 2026", good)])
    state = {}
    control.update(state, site, [LAYER], NOW)
    bad = picture(held=[(42.6, 17.4), (49.5, 17.4), (49.5, 12.5), (42.6, 12.5)])
    site.maps.insert(0, (8, "2026-10-03T00:58:46", "Houthi Attacks in Saudi Arabia, as of October 2, 2026", bad))
    control.update(state, site, [LAYER], NOW + timedelta(hours=4))
    kept = state["control"]["ye-houthi"]
    assert kept["map_id"] == "7" and "8" in kept["failed"]          # older good map kept, new one remembered
    images = site.images
    control.update(state, site, [LAYER], NOW + timedelta(hours=8))
    assert site.images == images                                   # the failed map isn't fetched again
