import copy

import yaml

import corrections
import extract
import merge


def report(url, group, side=None, t="2026-09-27T10:00:00Z"):
    return {"source": group, "platform": "rss", "kind": "news", "side": side, "group": group, "weight": 1,
            "claim": "report", "launched": None, "url": url, "time": t, "summary": f"report {url}"}


def event(i="e1", reports=None):
    return {"id": i, "theater": "ukraine", "type": "missile_drone", "summary": "Drones hit Kyiv.", "place": "Kyiv",
            "country": "UA", "attacker": "RU", "lat": 50.45, "lon": 30.52, "approx": False, "origins": [],
            "severity": 2, "killed": None, "injured": None, "time": "2026-09-27T10:00:00Z",
            "updated": "2026-09-27T10:00:00Z", "alert": False,
            "reports": reports or [report("u1", "ua-military", "UA"), report("u2", "reuters")]}


def load(tmp_path, entries):
    p = tmp_path / "corrections.yaml"
    p.write_text(yaml.safe_dump({"corrections": entries}))
    return corrections.load(p)


def test_every_entry_needs_a_note(tmp_path, capsys):
    fixes = load(tmp_path, [{"id": "e1", "hide": True}, {"id": "e2", "hide": True, "note": "dup"}, {"hide": True, "note": "x"}])
    assert [c["id"] for c in fixes] == ["e2"]
    assert "needs a short note" in capsys.readouterr().out


def test_hide(tmp_path):
    fixes = load(tmp_path, [{"id": "e1", "hide": True, "note": "Duplicate."}])
    pub = [merge.public_event(event()), merge.public_event(event("e2"))]
    assert [e["id"] for e in corrections.publish(pub, fixes, extract.EVENT_TYPES)] == ["e2"]
    assert corrections.hidden_ids(fixes) == {"e1"}


def test_edit_labels_and_validates(tmp_path):
    fixes = load(tmp_path, [{"id": "e1", "note": "Wrong town.",
                             "edit": {"place": "Kharkiv", "lat": 49.99, "lon": 36.23, "severity": 9, "type": "bogus", "status": "x"}}])
    stored = event()
    out = corrections.publish([merge.public_event(stored)], fixes, extract.EVENT_TYPES)[0]
    assert (out["place"], out["lat"], out["lon"]) == ("Kharkiv", 49.99, 36.23)
    assert out["severity"] == 2 and out["type"] == "missile_drone"   # invalid values ignored
    assert out.get("status") != "x"                                    # fields outside the list ignored
    assert out["corrected"] == [{"note": "Wrong town.", "change": "Edited place, lat, lon"}]
    assert stored["place"] == "Kyiv"                       # stored event untouched: deleting the entry undoes it
    assert "corrected" not in corrections.publish([merge.public_event(stored)], [], extract.EVENT_TYPES)[0]


def test_drop_report_recomputes_confidence(tmp_path):
    fixes = load(tmp_path, [{"id": "e1", "drop_report": "u2", "note": "About another incident."}])
    events = [event()]
    merge.apply_status(events, [])
    assert events[0]["status"] == "corroborated"
    events = corrections.drop_reports(events, fixes)
    merge.apply_status(events, [])
    assert [r["url"] for r in events[0]["reports"]] == ["u1"]
    assert events[0]["status"] == "claimed"                 # only the Ukrainian military report is left
    out = corrections.publish([merge.public_event(events[0])], fixes, extract.EVENT_TYPES)[0]
    assert out["corrected"] == [{"note": "About another incident.", "change": "Removed a report"}]


def test_dropping_the_last_report_removes_the_event(tmp_path):
    fixes = load(tmp_path, [{"id": "e1", "drop_report": "u1", "note": "x"}])
    assert corrections.drop_reports([event(reports=[report("u1", "g")])], fixes) == []


def test_hidden_event_is_not_recreated(tmp_path):
    fixes = load(tmp_path, [{"id": "e1", "hide": True, "note": "Wrong."},
                            {"id": "e9", "drop_report": "u9", "note": "Wrong."}])
    events = [event()]
    blocked = corrections.blocked_urls(events, fixes)
    assert blocked == {"u1", "u2", "u9"}

    def cand(url, lat=50.45):
        return {"theater": "ukraine", "type": "missile_drone", "summary": "Drones hit Kyiv.", "place": "Kyiv",
                "country": "UA", "attacker": None, "lat": lat, "lon": 30.52, "approx": False, "origins": [],
                "parties": [], "severity": 2, "killed": None, "injured": None, "time": "2026-09-27T10:00:00Z",
                "transfer": None, "legal_basis": None, "alert": False, "report": report(url, "other", t="2026-09-27T11:00:00Z")}
    # the same reports are filtered out before merging, so they can't create a new event
    candidates = [c for c in [cand("u1"), cand("u2"), cand("u9")] if c["report"]["url"] not in blocked]
    assert candidates == []
    # a new report of the same incident joins the hidden event and stays hidden
    events = merge.merge(copy.deepcopy(events), [cand("u3")])
    assert len(events) == 1 and len(events[0]["reports"]) == 3
    pub = corrections.publish([merge.public_event(e) for e in events], fixes, extract.EVENT_TYPES)
    assert pub == []
    # A genuinely later occurrence in the same place is a new alert, not the hidden event.
    later = {**cand("u4"), "time": "2026-09-27T11:00:00Z", "summary": "Drones hit Kyiv again."}
    separate = merge.merge(copy.deepcopy(events), [later])
    pub = corrections.publish([merge.public_event(e) for e in separate], fixes, extract.EVENT_TYPES)
    assert len(pub) == 1 and pub[0]["id"] != "e1"


def test_example_file_parses():
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "pipeline/config/corrections.yaml"
    entries = corrections.load(path)
    raw = (__import__("yaml").safe_load(path.read_text(encoding="utf-8")) or {}).get("corrections") or []
    assert len(entries) == len(raw)                     # every entry in the real file is valid (id and note)
    assert all(e["id"] and e["note"] for e in entries)
