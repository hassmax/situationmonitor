from datetime import datetime, timedelta, timezone

import common


def test_items_are_never_dated_in_the_future():
    future = common.now() + timedelta(hours=3)
    it = common.make_item({"name": "Taipei Times"}, "rss", "rss:x", "https://x/1", "t", future)
    assert common.parse_time(it["time"]) <= common.now()
    past = datetime(2026, 9, 27, 10, 0, tzinfo=timezone.utc)
    assert common.make_item({}, "rss", "s", "u", "t", past)["time"] == "2026-09-27T10:00:00Z"
    naive = datetime(2026, 9, 27, 10, 0)                  # a feed with no time zone
    assert common.make_item({}, "rss", "s", "u", "t", naive)["time"] == "2026-09-27T10:00:00Z"
