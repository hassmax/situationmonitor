from datetime import datetime, timezone

import fleet


def test_carrier_names_start_with_uss_even_for_stored_entries():
    state = {"fleet": {"CVN-78": {"hull": "CVN-78", "name": "USS Gerald R. Ford", "short": "Ford", "lat": 36.9, "lon": -76.3}}}
    c = fleet.public(state, datetime(2026, 9, 28, tzinfo=timezone.utc))[0]
    assert (c["name"], c["short"]) == ("U.S.S. Gerald R. Ford", "U.S.S. Ford")
    assert all(name.startswith("U.S.S. ") and short.startswith("U.S.S. ") for name, short in fleet.CARRIERS.values())


def news(hull, place, lat, lon, time, status="operating", url=None, heading=None):
    return {"hull": hull, "status": status, "place": place, "lat": lat, "lon": lon, "heading_to": heading,
            "time": time, "source": "Some outlet", "url": url or f"https://example.com/{place}-{time}"}


def home_state():
    state = {}
    fleet.apply_home_baseline(state)
    return state


def test_vague_place_is_not_a_position():
    state = home_state()
    fleet.update(state, [news("CVN-71", "Middle East", 25.0, 55.0, "2026-09-28T08:45:00Z")])
    assert state["fleet"]["CVN-71"]["place"].startswith("San Diego")


def test_another_carriers_home_port_is_ignored():
    state = home_state()
    fleet.update(state, [news("CVN-78", "San Diego", 32.716, -117.161, "2026-09-27T22:15:00Z", status="departed")])
    assert state["fleet"]["CVN-78"]["place"] == "Norfolk, Va."          # Ford lives in Norfolk
    fleet.update(state, [news("CVN-71", "San Diego", 32.716, -117.161, "2026-09-28T14:13:00Z", status="departed")])
    assert state["fleet"]["CVN-71"]["status"] == "departed"             # Roosevelt does live in San Diego


def test_impossible_jump_waits_for_a_second_report():
    state = home_state()
    fleet.update(state, [news("CVN-71", "Thailand", 13.1, 100.9, "2026-09-27T03:30:00Z")])
    fleet.update(state, [news("CVN-71", "Arabian Sea", 16.0, 63.0, "2026-09-27T12:00:00Z", url="u1")])
    assert state["fleet"]["CVN-71"]["place"] == "Thailand"               # 4,000 km in 9 hours: held
    fleet.update(state, [news("CVN-71", "Arabian Sea", 15.5, 62.0, "2026-09-27T13:00:00Z", url="u2")])
    assert state["fleet"]["CVN-71"]["place"] == "Arabian Sea"            # a second, different report confirms it
    fleet.update(state, [news("CVN-71", "Arabian Sea", 17.0, 64.0, "2026-09-29T13:00:00Z", url="u3")])
    assert state["fleet"]["CVN-71"]["lat"] == 17.0                       # ordinary moves go through at once


def test_repair_resets_misattributed_and_drops_impossible_previous_positions():
    state = {"fleet": {
        "CVN-78": {"hull": "CVN-78", "lat": 32.716, "lon": -117.161, "place": "San Diego", "status": "departed",
                   "as_of": "2026-09-27T22:15:38Z", "source": "San Diego Union-Tribune (via Google News)", "track": []},
        "CVN-71": {"hull": "CVN-71", "lat": 32.716, "lon": -117.161, "place": "San Diego", "status": "departed",
                   "as_of": "2026-09-28T14:13:00Z", "source": "USNI News (via Google News)", "at_home": True,
                   "prev": {"lat": 25.0, "lon": 55.0, "place": "Middle East", "as_of": "2026-09-28T08:45:14Z"},
                   "moved_at": "2026-09-28T10:43:05Z", "track": [{"lat": 25.0, "lon": 55.0, "place": "Middle East",
                                                                   "time": "2026-09-28T08:45:14Z"}]}}}
    fleet.repair(state)
    fleet.apply_home_baseline(state, datetime(2026, 9, 28, 18, tzinfo=timezone.utc))
    ford, tr = state["fleet"]["CVN-78"], state["fleet"]["CVN-71"]
    assert ford["place"] == "Norfolk, Va." and ford["at_home"]
    assert "prev" not in tr and tr["at_home"] is False and tr["status"] == "departed"
    assert state["fleet_version"] == fleet.FLEET_VERSION


def test_an_old_tracker_cannot_say_who_is_home():
    state = home_state()
    fleet.update(state, [news("CVN-72", "Philippine Sea", 20.0, 130.0, "2026-09-27T05:00:00Z", status="underway")])
    state["fleet_meta"] = {"tracker_time": "2026-08-31T12:00:00Z", "tracker_hulls": ["CVN-78"]}
    fleet.apply_home_baseline(state, datetime(2026, 9, 28, tzinfo=timezone.utc))
    assert state["fleet"]["CVN-72"]["place"] == "Philippine Sea"


TRACKER = """<p>These are the approximate positions of the U.S. Navy's deployed carrier strike groups as of Sept. 21, 2026.</p>
<h2>Near Hawaii</h2>
<p>Carrier USS Abraham Lincoln (CVN-72) is operating near Hawaii, a U.S. official told USNI News.</p>
<h2>In the Arabian Sea</h2>
<p>The George H.W. Bush Carrier Strike Group is in the North Arabian Sea. USS George H.W. Bush (CVN-77) arrived last week.</p>
<h2>In the Mediterranean</h2>
<p>USS Gerald R. Ford (CVN-78) is en route to the Eastern Mediterranean after a port visit.</p>
<h2>In the Western Pacific</h2>
<p>USS George Washington (CVN-73) departed Yokosuka for its patrol. It is underway in the Philippine Sea.</p>
<h2>In the Pacific</h2>
<p>USS Nimitz (CVN-68) is in the Pacific.</p>"""


def test_tracker_is_read_without_the_model():
    got = {c["hull"]: c for c in fleet.parse_tracker(TRACKER)}
    assert got["CVN-72"]["place"] == "near Hawaii"
    assert got["CVN-77"]["place"] == "North Arabian Sea"                    # "H.W." is not a sentence end
    assert got["CVN-78"]["heading_to"]["place"] == "Eastern Mediterranean"
    assert got["CVN-73"]["place"] == "Philippine Sea"                        # where it went, not where it left
    assert got["CVN-68"]["lat"] is None                                      # "the Pacific" is not a position,
    reports = [c for c in fleet.parse_tracker(TRACKER) if c["lat"] is not None]  # but Nimitz still counts as listed
    assert len(reports) == 4


def test_tracker_replaces_a_later_news_report_it_could_not_have_sailed_to():
    state = home_state()
    fleet.update(state, [news("CVN-72", "Philippine Sea", 20.0, 131.0, "2026-09-27T05:00:00Z", status="underway")])
    tracker = [{**c, "time": "2026-09-21T12:00:00Z", "source": "USNI News Fleet and Marine Tracker", "url": "t",
                "trusted": True} for c in fleet.parse_tracker(TRACKER) if c["lat"] is not None]
    fleet.update(state, tracker)
    assert state["fleet"]["CVN-72"]["place"] == "near Hawaii"                # 6,500 km in 6 days: the news was wrong
    assert "prev" not in state["fleet"]["CVN-72"]                            # and no line is drawn from it
    # a later news report that fits the tracker stands
    fleet.update(state, [news("CVN-77", "Gulf of Oman", 24.5, 58.5, "2026-09-25T05:00:00Z")])
    assert state["fleet"]["CVN-77"]["place"] == "Gulf of Oman"


def test_vague_report_still_gives_the_destination():
    state = home_state()
    h = {"place": "Middle East", "lat": 25.3, "lon": 55.3}
    fleet.update(state, [news("CVN-71", "Middle East", 25.0, 55.0, "2026-09-28T08:45:00Z", heading=h)])
    c = state["fleet"]["CVN-71"]
    assert c["place"].startswith("San Diego") and c["heading_to"] == h
