from datetime import datetime, timezone

import audit
import fleet
import geo
import merge

NOW = datetime(2026, 10, 7, 15, tzinfo=timezone.utc)


def ev(i, summary, type_="arms_transfer", **kw):
    return {"id": i, "type": type_, "summary": summary, "time": "2026-10-07T10:00:00Z", **kw}


def test_route_starts_at_a_command_or_region_are_that_region():
    e = ev("n", "Nevada Air National Guard members returned home following a deployment.", country="US", lat=39.3, lon=-116.6,
           place="Nevada", transfer={"supplier": "US", "recipient": "US", "from": {"place": "CENTCOM", "lat": 27.99, "lon": 82.52},
                                     "to": {"place": "Nevada", "lat": 39.3, "lon": -116.6}})
    a = ev("a", "The United States transferred spy drones from Africa to Colombia.", country="CO", lat=4.6, lon=-74.3,
           transfer={"supplier": "US", "recipient": "CO", "from": {"place": "Africa", "lat": 7.54, "lon": 15.36},
                     "to": {"place": "Colombia", "lat": 4.6, "lon": -74.3}})
    assert audit.run([e, a], [], {}, NOW)["counts"] == {"route_end_unplaced": 2}
    geo.pin_commands([e, a])
    assert e["transfer"]["from"]["region"] and e["transfer"]["from"]["place"].startswith("Middle East")   # not Nepal
    assert a["transfer"]["from"]["region"] and a["transfer"]["from"]["place"] == "Africa"
    assert audit.run([e, a], [], {}, NOW)["counts"] == {}


def test_a_carriers_own_move_is_not_a_supply_route():
    c = ev("c", "A US aircraft carrier arrived in Thailand after a mission.", country="TH",
           transfer={"supplier": "US", "recipient": "US", "from": {"place": "Middle East", "lat": 25, "lon": 55},
                     "to": {"place": "Phuket", "lat": 7.88, "lon": 98.39}})
    lincoln = ev("l", "A portion of the USS Abraham Lincoln strike group is set to return to San Diego.",
                 transfer={"supplier": "US", "recipient": "US", "to": {"place": "San Diego", "lat": 32.7, "lon": -117.2}})
    jets = ev("j", "Twelve F-15E Strike Eagles returned to Seymour Johnson.",
              transfer={"supplier": "US", "recipient": "US", "to": {"place": "Seymour Johnson", "lat": 35.3, "lon": -78.0}})
    assert audit.run([c, lincoln, jets], [], {}, NOW)["counts"].get("carrier_route") == 2
    assert merge.carrier_moves([c, lincoln, jets]) == 2
    assert c["type"] == "deployment" and c["place"] == "Phuket" and c["transfer"] is None and jets["type"] == "arms_transfer"


def test_drone_and_missile_attacks_filed_as_airstrikes_or_explosions_get_their_kind():
    mk = lambda i, t, s, att="RU": ev(i, s, t, attacker=att)
    cases = [mk("1", "airstrike", "Ukraine's air defense intercepted 5 ballistic missiles and 117 drones during a massive Russian attack."),
             mk("2", "explosion", "The Houthis claim fresh missile and drone attacks targeting locations in Saudi Arabia.", "YE"),
             mk("3", "airstrike", "Ukrainian Air Force reported Russian guided bomb (KAB) strikes on Zaporizhzhia."),
             mk("4", "airstrike", "Russian Ministry of Defence claimed drone operators destroyed a Ukrainian position."),
             mk("5", "explosion", "An explosion near a drone factory; cause unknown.", None)]
    assert merge.drone_strikes(cases) == 2
    assert [c["type"] for c in cases] == ["missile_drone", "missile_drone", "airstrike", "airstrike", "explosion"]


def test_misplaced_pins_and_routes_ending_in_the_supplier_are_flagged():
    wrong = ev("w", "Morocco's first F-16 completed its maiden flight.", "production", country="MA", lat=32.75, lon=-97.33, place="Fort Worth")
    island = ev("i", "US Marines on Yonaguni.", "deployment", country="JP", lat=24.47, lon=123.0, place="Yonaguni")
    sale = ev("s", "Morocco's first F-16 flew in Greenville.", country="MA",
              transfer={"supplier": "US", "recipient": "MA", "to": {"place": "Greenville", "lat": 34.85, "lon": -82.4}})
    counts = audit.run([wrong, island, sale], [], {}, NOW)["counts"]
    assert counts == {"pin_outside_country": 1, "route_end_in_supplier": 1}    # a small island isn't flagged


def test_carrier_checks_and_no_line_from_a_position_it_couldnt_have_sailed_from():
    away = lambda hull, name, place, lat, lon: {"hull": hull, "name": name, "lat": lat, "lon": lon, "place": place, "at_home": False}
    rows = [away("CVN-77", "Bush", "Phuket", 7.88, 98.39), away("CVN-78", "Ford", "Thailand", 9.0, 99.0),
            away("CVN-68", "Nimitz", "Norfolk", 36.95, -76.33), away("CVN-69", "Eisenhower", "Norfolk", 36.94, -76.3)]
    assert audit.run([], rows, {}, NOW)["counts"] == {"carriers_together": 1}     # not the two at their home port
    state = {"fleet": {"CVN-76": {"hull": "CVN-76", "lat": 7.88, "lon": 98.39, "place": "Phuket", "status": "arrived",
                                  "as_of": "2026-10-04T10:00:00Z", "track": []}}}
    fleet.update(state, [{"hull": "CVN-76", "status": "in port", "place": "Bremerton, Wash.", "lat": 47.56, "lon": -122.63,
                          "heading_to": None, "time": "2026-10-05T18:13:05Z", "trusted": True, "source": "USNI", "url": "u"}])
    assert "prev" not in state["fleet"]["CVN-76"]          # Phuket to Bremerton in a day: the old position was wrong
