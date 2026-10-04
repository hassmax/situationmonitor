import json

import publish


def test_bucket_matches_the_page():
    # the same ids through site/app.js bucketOf (node) gave these
    assert [publish.report_bucket(i) for i in ["a1b2c3d4e5f6", "ev-0001", "Ünïcødé-ид", "0", "f" * 32]] == [0, 29, 50, 48, 0]


def test_reports_leave_the_events_file(tmp_path):
    reports = [{"source": "Reuters", "url": "https://example.com/a", "summary": "x", "time": "2026-10-04T00:00:00Z"},
               {"source": "TASS", "url": "https://example.com/b", "summary": "y", "time": "2026-10-04T01:00:00Z"},
               {"source": "Reuters", "url": "https://example.com/c", "summary": "z", "time": "2026-10-04T02:00:00Z"}]
    public = {"generated_at": "2026-10-04T03:00:00Z", "events": [{"id": "ev-0001", "summary": "s", "reports": reports},
                                                                  {"id": "a1b2c3d4e5f6", "summary": "t", "reports": []}]}
    publish.write(tmp_path, public)
    events = json.loads((tmp_path / "events.json").read_text())
    assert events["generated_at"] == "2026-10-04T03:00:00Z"
    assert all("reports" not in e for e in events["events"])
    assert events["events"][0]["src"] == ["Reuters", "TASS"]  # distinct names, for search
    assert events["events"][1]["src"] == []
    files = sorted(p.name for p in (tmp_path / "reports").iterdir())
    assert len(files) == publish.REPORT_BUCKETS  # every file, so the page never asks for a missing one
    b = json.loads((tmp_path / "reports" / "29.json").read_text())
    assert b["generated_at"] == "2026-10-04T03:00:00Z" and b["reports"]["ev-0001"] == reports
    assert json.loads((tmp_path / "reports" / "00.json").read_text())["reports"] == {"a1b2c3d4e5f6": []}
    assert "reports" in public["events"][0]  # the caller's copy (archive, alerts) is left alone
