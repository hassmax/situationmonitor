from datetime import datetime, timezone

import fleet


def test_carrier_names_start_with_uss_even_for_stored_entries():
    state = {"fleet": {"CVN-78": {"hull": "CVN-78", "name": "USS Gerald R. Ford", "short": "Ford", "lat": 36.9, "lon": -76.3}}}
    c = fleet.public(state, datetime(2026, 9, 28, tzinfo=timezone.utc))[0]
    assert (c["name"], c["short"]) == ("U.S.S. Gerald R. Ford", "U.S.S. Ford")
    assert all(name.startswith("U.S.S. ") and short.startswith("U.S.S. ") for name, short in fleet.CARRIERS.values())
