"""2026-10-04: the regional analyst, which replaced the "what changed in the last 6 hours" bullets."""
from datetime import datetime, timedelta, timezone

import analyst

NOW = datetime(2026, 10, 4, 18, 0, tzinfo=timezone.utc)
THEATERS = [{"id": "mideast", "name": "Middle East", "camera": {"lat": 28.5, "lng": 45.0, "altitude": 1.25}},
            {"id": "ukraine", "name": "Russia–Ukraine", "camera": {"lat": 48.5, "lng": 34.0, "altitude": 0.85}},
            {"id": "global", "name": "Worldwide", "camera": {"lat": 30, "lng": 10, "altitude": 2.6}, "listed": False}]


def ev(i, theater="mideast", hours_ago=1, status="corroborated", type_="deployment", **kw):
    t = (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"id": i, "theater": theater, "type": type_, "place": "Al Udeid", "country": "QA", "summary": f"event {i}",
            "status": status, "severity": 2, "sources_count": 2, "time": t, "updated": t, "reports": [], **kw}


EVENTS = [ev("a1"), ev("a2", hours_ago=30), ev("s1", status="unconfirmed"), ev("c1", status="claimed"),
          ev("u1", theater="ukraine", type_="missile_drone"), ev("old", hours_ago=24 * 5), ev("g1", theater="global")]
CARRIERS = [{"name": "U.S.S. Ford", "lat": 22.0, "lon": 60.0, "place": "Arabian Sea", "status": "deployed", "at_home": False},
            {"name": "U.S.S. Bush", "lat": 36.9, "lon": -76.3, "place": "Norfolk", "status": "in port", "at_home": True}]
FLIGHTS = {"aircraft": [{"role": "tanker", "lat": 25.0, "lon": 51.0}, {"role": "tanker", "lat": 24.6, "lon": 55.5},
                        {"role": "airlift", "lat": 50.0, "lon": 8.0}]}


def test_regions_carry_recent_events_counts_and_context():
    shown = analyst.regions(EVENTS, THEATERS, CARRIERS, FLIGHTS, NOW)
    assert [r["region"] for r in shown] == ["mideast", "ukraine"]          # not the unlisted worldwide one
    me = shown[0]
    ids = [f["id"] for f in me["events"]]
    assert "old" not in ids and ids[0] in ("a1", "s1", "c1") and ids[-1] == "a2"   # new first, older after
    assert next(f for f in me["events"] if f["id"] == "a1")["new"] is True
    assert me["activity"]["force posture"]["last_6h"] == 3
    assert me["us_carriers_nearby"][0]["carrier"] == "U.S.S. Ford" and len(me["us_carriers_nearby"]) == 1
    assert me["military_aircraft_broadcasting_now"] == {"tanker": 2}
    assert "military_aircraft_broadcasting_now" in shown[1]   # Frankfurt is within reach of Ukraine's camera point


def test_confidence_comes_from_the_cited_events_not_the_model():
    shown = analyst.regions(EVENTS, THEATERS, CARRIERS, FLIGHTS, NOW)
    reply = {"regions": [{"region": "mideast", "judgments": [
        {"headline": "US increasing force posture in the Middle East", "trend": "escalating",
         "text": "More US aircraft and units arrived in Qatar (a1).", "ids": ["a1", "a2"], "confidence": "certain"},
        {"headline": "Strikes on Gulf bases", "trend": "escalating", "text": "Iran struck bases.", "ids": ["s1", "c1"]},
        {"headline": "Reports suggest a drawdown", "trend": "de-escalating", "text": "A single report says jets left.", "ids": ["s1"]},
    ]}, {"region": "ukraine", "judgments": [
        {"headline": "Made-up link", "trend": "steady", "text": "x", "ids": ["a1"]},        # cites another region's event
        {"headline": "Bad trend word", "trend": "volatile", "text": "x", "ids": ["u1"]},
        {"headline": "Strikes continue", "trend": "steady", "text": "Drone attacks continued.", "ids": ["u1", "zzz"]}]},
        {"region": "nowhere", "judgments": [{"headline": "x", "trend": "steady", "text": "x", "ids": ["a1"]}]}]}
    out = analyst.validate(reply, shown, EVENTS)
    assert [r["theater"] for r in out] == ["mideast"]
    first, second = out[0]["judgments"]
    assert first["text"] == "More US aircraft and units arrived in Qatar." and first["confidence"] == "higher"
    assert first["tally"] == {"corroborated": 2, "single_source": 0, "claimed": 0}
    # "Strikes on Gulf bases" rests only on a single-source report and a claim, stated as fact: dropped
    assert second["headline"] == "Reports suggest a drawdown" and second["confidence"] == "low"
    assert analyst.validate("nonsense", shown, EVENTS) is None
    assert analyst.validate({"regions": []}, shown, EVENTS) is None


def test_update_is_hourly_budgeted_and_keeps_the_last_good_analysis():
    calls = []

    def ask(system, user, state, settings, now, max_tokens=0, purpose=""):
        calls.append(purpose)
        return {"regions": [{"region": "mideast", "judgments": [
            {"headline": "US increasing force posture", "trend": "escalating", "text": "Units arrived.", "ids": ["a1"]}]}]}

    state = {}
    analyst.update(state, EVENTS, THEATERS, CARRIERS, FLIGHTS, {}, NOW, ask, remaining=100, share=5)
    assert calls == ["analysis"] and state["analysis"]["regions"][0]["judgments"][0]["confidence"] == "moderate"
    analyst.update(state, EVENTS + [ev("n1")], THEATERS, CARRIERS, FLIGHTS, {}, NOW + timedelta(minutes=20), ask, 100, 5)
    assert calls == ["analysis"]                                   # not again within the hour
    later = NOW + timedelta(hours=2)
    analyst.update(state, EVENTS, THEATERS, CARRIERS, FLIGHTS, {}, later, lambda *a, **k: None, 100, 5)
    assert state["analysis"]["generated_at"] == "2026-10-04T18:00:00Z"   # a failed call keeps the last one
    state["analysis_attempt"] = None
    analyst.update(state, EVENTS + [ev("n2", hours_ago=0)], THEATERS, CARRIERS, FLIGHTS, {}, later, ask, 100, 0)
    assert calls == ["analysis"]                                   # no share left: no call
