"""2026-10-04: arms transfers and own-force moves merge only on the same route, within 72 hours of
when the move began. One "US forces moved" event had absorbed 23 reports, from the Iraq withdrawal
(29 September) to B-1 bombers leaving RAF Fairford (4 October), under a date that kept them off the map."""
from datetime import datetime, timezone

import merge

NOW = datetime(2026, 10, 4, 19, 0, tzinfo=timezone.utc)
FAIRFORD = {"place": "RAF Fairford", "lat": 51.682, "lon": -1.79}
US = {"place": "United States", "lat": 38.9, "lon": -77.0}
CENTCOM = {"place": "Middle East (CENTCOM area)", "lat": 27.0, "lon": 51.0, "region": True}
EIELSON = {"place": "Eielson Air Force Base", "lat": 64.67, "lon": -147.08}


def report(url, time, summary, source="Shin (Persian-language OSINT)", group="shin"):
    return {"source": source, "platform": "telegram", "kind": "osint", "side": None, "group": group, "weight": 2,
            "url": url, "time": time, "summary": summary}


def move(i, time, frm, to, place, lat, lon, summary, reports=None):
    return {"id": i, "type": "arms_transfer", "theater": "mideast", "time": time, "updated": time, "summary": summary,
            "place": place, "lat": lat, "lon": lon, "country": "US", "severity": 1, "approx": False,
            "transfer": {"kind": "delivery", "supplier": "US", "recipient": "US", "mode": "air", "from": frm, "to": to, "via": []},
            "reports": reports or [report(f"https://t.me/x/{i}", time, summary)]}


def cand(time, frm, to, place, lat, lon, summary):
    c = move("new", time, frm, to, place, lat, lon, summary)
    return {**c, "report": c["reports"][0]}


def test_own_moves_on_other_routes_stay_apart():
    old = move("iraq", "2026-10-03T17:00:00Z", CENTCOM, EIELSON, "Eielson Air Force Base", 64.67, -147.08, "KC-135s return home from CENTCOM.")
    b1 = cand("2026-10-04T11:11:00Z", FAIRFORD, US, "RAF Fairford", 51.68, -1.79, "Three B-1B bombers left RAF Fairford for the US.")
    assert merge._find_transfer([old], b1) is None
    same = cand("2026-10-04T08:00:00Z", CENTCOM, EIELSON, "Eielson Air Force Base", 64.67, -147.08, "More KC-135s returned to Eielson.")
    assert merge._find_transfer([old], same) is old


def test_the_window_counts_from_when_the_move_began():
    first = move("b1", "2026-10-01T11:00:00Z", FAIRFORD, US, "RAF Fairford", 51.68, -1.79, "B-1s left Fairford.")
    first["updated"] = "2026-10-04T10:00:00Z"   # later reports must not keep it open
    later = cand("2026-10-04T17:00:00Z", FAIRFORD, US, "RAF Fairford", 51.68, -1.79, "Ten B-1s left Fairford.")
    assert merge._find_transfer([first], later) is None


def test_a_move_naming_no_ends_needs_the_same_place():
    a = move("a", "2026-10-04T10:00:00Z", None, None, "RAF Fairford", 51.68, -1.79, "US bombers moving.")
    near = cand("2026-10-04T12:00:00Z", None, None, "RAF Fairford", 51.70, -1.80, "US bombers moving.")
    far = cand("2026-10-04T12:00:00Z", None, None, "Guam", 13.5, 144.8, "US bombers moving.")
    assert merge._find_transfer([a], near) is a and merge._find_transfer([a], far) is None


def test_overgrown_transfers_are_split_once_and_reread():
    reps = [report("https://t.me/shin/1", "2026-09-29T17:42:56Z", "Four KC-135s are returning home from CENTCOM bases."),
            report("https://news.example/2", "2026-09-30T19:20:00Z", "The US withdrew its remaining troops from Iraq.", "KUTV (via Google News)", "google-news"),
            report("https://t.me/shin/3", "2026-10-04T11:11:11Z", "Three B-1B Lancer bombers departed RAF Fairford to return to the United States."),
            report("https://bsky.app/profile/chadbourn.bsky.social/post/x", "2026-10-04T17:11:28Z",
                   "Ten USAF B-1B Lancer bombers left RAF Fairford for bases in the United States.", "Mark Chadbourn (@chadbourn.bsky.social, Bluesky)", "bluesky-search")]
    big = move("84d05370d95a", "2026-09-29T17:42:56Z", CENTCOM, EIELSON, "Eielson Air Force Base", 64.67, -147.08, "The US completed its Iraq troop withdrawal.", reps)
    state = {}
    items = merge.split_overgrown_transfers([big], state, NOW)
    assert [r["url"] for r in big["reports"]] == ["https://t.me/shin/1"]          # its first report stays
    assert big["updated"] == "2026-09-29T17:42:56Z"
    assert [i["text"] for i in items] == [r["summary"] for r in reps[1:]]
    assert items[2]["source"].startswith("Mark Chadbourn") and items[2]["group"] == "bluesky-search" and not items[2]["prefilter"]
    within = move("ok", "2026-10-04T10:00:00Z", FAIRFORD, US, "RAF Fairford", 51.68, -1.79, "B-1s left.",
                  [report("u1", "2026-10-04T10:00:00Z", "a"), report("u2", "2026-10-05T09:00:00Z", "b")])
    assert merge.split_overgrown_transfers([within], {}, NOW) == [] and len(within["reports"]) == 2
    assert items[0]["max_age_h"] > 24 * 7                      # older than the usual 36 hours, still read
    assert merge.split_overgrown_transfers([big], state, NOW) == []   # once
