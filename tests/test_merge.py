"""Duplicates: reports pinned to a whole country or sea, attack waves across the old day
boundary, and unattributed strikes on a wave's targets."""
import merge
import dedupe

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


def test_country_level_report_needs_same_incident_confirmation():
    spot = event("a", "Fairford", FAIRFORD, "2026-09-27T12:00:00Z",
                 "British counterterrorism police made explosives arrests near RAF Fairford, a base used by US forces.",
                 attacker="RU")
    broad = event("b", "England", ENGLAND, "2026-09-27T16:00:00Z",
                  "UK counter-terrorism police investigate a suspected bombing plot at a base used by US forces.",
                  attacker="IR")                       # hybrid: the suspected culprit may differ between reports
    none = event("c", None, UK, "2026-09-27T14:00:00Z", "UK counter-terrorism police probe a suspected plot to bomb a base.")
    out = merge.merge([spot], [cand(broad), cand(none)])
    assert len(out) == 3  # different occurrence times need confirmation, even with broad pins
    out, _ = dedupe._fold(out, [(out[0]["id"], e["id"]) for e in out[1:]], set())
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


HAGUE, BERLIN, NYC = (52.08, 4.30), (52.52, 13.40), (40.71, -74.0)


def test_one_visit_filed_under_two_theaters_is_one_event():
    a = event("a", "The Hague", HAGUE, "2026-09-28T16:42:00Z", "Germany's foreign minister visited the ICC, "
              "pledging full support despite US sanctions.", type_="diplomacy", country="NL", theater="ukraine",
              parties=["DE", "ICC"])
    b = dict(event("b", "The Hague", HAGUE, "2026-09-28T18:26:00Z", a["summary"], type_="legal", country="NL",
                   theater="nato_east", parties=["DE", "ICC"]))
    assert len(merge.merge([], [cand(a), cand(b)])) == 1
    out, folded = merge.consolidate([b, a], set())
    assert [e["id"] for e in out] == ["a"] and [e["id"] for e in folded] == ["b"]


def test_one_meeting_pinned_to_different_cities_needs_the_same_parties_and_close_wording():
    a = event("a", "New York", NYC, "2026-09-27T08:00:00Z", "German and Russian foreign ministers held rare talks "
              "at the UN.", type_="diplomacy", country="US", parties=["DE", "RU"])
    b = event("b", "Berlin", BERLIN, "2026-09-27T12:00:00Z", "German and Russian foreign ministers held rare talks.",
              type_="diplomacy", country="DE", parties=["DE", "RU"])
    c = event("c", "Berlin", BERLIN, "2026-09-27T13:00:00Z", "Germany and Russia discussed Black Sea exports.",
              type_="diplomacy", country="DE", parties=["DE", "RU"])
    d = event("d", "Berlin", BERLIN, "2026-09-27T14:00:00Z", "German and Russian foreign ministers held rare talks.",
              type_="diplomacy", country="DE", parties=["DE", "RU", "EE"])
    out = merge.merge([], [cand(a), cand(b), cand(c)])
    assert sorted(len(e["reports"]) for e in out) == [1, 2]  # a+b; c's wording is too different at that distance
    assert len(merge.merge([], [cand(a), cand(d)])) == 2  # not the same parties


def test_stored_talks_fold_only_with_close_wording():
    a = event("a", "Berlin", BERLIN, "2026-09-27T12:00:00Z", "Germany and Russia held rare talks amid tensions.",
              type_="diplomacy", country="DE", parties=["DE", "RU"])
    b = event("b", "Berlin", BERLIN, "2026-09-27T13:00:00Z", "Estonia criticized Germany and Russia's talks.",
              type_="diplomacy", country="DE", parties=["DE", "EE", "RU"])
    out, folded = merge.consolidate([a, b], set())
    assert folded == [] and len(out) == 2


ADDIS, ETHIOPIA, ISTANBUL, ATLANTIC = (9.04, 38.75), (10.21, 38.65), (41.01, 28.98), (36.9, -75.5)


def test_one_shared_word_is_not_similar_wording():
    ike = event("i", "Atlantic Ocean", ATLANTIC, "2026-09-29T03:00:00Z", "Four personnel injured in a mishap aboard "
                "the carrier USS Dwight D. Eisenhower.", type_="naval", country=None, approx=True)
    plane = event("p", "Istanbul", ISTANBUL, "2026-09-29T04:00:00Z", "An Iranian aircraft is seized in Istanbul.",
                  type_="naval", country="TR")
    assert not merge._similar(ike, plane)
    assert len(merge.merge([], [cand(ike), cand(plane)])) == 2


def test_country_level_fighting_needs_same_incident_confirmation():
    a = event("a", "Addis Ababa", ADDIS, "2026-09-29T08:19:00Z", "Fighting in Ethiopia intensifies amid ongoing "
              "conflict dynamics.", type_="ground", country="ET", theater="horn")
    b = event("b", "Ethiopia", ETHIOPIA, "2026-09-29T08:23:00Z", "Fighting in Ethiopia intensifies.",
              type_="ground", country="ET", theater="horn")
    assert len(merge.merge([], [cand(a), cand(b)])) == 2
    out, folded = merge.consolidate([a, b], set())
    assert len(out) == 2 and folded == []
    out, _ = dedupe._fold(out, [("a", "b")], set())
    assert [e["id"] for e in out] == ["a"]


def test_the_same_template_about_different_places_stays_apart():
    a = event("a", "Sumy Oblast", (51.0, 34.5), "2026-09-29T08:00:00Z", "Russian Sever group forces took control "
              "of Maryino in Sumy Oblast.", type_="territory", country="UA", theater="ukraine")
    b = event("b", "Ukraine", (48.4, 31.2), "2026-09-29T09:00:00Z", "Russian Sever group forces took control "
              "of Petropavlivka and Lozova in Sumy Oblast.", type_="territory", country="UA", theater="ukraine")
    out, folded = merge.consolidate([a, b], set())
    assert folded == []


def test_a_report_citing_ukmto_leads_a_shipping_incident():
    e = event("s", "Gulf of Aden", (12.5, 47.5), "2026-09-29T05:00:00Z", "A tanker was hit by a projectile.",
              type_="naval", country=None, theater="mideast")
    e["reports"][0]["weight"] = 3
    e["reports"].append(dict(e["reports"][0], url="https://example.com/ukmto", weight=1, time="2026-09-29T06:00:00Z",
                             summary="UKMTO reports a vessel hit by an unknown projectile 40 nautical miles east of Aden."))
    assert merge._headline(e)["url"] == "https://example.com/ukmto"


def test_two_events_from_one_roundup_article_get_different_ids():
    import merge as m
    a = {"id": "x", "summary": "US-China summit in Beijing.", "time": "2026-09-27T01:40:00Z", "reports": [{"url": "u"}, {"url": "v"}]}
    b = {"id": "x", "summary": "Erdogan met Bangladesh's leader in New York.", "time": "2026-09-27T00:00:00Z", "reports": [{"url": "u"}]}
    events = [b, a]
    assert m.unique_ids(events) == 1
    assert a["id"] == "x" and b["id"] != "x"          # the one with more reports keeps its id
    assert m.unique_ids(events) == 0                  # stable afterwards
    taken = {"id": m.short_hash("event", "u")}
    assert m._new_id([], "u", "another meeting") == taken["id"]
    assert m._new_id([taken], "u", "another meeting") != taken["id"]


def test_recent_deployments_into_another_country_are_read_again_once():
    from datetime import datetime, timezone
    import merge as m
    now = datetime(2026, 10, 7, 12, tzinfo=timezone.utc)
    rep = lambda u: {"source": "Reuters", "url": u, "summary": "German F-35s landed at Fort Smith for training.", "time": "2026-10-06T10:00:00Z"}
    abroad = {"id": "a", "type": "deployment", "country": "US", "attacker": "DE", "parties": ["DE", "US"], "time": "2026-10-06T10:00:00Z",
              "summary": "German F-35s landed at Fort Smith.", "reports": [rep("u1"), rep("u2")]}
    home = {"id": "h", "type": "deployment", "country": "EE", "attacker": "EE", "parties": ["EE"], "time": "2026-10-06T10:00:00Z",
            "summary": "Estonia moved troops closer to the Russian border.", "reports": [rep("u3")]}
    old = {**abroad, "id": "o", "time": "2026-09-30T10:00:00Z"}
    strike = {"id": "s", "type": "airstrike", "country": "YE", "attacker": "SA", "parties": ["SA"], "time": "2026-10-06T10:00:00Z", "reports": []}
    state = {}
    events, items = m.reread_foreign_deployments([abroad, home, old, strike], state, now)
    assert [e["id"] for e in events] == ["h", "o", "s"]          # only the recent foreign deployment goes back
    assert [i["url"] for i in items] == ["u1", "u2"] and all(not i["prefilter"] for i in items)
    assert m.reread_foreign_deployments([abroad], state, now) == ([abroad], [])   # once
    # forces sent into another country are not "arms production", whatever words the report uses
    sent = {"id": "t", "type": "arms_transfer", "country": "PL", "summary": "US troops receive orders to deploy to Poland.",
            "transfer": {"supplier": "US", "recipient": "US", "to": {"place": "Poland", "lat": 52, "lon": 19}}}
    built = {"id": "b", "type": "arms_transfer", "country": "TW", "summary": "Taiwan orders more anti-ship missiles from its industry.",
             "transfer": {"supplier": "TW", "recipient": "TW"}}
    assert m.own_procurement([sent, built]) == 1 and sent["type"] == "arms_transfer" and built["type"] == "production"


def test_a_countrys_own_purchases_and_production_are_arms_production():
    import merge as m
    def own(i, summary, **t):
        return {"id": i, "type": "arms_transfer", "summary": summary, "place": "Taipei",
                "transfer": {"supplier": "TW", "recipient": "TW", **t}}
    build = own("b", "Taiwan announces plans to build more anti-ship missiles.", to={"place": "Taipei", "lat": 25.0, "lon": 121.5})
    contract = own("c", "Finland and Sweden sign contracts to procure armored personnel carriers.")
    move = own("m", "US transfers six F-16s from Aviano to CENTCOM operations.",
               **{"from": {"place": "Aviano"}, "to": {"place": "Middle East (CENTCOM area)"}})
    withdraw = own("w", "Ethiopia plans to withdraw 3,000 troops from Somalia.", to={"place": "Ethiopia"})
    assert m.own_procurement([build, contract, move, withdraw]) == 2
    assert build["type"] == contract["type"] == "production" and build["transfer"] is None and build["country"] == "TW"
    assert move["type"] == withdraw["type"] == "arms_transfer"


def test_production_reports_merge_into_one_event():
    import merge as m
    assert m.FAMILY["production"] == "production" and m.RADIUS_KM["production"] > 0


def test_launches_into_one_sea_are_one_wave_whatever_country_the_reports_give():
    # North Korea's launch came in "toward the Sea of Japan" with country JP; Seoul's outlets say
    # "East Sea", with KR or no country
    jp = event("j", "Sea of Japan", (40.0, 135.0), "2026-10-02T21:42:00Z", "North Korea launched missiles toward the Sea of Japan.",
               type_="missile_drone", country="JP", attacker="KP", theater="indopac")
    kr = event("k", "East Sea", (40.0, 135.0), "2026-10-02T22:10:00Z", "North Korea fired a ballistic missile into the East Sea, JCS says.",
               type_="missile_drone", country="KR", attacker="KP", theater="indopac")
    none = event("n", "Sea of Japan", (40.0, 135.0), "2026-10-02T22:30:00Z", "Japan says a North Korean missile fell outside its EEZ.",
                 type_="missile_drone", country=None, attacker="KP", theater="indopac")
    out = merge.merge([], [cand(jp), cand(kr), cand(none)])
    assert len(out) == 1 and out[0]["wave"] and len(out[0]["reports"]) == 3


def test_a_launch_report_becomes_the_waves_launch_area():
    # "fired a ballistic missile from Wonsan" was its own event pinned at Wonsan, and the wave's line
    # started from an assumed launch area
    w = event("w", "Sea of Japan", (40.0, 135.0), "2026-10-02T21:42:00Z", "North Korea launched missiles toward the Sea of Japan.",
              type_="missile_drone", country="JP", attacker="KP", theater="indopac", wave=True,
              targets=[{"place": "Sea of Japan", "lat": 40.0, "lon": 135.0}])
    site = event("s", "Wonsan", (39.17, 127.43), "2026-10-02T22:47:00Z", "South Korea's JCS reports North Korea fired a ballistic missile from Wonsan.",
                 type_="missile_drone", country="KP", attacker="KP", theater="indopac")
    test = event("t", "Pyongyang", (39.03, 125.75), "2026-10-02T22:50:00Z", "North Korea held a missile parade in Pyongyang.",
                 type_="missile_drone", country="KP", attacker="KP", theater="indopac")
    out, folded = merge.launch_sites([w, site, test], set())
    assert [e["id"] for e in folded] == ["s"] and {e["id"] for e in out} == {"w", "t"}
    assert w["origins"] == [{"place": "Wonsan", "lat": 39.17, "lon": 127.43}] and len(w["reports"]) == 2
    denial = event("d", "Tehran", (35.69, 51.39), "2026-10-02T22:50:00Z",
                   "A military source denies reports that a missile was launched from Iran toward Jordan.",
                   type_="missile_drone", country="IR", attacker="IR", theater="mideast")
    assert not merge._launch_report(denial)


def test_talks_with_one_party_list_within_the_other_and_close_wording_merge():
    # "Pakistan announces Mecca pact talks on the Houthis" came in with [PK] and with [PK, SA, YE]
    a = event("a", "Islamabad", (33.69, 73.06), "2026-10-02T04:58:00Z",
              "Pakistan announces that members of the Mecca pact will convene for emergency talks regarding the Houthis.",
              type_="diplomacy", country="PK", theater="mideast", parties=["PK", "SA", "YE"])
    b = event("b", "Islamabad", (33.69, 73.06), "2026-10-02T14:43:00Z",
              "Pakistan announces that Makkah pact members will hold emergency talks on engaging the Houthis.",
              type_="diplomacy", country="PK", theater="mideast", parties=["PK"])
    c = event("c", "Islamabad", (33.69, 73.06), "2026-10-02T15:00:00Z",
              "Pakistan's prime minister met Chinese investors about a new rail line.",
              type_="diplomacy", country="PK", theater="mideast", parties=["PK"])
    out, folded = merge.consolidate([a, b, c], set())
    assert [e["id"] for e in folded] == ["b"] and {e["id"] for e in out} == {"a", "c"}
    cabinet = event("k", "Riyadh", (24.71, 46.68), "2026-10-02T10:00:00Z",
                    "Saudi Crown Prince MBS chaired a cabinet meeting in Riyadh where the cabinet stated its position.",
                    type_="diplomacy", country="SA", theater="mideast", parties=["SA"])
    visit = event("v", "Riyadh", (24.71, 46.68), "2026-10-02T09:00:00Z", "Saudi crown prince and UAE vice president meet in Riyadh.",
                  type_="diplomacy", country="SA", theater="mideast", parties=["AE", "SA"])
    assert not merge._same_talks(visit, cabinet)


def test_a_waves_place_keeps_its_first_and_latest_report_time():
    first = wave("a", "2026-09-28T08:53:00Z", [("Erbil", (36.19, 44.01))])
    later = wave("b", "2026-09-28T11:33:00Z", [("Erbil", (36.19, 44.01))])
    out = merge.merge([], [cand(later), cand(first)])
    t = out[0]["targets"][0]
    assert t["time"] == "2026-09-28T08:53:00Z" and t["last"] == "2026-09-28T11:33:00Z" and t["reports"] == 2
