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


def test_a_delivery_ending_in_the_suppliers_country_is_the_recipients_own_move_or_a_marker():
    train = ev("t", "A German F-35 aircraft arrived at Ebbing Air National Base for training.", country="US", lat=35.4, lon=-94.4,
               transfer={"supplier": "US", "recipient": "DE", "kind": "delivery", "from": None,
                         "to": {"place": "Ebbing Air National Guard Base", "lat": 35.336, "lon": -94.367}})
    flight = ev("f", "Morocco's first F-16 aircraft completed its maiden flight in Greenville.", country="US", lat=34.85, lon=-82.4,
                transfer={"supplier": "US", "recipient": "MA", "kind": "delivery", "from": None,
                          "to": {"place": "Greenville", "lat": 34.853, "lon": -82.394}})
    sale = ev("s", "Poland received F-35 jets.", country="PL", lat=52.2, lon=21.0,
              transfer={"supplier": "US", "recipient": "PL", "kind": "delivery", "to": {"place": "Warsaw", "lat": 52.2, "lon": 21.0}})
    assert audit.run([train, flight, sale], [], {}, NOW)["counts"] == {"route_end_in_supplier": 2}
    assert merge.deliveries_at_supplier([train, flight, sale]) == 2
    assert train["transfer"]["supplier"] == train["transfer"]["recipient"] == "DE"     # Germany's own aircraft, faint from home
    assert flight["transfer"] is None and sale["transfer"]["supplier"] == "US"         # no route for a first flight
    assert audit.run([train, flight, sale], [], {}, NOW)["counts"] == {}


def test_a_routes_pin_may_lie_in_the_supplier_or_recipient_not_only_the_named_country():
    moscow = ev("m", "Equipment is being supplied from Algeria to Russia via Poland.", country="PL", lat=55.75, lon=37.62,
                transfer={"supplier": "DZ", "recipient": "RU", "kind": "delivery", "to": {"place": "Moscow", "lat": 55.756, "lon": 37.617}})
    assert audit.run([moscow], [], {}, NOW)["counts"] == {}


def test_a_region_name_is_no_carrier_position():
    row = {"hull": "CVN-73", "name": "U.S.S. George Washington", "lat": 25.0, "lon": 55.0, "place": "West Asia", "at_home": False}
    assert audit.run([], [row], {}, NOW)["counts"] == {"carrier_vague_place": 1}
    state = {"fleet": {"CVN-73": {"hull": "CVN-73", "lat": 25.0, "lon": 55.0, "place": "West Asia", "status": "operating",
                                  "as_of": "2026-10-07T10:42:56Z", "source": "Islam Times (via Google News)", "trusted": False,
                                  "prev": {"lat": 16.0, "lon": 63.0, "place": "Arabian Sea", "as_of": "2026-10-05T18:13:05Z"},
                                  "last_trusted": {"lat": 16.0, "lon": 63.0, "place": "Arabian Sea", "as_of": "2026-10-05T18:13:05Z"},
                                  "track": []}}}
    fleet.repair(state)
    c = state["fleet"]["CVN-73"]
    assert (c["place"], c["lat"], c["lon"]) == ("Arabian Sea", 16.0, 63.0) and "prev" not in c
