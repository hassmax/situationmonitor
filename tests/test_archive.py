import json
from datetime import datetime, timezone

import archive

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)


def ev(i, time, summary="s"):
    return {"id": i, "time": time, "summary": summary}


def read(root, day):
    return json.loads((root / "archive" / f"{day}.json").read_text())


def test_one_file_per_utc_day_by_event_time(tmp_path):
    n = archive.update(tmp_path, [ev("a", "2026-09-26T23:50:00Z"), ev("b", "2026-09-27T00:10:00Z")], {}, [], NOW)
    assert n == 2
    assert [e["id"] for e in read(tmp_path, "2026-09-26")["events"]] == ["a"]
    assert [e["id"] for e in read(tmp_path, "2026-09-27")["events"]] == ["b"]


def test_unchanged_day_is_not_rewritten(tmp_path):
    events = [ev("a", "2026-09-27T01:00:00Z")]
    archive.update(tmp_path, events, {}, [], NOW)
    path = tmp_path / "archive" / "2026-09-27.json"
    before = path.stat().st_mtime_ns
    assert archive.update(tmp_path, events, {}, [], NOW) == 0
    assert path.stat().st_mtime_ns == before
    assert archive.update(tmp_path, [ev("a", "2026-09-27T01:00:00Z", summary="updated")], {}, [], NOW) == 1
    assert read(tmp_path, "2026-09-27")["events"][0]["summary"] == "updated"


def test_events_that_age_out_stay_archived(tmp_path):
    archive.update(tmp_path, [ev("a", "2026-09-20T01:00:00Z"), ev("b", "2026-09-20T02:00:00Z")], {}, [], NOW)
    # later, "a" is pruned from the dashboard but "b" changes, so the day is rewritten
    archive.update(tmp_path, [ev("b", "2026-09-20T02:00:00Z", summary="new")], {}, [], NOW)
    assert [e["id"] for e in read(tmp_path, "2026-09-20")["events"]] == ["a", "b"]


def test_taken_down_events_are_removed(tmp_path):
    archive.update(tmp_path, [ev("a", "2026-09-20T01:00:00Z"), ev("bad", "2026-09-20T02:00:00Z")], {}, [], NOW)
    archive.update(tmp_path, [], {"bad": "2026-09-20"}, [], NOW)
    assert [e["id"] for e in read(tmp_path, "2026-09-20")["events"]] == ["a"]


def test_event_redated_moves_between_days(tmp_path):
    archive.update(tmp_path, [ev("a", "2026-09-27T00:30:00Z")], {}, [], NOW)
    archive.update(tmp_path, [ev("a", "2026-09-26T22:00:00Z"), ev("x", "2026-09-27T05:00:00Z")], {}, [], NOW)
    assert [e["id"] for e in read(tmp_path, "2026-09-26")["events"]] == ["a"]
    assert [e["id"] for e in read(tmp_path, "2026-09-27")["events"]] == ["x"]


def test_daily_fleet_snapshot(tmp_path):
    fleet = [{"hull": "CVN-78", "lat": 36.9, "lon": -76.3, "place": "Norfolk"}, {"hull": "CVN-79", "lat": None}]
    assert archive.update(tmp_path, [], {}, fleet, NOW) == 1
    snap = json.loads((tmp_path / "archive" / "fleet" / "2026-09-27.json").read_text())
    assert [c["hull"] for c in snap["carriers"]] == ["CVN-78"]
    assert archive.update(tmp_path, [], {}, fleet, NOW) == 0
