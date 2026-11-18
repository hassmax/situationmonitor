"""2026-11-17: tension needles, each region's last 24 hours against its own last 30 days (no model)."""
import json
from datetime import datetime, timedelta, timezone

import tension

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)
THEATERS = [{"id": "mideast", "name": "Middle East"}, {"id": "ukraine", "name": "Russia–Ukraine"},
            {"id": "global", "name": "Worldwide", "listed": False}]
_n = [0]


def ev(hours_ago, type_="airstrike", severity=1, status="corroborated", theater="mideast", summary="x", **kw):
    _n[0] += 1
    t = (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"id": f"e{_n[0]}", "theater": theater, "type": type_, "severity": severity, "status": status,
            "summary": summary, "time": t, **kw}


ALL_DAYS = {(NOW - timedelta(days=k)).strftime("%Y-%m-%d") for k in range(0, 33)}


def usual_month(per_day=2, theater="mideast"):
    """the same activity every day for the past 30 days"""
    return [ev(24 * d + 3 + i, theater=theater) for d in range(1, 31) for i in range(per_day)]


def reading(published, archived=(), have=ALL_DAYS, state=None, theater="mideast"):
    out = tension.compute(list(published), list(archived), set(have), THEATERS, state or {}, NOW)
    return next(r for r in out["regions"] if r["theater"] == theater)


def test_a_usual_day_reads_guarded_a_far_busier_one_severe_and_a_quiet_one_low():
    past = usual_month()
    usual = reading([ev(2), ev(5)], past)
    assert usual["level"] == "Guarded" and abs(usual["reading"] - 0.5) < 0.01
    assert usual["raw"] == usual["reading"] and usual["usual"] == usual["score"] and usual["events"] == 2
    busy = reading([ev(1, severity=3), ev(2, severity=3), ev(3)], past)
    assert busy["level"] == "Severe" and busy["reading"] == 1.0
    assert reading([], past)["level"] == "Low"


def test_kinds_weigh_differently_and_confidence_and_old_stories_count_less():
    attack, posture = tension.points(ev(1)), tension.points(ev(1, type_="deployment"))
    assert attack > posture > 0
    assert tension.points(ev(1, severity=3)) > tension.points(ev(1, severity=2)) > attack
    assert tension.points(ev(1, status="claimed")) < tension.points(ev(1, status="unconfirmed")) < attack
    assert tension.points(ev(1, possibly_old=True)) < attack
    assert tension.points(ev(1, type_="production")) < posture


def test_ceasefires_talks_and_pullbacks_bring_it_down_but_broken_deals_and_hostile_steps_do_not():
    calm = lambda s, t="diplomacy": tension.points(ev(1, type_=t, summary=s))  # noqa: E731
    assert calm("Israel and Hamas agree to a ceasefire in Gaza") < 0
    assert calm("Peace talks open in Doha between the two sides") < 0
    assert calm("Russia pulls back troops from the border", "deployment") < 0
    assert calm("Ceasefire collapses as shelling resumes") > 0
    assert calm("Russia withdraws from the arms control treaty") > 0
    assert calm("Poland expels Russian diplomats") > 0
    assert calm("EU adopts new sanctions package against Iran") > 0
    assert calm("Leaders meet in Brussels") == 0
    assert calm("Tehran and New Delhi held talks to deepen ties in the technology sector") == 0
    assert calm("Yamaha withdraws from the Qatar MotoGP citing the conflict") == 0
    assert calm("Reports announce the completion of the US troop withdrawal from Iraq") < 0
    assert calm("Ethiopia called for the immediate withdrawal of Eritrean troops") >= 0
    # a battlefield withdrawal is fighting, not a calming step
    assert tension.points(ev(1, type_="territory", summary="Ukrainian forces withdraw from Pokrovsk")) > 0
    past = usual_month()
    assert reading([ev(2), ev(5), ev(1, type_="diplomacy", summary="Sides agree to a truce")], past)["reading"] < 0.5


def test_the_analysts_judgments_nudge_it_and_are_capped_and_listed():
    past = usual_month()
    today = [ev(2), ev(5)]
    def analysis(*trends, hours_old=1):
        return {"analysis": {"generated_at": (NOW - timedelta(hours=hours_old)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                             "regions": [{"theater": "mideast", "judgments": [{"headline": f"h{i}", "trend": t, "ids": ["a", "b"]}
                                                                              for i, t in enumerate(trends)]}]}}
    up = reading(today, past, state=analysis("escalating"))
    assert abs(up["reading"] - 0.56) < 0.01 and up["analyst"][0]["trend"] == "escalating"
    assert abs(reading(today, past, state=analysis("de-escalating"))["reading"] - 0.44) < 0.01
    assert abs(reading(today, past, state=analysis(*["escalating"] * 5))["reading"] - 0.62) < 0.01  # capped
    assert reading(today, past, state=analysis("steady"))["analyst"] == []
    assert abs(reading(today, past, state=analysis("escalating", hours_old=20))["reading"] - 0.5) < 0.01  # too old


def test_too_little_history_gives_no_needle_and_unlisted_regions_are_left_out():
    few = {(NOW - timedelta(days=k)).strftime("%Y-%m-%d") for k in range(0, 6)}
    r = reading([ev(2)], usual_month(), have=few)
    assert r["level"] is None and r["days"] == 5
    out = tension.compute([], [], ALL_DAYS, THEATERS, {}, NOW)
    assert [x["theater"] for x in out["regions"]] == ["mideast", "ukraine"]


def test_reasons_list_the_heaviest_events_and_the_published_version_wins():
    past = usual_month()
    big = ev(1, severity=3)
    r = reading([ev(2), big, ev(1, type_="diplomacy", summary="ceasefire agreed")], past)
    assert r["reasons"][0]["id"] == big["id"] and r["reasons"][0]["why"] == "airstrike"
    assert any(x["why"] == "calming step" for x in r["reasons"])
    old = dict(big, severity=1)
    assert reading([big], past + [old])["score"] == reading([big], past)["score"]


def test_update_reads_the_archive_files(tmp_path):
    folder = tmp_path / "archive"
    folder.mkdir()
    past = usual_month()
    for k in range(0, 32):
        day = (NOW - timedelta(days=k)).strftime("%Y-%m-%d")
        evs = [e for e in past if e["time"].startswith(day)]
        (folder / f"{day}.json").write_text(json.dumps({"day": day, "events": evs}))
    out = tension.update(tmp_path, [ev(2), ev(5)], THEATERS, {}, NOW)
    me = next(r for r in out["regions"] if r["theater"] == "mideast")
    assert me["level"] == "Guarded" and me["days"] == 30 and out["window_hours"] == 24
