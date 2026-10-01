"""Google News reports are credited to the outlet that published them, and reports of one
military build-up merge into one event even when they are pinned far apart."""
from datetime import datetime, timezone

import hunter
import merge
from sources import rss

NOW = datetime(2026, 9, 27, 23, 0, tzinfo=timezone.utc)
OUTLETS = {"cbsnews.com": {"domain": "cbsnews.com", "name": "CBS News", "tier": 1},
           "apnews.com": {"domain": "apnews.com", "name": "AP", "tier": 1}}
FEED = ("<?xml version='1.0'?><rss version='2.0'><channel><title>t</title>"
        "<item><title>US military lays groundwork for Cuba action - CBS News</title>"
        "<link>https://news.google.com/rss/articles/cbs1?oc=5</link><source url='https://www.cbsnews.com'>CBS News</source>"
        "<pubDate>Sun, 27 Sep 2026 20:00:00 GMT</pubDate></item>"
        "<item><title>Military prepares for Cuba - WINK News</title>"
        "<link>https://news.google.com/rss/articles/wink1?oc=5</link><source url='https://www.winknews.com'>WINK News</source>"
        "<pubDate>Sun, 27 Sep 2026 22:00:00 GMT</pubDate></item></channel></rss>").encode()


class Session:
    def get(self, url, timeout=0):
        self.url = url
        return self

    def raise_for_status(self):
        pass

    content = FEED


SEARCH = {"id": "gnews-latam-us", "name": "Google News (US military in Latin America)", "kind": "news",
          "group": "google-news", "weight": 2,
          "url": "https://news.google.com/rss/search?q=Cuba+military+when%3A12h&hl=en-US&gl=US&ceid=US%3Aen"}


def report(url, source="Google News (US military in Latin America)"):
    return {"source": source, "platform": "rss", "kind": "news", "side": None, "group": "google-news",
            "weight": 2, "url": url, "time": "2026-09-27T20:00:00Z", "summary": "x"}


def test_old_reports_are_credited_to_their_outlet():
    s = Session()
    labels = rss.fetch_outlet_labels([SEARCH, {"name": "Reuters RSS", "url": "https://example.com/feed"}], s, OUTLETS, 7)
    assert "when%3A7d" in s.url
    stored = [report("https://news.google.com/rss/articles/cbs1?oc=5"),
              report("https://news.google.com/rss/articles/wink1?oc=5"),
              report("https://news.google.com/rss/articles/unknown?oc=5")]
    assert rss.relabel(stored, labels) == 2
    cbs, wink, unknown = stored
    assert (cbs["source"], cbs["group"], cbs["weight"]) == ("CBS News (via Google News)", "outlet:cbsnews.com", 3)
    assert (wink["source"], wink["group"]) == ("WINK News (via Google News)", "google-news")   # unlisted: shared group
    assert unknown["source"] == "Google News (US military in Latin America)"                 # not found: unchanged
    assert rss.relabel(stored, labels) == 0                                                    # nothing left to do


def test_hunter_results_count_per_listed_outlet():
    items = hunter.run([{"id": "e", "type": "deployment", "place": "Cuba", "severity": 2, "status": "unconfirmed",
                         "time": "2026-09-27T21:00:00Z"}], {}, Session(), NOW, OUTLETS)
    by = {i["source"]: i for i in items}
    assert by["CBS News (via Google News)"]["group"] == "outlet:cbsnews.com"
    assert by["CBS News (via Google News)"]["weight"] == 4       # keeps the hunter's queue priority
    assert by["WINK News (via Google News)"]["group"] == "google-news"


def event(i, place, lat, lon, time, summary, approx=False, attacker="US", country="CU", type_="deployment", reports=None):
    return {"id": i, "alert": False, "theater": "latam", "type": type_, "summary": summary, "place": place,
            "country": country, "attacker": attacker, "lat": lat, "lon": lon, "approx": approx, "origins": [],
            "parties": [], "severity": 2, "killed": None, "injured": None, "time": time, "updated": time,
            "reports": reports or [report(f"https://example.com/{i}")]}


HAVANA = (23.1353, -82.359)
CUBA = (21.52, -77.78)   # the country's centre, about 500 km from Havana


def cand(e):
    return {**e, "report": e["reports"][0]}


def test_buildup_reports_merge_however_far_apart_the_pins_are():
    first = event("a", "Havana", *HAVANA, "2026-09-27T07:00:00Z", "US military weighs possible action around Cuba.")
    later = event("b", "Cuba", *CUBA, "2026-09-27T22:00:00Z",
                  "The U.S. military is laying the groundwork for potential action around Cuba.", approx=True)
    out = merge.merge([first], [cand(later)])
    assert len(out) == 1 and len(out[0]["reports"]) == 2


def test_buildup_rule_needs_same_actor_same_country_and_similar_wording():
    first = event("a", "Havana", *HAVANA, "2026-09-27T07:00:00Z", "US military weighs possible action around Cuba.")
    for other in (event("b", "Cuba", *CUBA, "2026-09-27T09:00:00Z", "Russia sends a naval group to Cuba for a visit.",
                        attacker="RU"),                                                        # someone else's forces
                  event("c", "Cuba", *CUBA, "2026-09-27T09:00:00Z", "Cuban forces hold a coastal defense drill."),
                  event("d", "Cuba", *CUBA, "2026-09-28T09:00:00Z", "US military weighs possible action around Cuba."),
                  event("e", "Cuba", *CUBA, "2026-09-27T09:00:00Z", "US military weighs possible action around Cuba.",
                        type_="airstrike")):                                                   # not a deployment
        assert len(merge.merge([dict(first, reports=list(first["reports"]))], [cand(other)])) == 2, other["id"]


def test_consolidate_folds_stored_split_events_and_keeps_the_earliest_id():
    a = event("a", "Havana", *HAVANA, "2026-09-27T07:00:00Z", "US military weighs possible action around Cuba.")
    b = event("b", "Cuba", *CUBA, "2026-09-27T22:00:00Z", "US military lays groundwork for possible action around Cuba.",
              approx=True)
    c = event("c", "Santiago de Cuba", 20.02, -75.82, "2026-09-27T12:00:00Z", "Cuba announces a coastal defense drill.")
    hidden = event("h", "Havana", *HAVANA, "2026-09-27T08:00:00Z", "US military weighs possible action around Cuba.")
    out, folded = merge.consolidate([b, c, a, hidden], skip={"h"})
    assert [e["id"] for e in out] == ["c", "a", "h"] and [e["id"] for e in folded] == ["b"]
    assert len(a["reports"]) == 2 and a["updated"] == "2026-09-27T22:00:00Z" and a["time"] == "2026-09-27T07:00:00Z"
    assert merge.consolidate(out, skip={"h"})[1] == []                       # nothing left to fold


def test_status_counts_listed_outlets_separately():
    e = event("a", "Havana", *HAVANA, "2026-09-27T07:00:00Z", "x", reports=[
        {**report("u1"), "source": "CBS News (via Google News)", "group": "outlet:cbsnews.com"},
        {**report("u2"), "source": "AP (via Google News)", "group": "outlet:apnews.com"},
        {**report("u3"), "source": "WINK News (via Google News)"}])
    merge.apply_status([e], [])
    assert e["status"] == "corroborated" and e["sources_count"] == 2


def test_outlets_are_known_by_the_name_google_news_gives_them():
    import config
    from sources import rss
    outlets = config.load().outlets
    assert rss.outlet_named("Українські Національні Новини (УНН)", outlets)["domain"] == "unn.ua"
    assert rss.outlet_named("Yahoo", outlets) is None                     # republishes others: not listed
    reports = [{"source": n + " (via Google News)", "group": "google-news", "kind": "news", "side": None, "weight": 2}
               for n in ("Colorado Politics", "denvergazette.com", "Yahoo", "Newsmax", "IntelliNews")]
    assert rss.relabel_by_name(reports, outlets) == 4
    assert [r["group"] for r in reports] == ["clarity-media", "clarity-media", "google-news",
                                             "outlet:newsmax.com", "outlet:intellinews.com"]
    assert rss.relabel_by_name(reports, outlets) == 0                     # done once
