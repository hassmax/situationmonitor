from datetime import datetime, timedelta, timezone

import extract
import hunter

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)


def ev(i, hours_ago=2, **kw):
    t = (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"id": i, "type": "naval", "place": "Strait of Hormuz", "severity": 2, "status": "unconfirmed", "time": t, **kw}


def test_query_building():
    assert hunter.query(ev("a")) == '"Strait of Hormuz" (ship OR vessel OR tanker) when:1d'
    assert hunter.query(ev("b", type="missile_drone", place="Kyiv (Obolon)")) == '"Kyiv" (drone OR missile) when:1d'
    assert hunter.query(ev("c", type="artillery", place="Kherson, Ukraine")) == '"Kherson" (shelling OR artillery) when:1d'
    assert hunter.query(ev("d", place=None)) is None


def test_picks_only_important_uncorroborated_recent_events():
    events = [ev("ok"), ev("claimed", status="claimed"), ev("corroborated", status="corroborated"),
              ev("minor", severity=1), ev("old", hours_ago=13), ev("alerts", alert=True), ev("hidden", hidden="note")]
    assert sorted(e["id"] for e in hunter.pick(events, {}, NOW)) == ["claimed", "ok"]


def test_at_most_eight_most_severe_first():
    events = [ev(f"s2-{i}", hours_ago=i / 10) for i in range(8)] + [ev("s3", severity=3, hours_ago=5)]
    picked = hunter.pick(events, {}, NOW)
    assert len(picked) == 8 and picked[0]["id"] == "s3"


class Session:
    def __init__(self, fail=False):
        self.urls, self.fail = [], fail

    def get(self, url, timeout=0):
        self.urls.append(url)
        if self.fail:
            raise OSError("down")
        return self

    def raise_for_status(self):
        pass

    content = ("<?xml version='1.0'?><rss version='2.0'><channel><title>t</title>"
               "<item><title>Tanker hit near Hormuz - Reuters</title><link>https://news.google.com/rss/articles/x1</link>"
               "<pubDate>Sun, 27 Sep 2026 11:00:00 GMT</pubDate></item>"
               "<item><title>Old tanker story - X</title><link>https://news.google.com/rss/articles/x2</link>"
               "<pubDate>Wed, 23 Sep 2026 11:00:00 GMT</pubDate></item></channel></rss>").encode()


def test_results_join_the_queue_credited_to_their_outlet():
    items = hunter.run([ev("a")], {}, Session(), NOW)
    assert [i["text"] for i in items] == ["Tanker hit near Hormuz - Reuters"]    # older than a day dropped
    it = items[0]
    # an outlet not listed in sources.yaml stays in the shared google-news group
    assert (it["source"], it["group"], it["platform"]) == ("Reuters (via Google News)", "google-news", "rss")
    # highest priority in the normal extraction queue
    queue = extract.build_queue([], [{**it, "weight": 1, "id": "other", "time": it["time"]}, it], NOW,
                                {"max_item_age_hours": 36, "pending_max": 400})
    assert queue[0]["id"] == it["id"]


def test_each_event_searched_at_most_twice_three_hours_apart():
    state, s = {}, Session()
    hunter.run([ev("a")], state, s, NOW)
    hunter.run([ev("a")], state, s, NOW + timedelta(hours=2))          # too soon
    assert len(s.urls) == 1
    hunter.run([ev("a")], state, s, NOW + timedelta(hours=3))
    hunter.run([ev("a")], state, s, NOW + timedelta(hours=7))          # already searched twice
    assert len(s.urls) == 2


def test_failed_search_is_not_counted():
    state = {}
    assert hunter.run([ev("a")], state, Session(fail=True), NOW) == []
    assert "a" not in state.get("hunter", {})
    assert len(hunter.pick([ev("a")], state, NOW)) == 1


def test_makes_no_model_calls():
    assert "ask" not in hunter.run.__code__.co_varnames and "extract" not in hunter.__dict__
