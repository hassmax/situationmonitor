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
<p class="wp-caption-text">USS Abraham Lincoln (CVN-72) in the Arabian Sea, Jan. 2, 2020. U.S. Navy Photo</p>
<h2>In the North Arabian Sea</h2>
<p>The George H.W. Bush Carrier Strike Group is operating in the region. USS George H.W. Bush (CVN-77) departed Naval Station Norfolk, Va., in August.</p>
<p><strong>In the Mediterranean</strong></p>
<p>USS Gerald R. Ford (CVN-78) is en route to the Eastern Mediterranean after a port visit.</p>
<h2>In the Western Pacific</h2>
<p>USS George Washington (CVN-73) departed Yokosuka for its patrol.</p>
<h2>Elsewhere</h2>
<p>USS Theodore Roosevelt (CVN-71) departs San Diego for a patrol in the Philippine Sea.</p>
<h2>In the Pacific</h2>
<p>USS Nimitz (CVN-68) is in the Pacific.</p>"""


def test_tracker_is_read_without_the_model():
    notes = []
    got = {c["hull"]: c for c in fleet.parse_tracker(TRACKER, notes)}
    assert got["CVN-72"]["place"] == "near Hawaii"                  # the section, not the old photo caption
    assert got["CVN-77"]["place"] == "North Arabian Sea"            # the section, not the port it left
    assert got["CVN-78"]["place"] == "Mediterranean Sea"            # a bold paragraph works as a section title
    assert got["CVN-78"]["heading_to"]["place"] == "Eastern Mediterranean"
    assert got["CVN-73"]["place"] == "Western Pacific"
    assert (got["CVN-71"]["place"], got["CVN-71"]["status"]) == ("San Diego", "departed")  # last known point: departed
    assert got["CVN-68"]["lat"] is None                             # "the Pacific" is not a position,
    assert len([c for c in got.values() if c["lat"] is not None]) == 5  # but Nimitz still counts as listed
    assert any(n.startswith("CVN-72: near Hawaii (from section") for n in notes)


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



REAL = """<h2>In the Pacific</h2>
<p>The Abraham Lincoln Carrier Strike Group departed last Sunday from Apra Harbor, Guam, and is transiting the Pacific Ocean en route to California.</p>
<h2>In the Arabian Sea</h2>
<p>Aircraft carrier USS\xa0 George Washington \xa0(CVN-73), along with embarked Carrier Air Wing (CVW) 5\xa0and USS\xa0 Shoup\xa0 (DDG-86), are operating in the Arabian Sea.</p>
<h2>In the Atlantic</h2>
<p>Carrier \nUSS\xa0 George H. W. Bush \xa0(CVN-77), homeported at Naval Station Norfolk, Va., is conducting sea trials.</p>
<h2>In the Eastern Pacific</h2>
<p>Aircraft carrier USS Theodore Roosevelt\xa0 (CVN-71) departed on deployment Sunday from San Diego, Calif.</p>"""


def test_real_tracker_sentences_28_sept():
    got = {c["hull"]: c for c in fleet.parse_tracker(REAL)}
    lincoln = got["CVN-72"]
    assert (lincoln["place"], lincoln["status"], lincoln["heading_to"]["place"]) == ("Guam", "departed", "California")
    assert got["CVN-73"]["place"] == "Arabian Sea"
    assert got["CVN-77"]["lat"] is None                  # "homeported at Norfolk" is not where it is; "H. W." still matches
    assert got["CVN-71"]["place"] == "Eastern Pacific"


def test_listed_without_position_and_nothing_stored_shows_home_port():
    state = {"fleet_meta": {"tracker_time": "2026-09-28T18:01:35Z", "tracker_hulls": ["CVN-77"]}}
    fleet.apply_home_baseline(state, datetime(2026, 9, 28, 19, tzinfo=timezone.utc))
    assert state["fleet"]["CVN-77"]["place"] == "Norfolk, Va."


LINCOLN = ("<h2>In the Pacific</h2>"
           "<p>The Abraham Lincoln Carrier Strike Group departed last Sunday from Apra Harbor, Guam, and is transiting "
           "the Pacific Ocean en route to California.</p>")


def lincoln(extra):
    c = next(c for c in fleet.parse_tracker(LINCOLN + extra) if c["hull"] == "CVN-72")
    return c["place"], c["status"], (c["heading_to"] or {}).get("place")


def test_near_hawaii_in_the_same_sentence():
    html = LINCOLN.replace("the Pacific Ocean en route", "the Pacific Ocean near Hawaii en route")
    c = next(c for c in fleet.parse_tracker(html) if c["hull"] == "CVN-72")
    assert (c["place"], c["heading_to"]["place"]) == ("near Hawaii", "California")


def test_near_hawaii_in_a_follow_on_sentence_about_the_strike_group():
    assert lincoln("<p>The strike group was operating near Hawaii on Sunday, a Navy official said.</p>") == \
        ("near Hawaii", "underway", "California")


def test_near_hawaii_in_a_later_paragraph_naming_the_carrier():
    assert lincoln("<h2>Elsewhere</h2><p>USS Abraham Lincoln (CVN-72) was near Hawaii on Monday.</p>")[0] == "near Hawaii"


def test_departure_point_only_when_nothing_says_where_it_is():
    assert lincoln("") == ("Guam", "departed", "California")


def test_held_reports_from_taken_down_articles_are_forgotten():
    import fleet as fl
    state = {"fleet": {"CVN-72": {"held": [{"url": "u-old-photo", "time": "2026-09-29T23:40:46Z"},
                                           {"url": "u-other", "time": "2026-09-29T23:50:00Z"}]},
                       "CVN-68": {"held": [{"url": "u-old-photo", "time": "2026-09-29T23:40:46Z"}]}}}
    fl.drop_held(state, {"u-old-photo"})
    assert [h["url"] for h in state["fleet"]["CVN-72"]["held"]] == ["u-other"] and "held" not in state["fleet"]["CVN-68"]


def test_a_report_that_fits_the_tracker_replaces_an_unconfirmed_one_it_contradicts():
    # The tracker put the Bush in the Arabian Sea on 28 Sept; an Iranian channel then put it in the
    # Strait of Hormuz, and ship imagery had it entering the Malacca Strait on 3 Oct: too far from
    # Hormuz, but within reach of the tracker's position, so the Hormuz report is set aside.
    state = home_state()
    tracker = {**news("CVN-77", "Arabian Sea", 16.0, 63.0, "2026-09-28T18:01:35Z"), "trusted": True}
    fleet.update(state, [tracker])
    fleet.update(state, [news("CVN-77", "Strait of Hormuz", 26.57, 56.25, "2026-09-30T21:25:00Z")])
    assert state["fleet"]["CVN-77"]["place"] == "Strait of Hormuz"
    fleet.update(state, [news("CVN-77", "Strait of Malacca", 2.5, 101.5, "2026-10-03T13:28:00Z")])
    c = state["fleet"]["CVN-77"]
    assert c["place"] == "Strait of Malacca" and c["prev"]["place"] == "Arabian Sea"   # line from the tracker
    assert [t["place"] for t in c["track"]][-2:] == ["Arabian Sea", "Strait of Malacca"]   # Hormuz dropped
    # a report too far even from the tracker's position is still held
    fleet.update(state, [news("CVN-77", "Norfolk area", 37.5, -70.0, "2026-10-04T00:00:00Z")])
    assert state["fleet"]["CVN-77"]["place"] == "Strait of Malacca"


def test_a_held_report_is_released_when_it_fits_the_last_tracker_position():
    # stored before the tracker position was kept: it is found in the track by the edition's date
    state = home_state()
    state["fleet_meta"] = {"tracker_time": "2026-09-28T18:01:35Z"}
    state["fleet"]["CVN-77"].update(
        lat=26.57, lon=56.25, place="Strait of Hormuz", as_of="2026-09-30T21:25:12Z", trusted=False, status="operating",
        track=[{"lat": 16.0, "lon": 63.0, "place": "Arabian Sea", "time": "2026-09-28T18:01:35Z"},
               {"lat": 26.57, "lon": 56.25, "place": "Strait of Hormuz", "time": "2026-09-30T21:25:12Z"}],
        held=[{"lat": 2.5, "lon": 101.5, "place": "Strait of Malacca", "time": "2026-10-03T13:28:32Z", "url": "u-m"}])
    assert fleet.release_held(state, {"u-m": "Flattop Fiesta (ship imagery)"}) == 1
    c = state["fleet"]["CVN-77"]
    assert (c["place"], c["source"], c["prev"]["place"]) == ("Strait of Malacca", "Flattop Fiesta (ship imagery)", "Arabian Sea")
    assert "held" not in c and fleet.release_held(state) == 0
