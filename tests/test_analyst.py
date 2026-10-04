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
        {"headline": "Reports suggest a drawdown", "trend": "de-escalating", "text": "A single report says jets left; Iran claims more.", "ids": ["s1", "c1"]},
        {"headline": "Army retakes town", "trend": "escalating", "text": "The army retook the town.", "ids": ["a1", "s1"]},   # single-source stated as fact
        {"headline": "One event only", "trend": "escalating", "text": "Units arrived.", "ids": ["a1"]},
        {"headline": "Advance confirmed", "trend": "escalating", "text": "Corroborated reports indicate the army advanced.", "ids": ["a1", "c1"]},
    ]}, {"region": "ukraine", "judgments": [
        {"headline": "Made-up link", "trend": "steady", "text": "x", "ids": ["a1"]},        # cites another region's event
        {"headline": "Bad trend word", "trend": "volatile", "text": "x", "ids": ["u1"]},
        {"headline": "Strikes continue", "trend": "steady", "text": "Drone attacks continued.", "ids": ["u1", "zzz"]}]},
        {"region": "nowhere", "judgments": [{"headline": "x", "trend": "steady", "text": "x", "ids": ["a1"]}]}]}
    out = analyst.validate(reply, shown, EVENTS)
    assert [r["theater"] for r in out] == ["mideast"]
    first, second = out[0]["judgments"]
    assert first["text"] == "More US aircraft and units arrived in Qatar." and first["confidence"] == "higher"
    assert first["tally"] == {"corroborated": 2, "single_source": 0, "claimed": 0, "tracked": 0}
    # "Strikes on Gulf bases" rests only on a single-source report and a claim, stated as fact: dropped
    assert second["headline"] == "Reports suggest a drawdown" and second["confidence"] == "low"
    assert analyst.validate("nonsense", shown, EVENTS) is None
    assert analyst.validate({"regions": []}, shown, EVENTS) is None


def test_update_is_hourly_budgeted_and_keeps_the_last_good_analysis(monkeypatch):
    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    calls = []

    def ask(system, user, state, settings, now, max_tokens=0, purpose="", model=None):
        calls.append(purpose)
        return {"regions": [{"region": "mideast", "judgments": [
            {"headline": "US increasing force posture", "trend": "escalating", "text": "Units arrived.", "ids": ["a1", "a2"]}]}]}

    state = {}
    analyst.update(state, EVENTS, THEATERS, CARRIERS, FLIGHTS, {}, NOW, ask, remaining=100, share=5)
    assert calls == ["analysis"] and state["analysis"]["regions"][0]["judgments"][0]["confidence"] == "higher"
    analyst.update(state, EVENTS + [ev("n1")], THEATERS, CARRIERS, FLIGHTS, {}, NOW + timedelta(minutes=20), ask, 100, 5)
    assert calls == ["analysis"]                                   # not again within the hour
    later = NOW + timedelta(hours=2)
    analyst.update(state, EVENTS, THEATERS, CARRIERS, FLIGHTS, {}, later, lambda *a, **k: None, 100, 5)
    assert state["analysis"]["generated_at"] == "2026-10-04T18:00:00Z"   # a failed call keeps the last one
    state["analysis_attempt"] = None
    analyst.update(state, EVENTS + [ev("n2", hours_ago=0)], THEATERS, CARRIERS, FLIGHTS, {}, later, ask, 100, 0)
    assert calls == ["analysis"]                                   # no share left: no call


def test_flash_first_then_the_usual_model(monkeypatch):
    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    asked = []

    def ask(system, user, state, settings, now, max_tokens=0, purpose="", model=None):
        asked.append(model)
        if model:
            return None   # Flash busy or out of its own limit
        return {"regions": [{"region": "mideast", "judgments": [
            {"headline": "US increasing force posture", "trend": "escalating", "text": "Units arrived.", "ids": ["a1", "a2"]}]}]}

    state = {"llm_model": {"model": "lite"}}
    analyst.update(state, EVENTS, THEATERS, CARRIERS, FLIGHTS, {"llm_fallback_models": ["flash"]}, NOW, ask, 100, 5)
    assert asked == ["flash", None] and state["analysis"]["by"] == "lite (Gemini)" and state["analysis"]["regions"]


def test_free_providers_first_with_their_own_counts(monkeypatch):

    sent = []

    class R:
        status_code = 200
        headers = {"content-type": "application/json"}

        def __init__(self, text):
            self.text = text

        def json(self):
            import json
            return json.loads(self.text)

    def post(url, body, token):
        sent.append((url, body["model"], token))
        reply = {"regions": [{"region": "mideast", "judgments": [
            {"headline": "US increasing force posture", "trend": "escalating", "text": "Units arrived.", "ids": ["a1", "a2"]}]}]}
        import json
        return R(json.dumps({"choices": [{"message": {"content": json.dumps(reply)}}]}))

    import extract
    monkeypatch.setattr(extract, "_post", post)
    monkeypatch.setenv("OPENROUTER_API_KEY", "k-or")   # Cerebras has no key: skipped
    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
    gemini = []
    state = {}
    analyst.update(state, EVENTS, THEATERS, CARRIERS, FLIGHTS, {}, NOW,
                   lambda *a, **k: gemini.append(1), remaining=0, share=0)   # Gemini out of calls
    assert [s[0] for s in sent] == ["https://openrouter.ai/api/v1/chat/completions"] and sent[0][2] == "k-or"
    assert not gemini and state["analysis"]["by"].endswith("(OpenRouter)")
    assert state["providers"]["openrouter"]["count"] == 1
    assert "llm_calls" not in state                                    # not counted against Gemini


def test_no_keys_and_no_gemini_left_means_no_call(monkeypatch):
    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    state, asked = {}, []
    analyst.update(state, EVENTS, THEATERS, CARRIERS, FLIGHTS, {}, NOW, lambda *a, **k: asked.append(1), remaining=2, share=5)
    assert not asked and "analysis" not in state


def test_a_line_resting_partly_on_claims_cannot_call_itself_corroborated():
    shown = analyst.regions(EVENTS, THEATERS, CARRIERS, FLIGHTS, NOW)
    bad = {"regions": [{"region": "mideast", "judgments": [
        {"headline": "Army advancing", "trend": "escalating", "text": "Corroborated reports indicate the army advanced.", "ids": ["a1", "c1"]}]}]}
    assert analyst.validate(bad, shown, EVENTS) is None
    fine = {"regions": [{"region": "mideast", "judgments": [
        {"headline": "Army advancing", "trend": "escalating", "text": "Corroborated reports indicate the army advanced.", "ids": ["a1", "a2"]}]}]}
    assert analyst.validate(fine, shown, EVENTS)[0]["judgments"][0]["confidence"] == "higher"


def test_tracked_flights_can_be_cited_and_carry_their_own_links():
    moves = {"aircraft": [], "attribution": "Flight data: adsb.lol contributors", "license_url": "https://example.org/odbl", "movements": [
        {"id": "fk1", "role": "tanker", "time": "2026-10-04T17:00:00Z", "places": [(25.117, 51.315)], "url": "https://adsb.lol/?icao=k1",
         "label": "KC-135 tanker · Al Udeid Air Base", "text": "KC-135 tanker (PAWN54) took off from Al Udeid Air Base, Qatar, at 4 Oct 16:00 UTC; last reported 400 km northwest."},
        {"id": "fk2", "role": "tanker", "time": "2026-10-04T17:10:00Z", "places": [(25.117, 51.315)], "url": "https://adsb.lol/?icao=k2",
         "label": "KC-135 tanker · Al Udeid Air Base", "text": "KC-135 tanker (PAWN55) took off from Al Udeid Air Base, Qatar, at 4 Oct 16:10 UTC."},
        {"id": "fz9", "role": "airlift", "time": "2026-10-04T17:10:00Z", "places": [(51.68, -1.79)], "url": "https://adsb.lol/?icao=z9",
         "label": "C-17 transport · RAF Fairford", "text": "C-17 took off from RAF Fairford."}]}
    shown = analyst.regions(EVENTS, THEATERS, CARRIERS, moves, NOW)
    me = next(r for r in shown if r["region"] == "mideast")
    assert [m["id"] for m in me["flight_movements"]] == ["fk2", "fk1"]      # newest first; Fairford is not in the Middle East
    reply = {"regions": [{"region": "mideast", "judgments": [
        {"headline": "US tankers leaving Al Udeid", "trend": "shifting", "text": "Two KC-135 tankers took off from Al Udeid toward the northwest.", "ids": ["fk1", "fk2"]},
        {"headline": "Stray flight", "trend": "shifting", "text": "A C-17 left Fairford.", "ids": ["fz9", "fk1"]}]}]}   # Fairford isn't in this region
    out = analyst.validate(reply, shown, EVENTS, moves)
    j = out[0]["judgments"]
    assert len(j) == 1 and j[0]["ids"] == [] and [f["url"] for f in j[0]["flights"]] == ["https://adsb.lol/?icao=k1", "https://adsb.lol/?icao=k2"]
    assert j[0]["tally"]["tracked"] == 2 and j[0]["confidence"] == "higher"


def test_one_notable_flight_can_stand_alone_but_not_one_transport():
    moves = {"aircraft": [], "movements": [
        {"id": "fk1", "role": "tanker", "time": "2026-10-04T17:00:00Z", "places": [(25.117, 51.315), (37.0, 35.4)], "url": "u1",
         "label": "KC-135 tanker · Al Udeid Air Base", "text": "KC-135 tanker took off from Al Udeid Air Base and landed at Incirlik Air Base."},
        {"id": "fc1", "role": "airlift", "time": "2026-10-04T17:00:00Z", "places": [(25.117, 51.315)], "url": "u2",
         "label": "C-17 transport · Al Udeid Air Base", "text": "C-17 took off from Al Udeid Air Base."}]}
    shown = analyst.regions(EVENTS, THEATERS, CARRIERS, moves, NOW)
    reply = {"regions": [{"region": "mideast", "judgments": [
        {"headline": "KC-135 tanker left Al Udeid for Incirlik", "trend": "shifting", "text": "A KC-135 tanker took off from Al Udeid and landed at Incirlik.", "ids": ["fk1"]},
        {"headline": "C-17 left Al Udeid", "trend": "shifting", "text": "A C-17 took off from Al Udeid.", "ids": ["fc1"]}]}]}
    out = analyst.validate(reply, shown, EVENTS, moves)
    assert [j["headline"] for j in out[0]["judgments"]] == ["KC-135 tanker left Al Udeid for Incirlik"]
    assert out[0]["judgments"][0]["confidence"] == "moderate"
