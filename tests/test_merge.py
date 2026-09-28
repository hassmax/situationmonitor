"""Duplicates: reports pinned to a whole country or sea, attack waves across the old day
boundary, and unattributed strikes on a wave's targets."""
import merge

FAIRFORD, ENGLAND, UK = (51.71, -1.78), (52.53, -1.26), (55.38, -3.44)
SCS, SCARBOROUGH = (12.0, 113.0), (15.15, 117.76)


def event(i, place, latlon, time, summary, type_="hybrid", country="GB", attacker=None, theater="nato_east", **kw):
    return {"id": i, "alert": False, "theater": theater, "type": type_, "summary": summary, "place": place,
            "country": country, "attacker": attacker, "lat": latlon[0], "lon": latlon[1], "approx": False,
            "origins": [], "parties": [], "severity": 2, "killed": None, "injured": None, "time": time, "updated": time,
            "reports": [{"source": "s", "group": f"g{i}", "url": f"https://example.com/{i}", "time": time,
                         "summary": summary, "weight": 1, "side": None, "kind": "news", "platform": "rss"}], **kw}


def cand(e):
    return {**e, "report": e["reports"][0]}


def test_country_level_report_joins_the_spot_it_is_about():
    spot = event("a", "Fairford", FAIRFORD, "2026-09-27T12:00:00Z",
                 "British counterterrorism police made explosives arrests near RAF Fairford, a base used by US forces.",
                 attacker="RU")
    broad = event("b", "England", ENGLAND, "2026-09-27T16:00:00Z",
                  "UK counter-terrorism police investigate a suspected bombing plot at a base used by US forces.",
                  attacker="IR")                       # hybrid: the suspected culprit may differ between reports
    none = event("c", None, UK, "2026-09-27T14:00:00Z", "UK counter-terrorism police probe a suspected plot to bomb a base.")
    out = merge.merge([spot], [cand(broad), cand(none)])
    assert len(out) == 1 and len(out[0]["reports"]) == 3 and out[0]["place"] == "Fairford"


def test_broad_pin_takes_the_precise_place_when_it_arrives_later():
    broad = event("b", "South China Sea", SCS, "2026-09-27T11:00:00Z",
                  "China conducted naval and air exercises around a disputed shoal in the South China Sea.",
                  type_="deployment", country="CN", attacker="CN", theater="indopac")
    spot = event("s", "Scarborough Shoal", SCARBOROUGH, "2026-09-27T23:00:00Z",
                 "China conducts naval and air exercises around Scarborough Shoal.",
                 type_="deployment", country="PH", attacker="CN", theater="indopac")   # a sea matches any country
    out = merge.merge([broad], [cand(spot)])
    assert len(out) == 1 and out[0]["place"] == "Scarborough Shoal" and out[0]["id"] == "b"


def test_different_stories_stay_apart():
    spot = event("a", "Fairford", FAIRFORD, "2026-09-27T12:00:00Z", "Police made explosives arrests near RAF Fairford.")
    for other in (event("b", "England", ENGLAND, "2026-09-27T13:00:00Z", "A cyberattack disrupted train ticket machines."),
                  event("c", "France", (46.6, 2.4), "2026-09-27T13:00:00Z", "Police made explosives arrests near a base.",
                        country="FR"),
                  event("d", "Fairford", FAIRFORD, "2026-09-27T12:30:00Z", "Police made explosives arrests near RAF Fairford.",
                        type_="airstrike")):
        assert len(merge.merge([dict(spot, reports=list(spot["reports"]))], [cand(other)])) == 2, other["id"]


def test_two_broad_pins_need_similar_wording():
    a = event("a", "Strait of Hormuz", (26.6, 56.3), "2026-09-27T07:00:00Z",
              "US and Iranian forces exchanged fire in the Strait of Hormuz.", type_="naval", country=None, theater="mideast")
    b = event("b", "Strait of Hormuz", (26.6, 56.3), "2026-09-27T09:00:00Z",
              "Iran claims it seized a second US underwater drone in the Strait of Hormuz.", type_="naval", country=None,
              theater="mideast")
    assert len(merge.merge([a], [cand(b)])) == 2


def wave(i, time, targets, attacker="IR", country="IQ"):
    e = event(i, targets[0][0], targets[0][1], time, "Iranian drones struck Erbil.", type_="missile_drone",
              country=country, attacker=attacker, theater="mideast")
    return e


def test_one_attack_wave_across_the_old_day_boundary():
    first = wave("a", "2026-09-28T08:53:00Z", [("Erbil", (36.19, 44.01))])
    later = wave("b", "2026-09-28T11:33:00Z", [("Erbil", (36.19, 44.01))])
    out = merge.merge([], [cand(first), cand(later)])
    assert len(out) == 1 and out[0]["wave"] and len(out[0]["reports"]) == 2


def test_unattributed_strike_on_a_wave_target_joins_the_wave():
    w = wave("a", "2026-09-28T08:53:00Z", [("Erbil", (36.19, 44.01))])
    hit = event("h", "Erbil", (36.2, 44.02), "2026-09-28T09:30:00Z", "Three drones targeted a camp in Erbil.",
                type_="missile_drone", country="IQ", theater="mideast")
    far = event("f", "Basra", (30.5, 47.8), "2026-09-28T09:30:00Z", "A drone struck an oil field near Basra.",
                type_="missile_drone", country="IQ", theater="mideast")
    out = merge.merge([], [cand(w), cand(hit), cand(far)])
    assert len(out) == 2 and len(next(e for e in out if e.get("wave"))["reports"]) == 2


def test_consolidate_folds_stored_waves_and_hits():
    a = merge.merge([], [cand(wave("a", "2026-09-28T08:53:00Z", [("Erbil", (36.19, 44.01))]))])[0]
    b = dict(a, id="b", wave_key="other", time="2026-09-28T11:33:00Z", updated="2026-09-28T11:33:00Z",
             reports=[dict(a["reports"][0], url="https://example.com/b2")], targets=[dict(a["targets"][0])] if a["targets"] else [])
    hit = event("h", "Erbil", (36.2, 44.02), "2026-09-28T09:30:00Z", "Drones hit Erbil.", type_="missile_drone",
                country="IQ", theater="mideast")
    out, folded = merge.consolidate([b, hit, a], set())
    assert [e["id"] for e in out] == [a["id"]] and sorted(e["id"] for e in folded) == ["b", "h"]
    assert len(out[0]["reports"]) == 3
