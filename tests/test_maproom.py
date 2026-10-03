"""ISW's Map Room: new maps are listed hourly, read by the model under their own share, and what
they report becomes ordinary items credited to ISW."""
from datetime import datetime, timedelta, timezone

import extract
from sources import maproom

NOW = datetime(2026, 10, 3, 3, 0, tzinfo=timezone.utc)
UP = "https://understandingwar.org/wp-content/uploads/2026/10/"


def entry(i, title, slug, image, when="2026-10-03T01:33:07", classes=()):
    srcset = ", ".join(f"{UP}{image}{suffix}.webp {w}w" for suffix, w in
                       (("", 1795), ("-240x300", 240), ("-1638x2048", 1638), ("-819x1024", 819)))
    return {"id": i, "date_gmt": when, "link": f"https://understandingwar.org/map/{slug}/",
            "title": {"rendered": title}, "class_list": list(classes),
            "content": {"rendered": f'<img src="{UP}{image}.webp" srcset="{srcset}" />'}}


LISTING = [
    entry(1, "Assessed Control of Terrain in Taiz and Lahij Governorates As of  October 2, 2026 at 2:00 PM ET",
          "taiz-oct-2", "Taiz-October-2-2026", classes=["team-middle-east"]),
    entry(2, "Assessed Control of Terrain in the Sumy Direction, October 2, 2026 at 1:30 PM ET",
          "sumy-oct-2", "Sumy-October-2-2026", "2026-10-03T00:42:21", ["focus-area-ukraine"]),
    entry(3, "Militancy in Sistan and Baluchistan Province Between September 1, 2026 at 2:00 PM ET and "
             "October 2, 2026 at 2:00 PM ET", "sistan", "Sistan-October-2-2026"),
    entry(4, "Assessed Control of Terrain in Taiz and Lahij Governorates as of September 30, 2026 at 2:00 PM ET",
          "taiz-sep-30", "Taiz-September-30-2026", "2026-10-01T01:00:00", ["team-middle-east"]),
]


class Resp:
    def __init__(self, data=None, content=b"", ctype="application/json"):
        self._data, self.content, self.headers = data, content, {"content-type": ctype}

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


class Site:
    def __init__(self, listing=LISTING):
        self.listing, self.calls = listing, []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(url)
        if url == maproom.API:
            return Resp(self.listing)
        if url.endswith(".webp"):
            return Resp(content=b"img:" + url.encode(), ctype="image/webp")
        raise AssertionError(url)


class Model:
    def __init__(self, reply):
        self.reply, self.asked = reply, []

    def __call__(self, system, text, state, settings, now, max_tokens=4000, purpose="other", images=None):
        self.asked.append({"text": text, "purpose": purpose, "images": images})
        return self.reply


def test_titles_say_which_maps_are_read_and_in_what_order():
    taiz = LISTING[0]["title"]["rendered"]
    assert maproom.skip_reason(taiz) is None
    assert maproom.series(taiz) == "assessed control of terrain in taiz and lahij governorates"
    assert maproom.skip_reason(LISTING[2]["title"]["rendered"]) == "covers 32 days"
    assert maproom.skip_reason("Key Developments in Russian-Occupied Ukraine September 24 to September 30, 2026") == "covers 7 days"
    assert maproom.skip_reason("Settlements on the Dnipro Face a Humanitarian Crisis, September 2026").startswith("no day")
    assert maproom.skip_reason("Iranian Campaign Between September 29, 2026 at 2:00 PM ET and September 30, 2026") is None
    assert maproom.priority(taiz, []) == 0
    assert maproom.priority("Assessed Control of Terrain in the Russo-Ukrainian War, October 2, 2026", ["focus-area-ukraine"]) == 1
    assert maproom.priority("Assessed Control of Terrain in the Sumy Direction, October 2, 2026", ["focus-area-ukraine"]) == 3


def test_new_maps_are_queued_once_and_the_list_is_checked_hourly():
    state, health, site = {}, {}, Site()
    maproom.check(state, site, health, NOW)
    queued = [m["id"] for m in state["maproom"]["queue"]]
    assert queued == ["1", "2"]                                   # Sistan covers a month; Sept 30 is over 36 h old
    assert state["maproom"]["queue"][0]["image"].endswith("Taiz-October-2-2026-1638x2048.webp")
    assert health["maproom"]["ok"]
    maproom.check(state, site, health, NOW + timedelta(minutes=30))
    assert len(site.calls) == 1                                   # not again within the hour
    maproom.check(state, site, health, NOW + timedelta(hours=1))
    assert [m["id"] for m in state["maproom"]["queue"]] == ["1", "2"]   # nothing queued twice


def test_findings_become_items_credited_to_isw_with_the_map_link():
    state, site = {}, Site()
    maproom.check(state, site, {}, NOW)
    model = Model({"findings": [
        {"text": "ISW assesses Houthi forces attacked government positions near Kirsh.", "place": "Kirsh",
         "country": "YE", "date": "2026-10-02"},
        {"text": "no place given"},
    ]})
    items = maproom.read(state, site, {}, NOW, model, budget=1)
    assert len(model.asked) == 1 and model.asked[0]["purpose"] == "maproom"
    assert model.asked[0]["images"] == ["data:image/webp;base64,"
                                        + __import__("base64").b64encode(b"img:" + f"{UP}Taiz-October-2-2026-1638x2048.webp".encode()).decode()]
    assert len(items) == 1
    it = items[0]
    assert it["source"] == "ISW Map Room" and it["platform"] == "maproom" and it["side"] is None
    assert it["url"] == "https://understandingwar.org/map/taiz-oct-2/"
    assert "Kirsh" in it["text"] and "Date: 2026-10-02" in it["text"] and it["weight"] == 4
    assert [m["id"] for m in state["maproom"]["queue"]] == ["2"]   # Yemen first; Sumy waits for budget


def test_a_control_map_is_compared_with_its_previous_edition():
    state, site = {}, Site()
    state["maproom"] = {"series": {"assessed control of terrain in taiz and lahij governorates": {
        "id": "4", "title": "Taiz, September 30", "image": f"{UP}Taiz-September-30-2026-1638x2048.webp",
        "date": "2026-10-01T01:00:00Z"}}}
    maproom.check(state, site, {}, NOW)
    model = Model({"findings": []})
    maproom.read(state, site, {}, NOW, model, budget=1)
    assert len(model.asked[0]["images"]) == 2 and "previous edition" in model.asked[0]["text"]
    assert state["maproom"]["series"]["assessed control of terrain in taiz and lahij governorates"]["id"] == "1"


def test_no_budget_means_no_calls_and_a_failed_reply_is_retried_once():
    state, site = {}, Site()
    maproom.check(state, site, {}, NOW)
    model = Model(None)
    assert maproom.read(state, site, {}, NOW, model, budget=0) == [] and not model.asked
    maproom.read(state, site, {}, NOW, model, budget=3)
    assert len(model.asked) == 1 and state["maproom"]["queue"][0]["tries"] == 1   # stops after a failure
    maproom.read(state, site, {}, NOW, model, budget=3)
    assert [m["id"] for m in state["maproom"]["queue"]] == ["2"]                  # given up after two
    assert maproom.read(state, site, {}, NOW + timedelta(hours=40), model, budget=3) == []
    assert state["maproom"]["queue"] == []                                        # too old to read now


def test_the_maproom_share_is_not_paced_and_images_go_with_the_question(monkeypatch):
    settings = {"daily_llm_calls": 470, "max_calls_per_run": 12}
    state = {"llm_calls": {"date": "2026-10-03", "count": 0, "by": {}}}
    assert extract.share_left(state, settings, NOW, "maproom") == 16           # all of it, at 03:00
    assert extract.share_left(state, settings, NOW, "dedupe") < 140
    sent = {}

    class R:
        status_code, headers, text = 200, {}, ""

        def json(self):
            return {"choices": [{"message": {"content": '{"findings": []}'}}]}

    def post(url, body, token):
        sent.update(body)
        return R()

    monkeypatch.setenv("LLM_API_KEY", "x")
    monkeypatch.setattr(extract, "_post", post)
    state["llm_model"] = {"url": "u", "model": "m", "json_mode": True}
    assert extract.ask_json("sys", "q", state, settings, NOW, purpose="maproom", images=["data:image/webp;base64,AA"]) == {"findings": []}
    parts = sent["messages"][1]["content"]
    assert parts[0] == {"type": "text", "text": "q"} and parts[1]["image_url"]["url"].startswith("data:image/webp")
    assert state["llm_calls"]["by"]["maproom"] == 1
