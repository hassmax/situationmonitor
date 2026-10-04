from datetime import datetime, timedelta, timezone

import recency

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
SETTINGS = {"daily_llm_calls": 470, "max_calls_per_run": 6}


def ev(i, summary, hours_ago=5):
    t = (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"id": i, "summary": summary, "place": "Tehran", "time": t,
            "reports": [{"platform": "rss", "url": f"https://news.google.com/rss/articles/{i}"}]}


def test_news_only_waves_are_checked_but_live_ones_and_alerts_are_not():
    wave = dict(ev("w", "Iran targets sites in Bahrain and Kuwait."), wave=True)
    live = dict(wave, id="l", reports=wave["reports"] + [{"platform": "telegram", "url": "https://t.me/x/1"}])
    alert = dict(wave, id="a", wave=False, alert=True)
    assert recency._needs_check(wave)
    assert not recency._needs_check(live) and not recency._needs_check(alert)


def rss(items):
    body = "".join(f"<item><title>{t}</title><link>https://example.com/{n}</link><pubDate>{d}</pubDate></item>"
                   for n, (t, d) in enumerate(items))
    return f"<?xml version='1.0'?><rss version='2.0'><channel><title>t</title>{body}</channel></rss>".encode()


class Session:
    def __init__(self, recent, older):
        self.recent, self.older, self.urls = recent, older, []

    def get(self, url, timeout=0):
        self.urls.append(url)
        self.content = rss(self.older if "before%3A" in url else self.recent)
        return self

    def raise_for_status(self):
        pass


TODAY = "Mon, 28 Sep 2026 06:00:00 GMT"
MARCH = "Tue, 03 Mar 2026 10:00:00 GMT"


def test_recycled_headlines_do_not_count_as_current_and_the_model_decides():
    e = ev("x", "Israel reported fresh airstrikes on Iranian targets east of Tehran.")
    s = Session(recent=[("Israel reports fresh airstrikes on Iranian targets east of Tehran - News On AIR", TODAY),
                        ("Israel strikes Iranian targets east of Tehran - Indian Express", TODAY)],
                older=[("Israel reports fresh airstrikes on Iranian targets east of Tehran - Reuters", MARCH)])
    seen = []

    def ask(prompt, text, *a, **k):
        seen.append(text)
        return {"results": [{"i": 0, "old": True, "match": 0}]}
    state = {}
    out = recency.check([e], {"x"}, s, ask, state, SETTINGS, NOW)
    assert out == [] and state["dropped_as_old"][0]["match_date"] == "2026-03-03"
    assert "News On AIR" not in seen[0]            # the recycled copy was set aside, not shown as current


def test_no_older_coverage_keeps_the_event_without_a_model_call():
    e = ev("y", "Drones struck a fuel depot near Tabriz overnight.")
    s = Session(recent=[("Drones hit fuel depot near Tabriz - AP", TODAY)], older=[])
    out = recency.check([e], {"y"}, s, lambda *a, **k: 1 / 0, {}, SETTINGS, NOW)
    assert out == [e] and e["checked"] == recency.CHECK_VERSION


def test_model_call_waits_when_only_a_few_events_need_checking():
    e = ev("z", "Israel reported fresh airstrikes on Iranian targets east of Tehran.")
    state = {"recency_asked": (NOW - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")}
    s = Session(recent=[], older=[])
    assert recency.check([e], {"z"}, s, lambda *a, **k: 1 / 0, state, SETTINGS, NOW) == [e]
    assert s.urls == [] and "checked" not in e


def test_drops_under_the_previous_rule_stand():
    old = {"summary": "s", "listed": "2026-09-26", "match": "m", "match_date": "2026-03-02", "version": 2,
           "event": ev("d", "Old story")}
    state = {"dropped_as_old": [old], "recency_asked": NOW.strftime("%Y-%m-%dT%H:%M:%SZ")}
    assert recency.check([], set(), Session([], []), lambda *a, **k: None, state, SETTINGS, NOW) == []
    assert state["dropped_as_old"] == [old]


def test_a_single_source_story_with_only_older_coverage_is_held_until_a_second_source():
    e = dict(ev("h", "Yemen's Houthis launched ballistic missiles at Israel."), severity=3,
             coverage={"current": 0, "older": 5})
    e["reports"][0].update(group="google-news", source="Mid-Day (via Google News)")
    assert recency.held(e)
    assert not recency.held(dict(e, coverage={"current": 3, "older": 5}))  # covered now elsewhere
    assert not recency.held(dict(e, coverage={"current": 0, "older": 0}))  # nothing older: new
    assert not recency.held(dict(e, severity=1))                           # minor items aren't held
    second = dict(e, reports=e["reports"] + [{"platform": "rss", "group": "reuters", "url": "u2"}])
    assert not recency.held(second)                                        # a second source: shown


def test_an_article_whose_date_could_not_be_read_is_held_despite_one_current_match():
    e = dict(ev("h", "Yemen's Houthis launched ballistic missiles at Israel."), severity=2,
             coverage={"current": 1, "older": 8})
    e["reports"][0].update(group="google-news", source="Mid-Day (via Google News)")
    assert not recency.held(e)                                                           # not checked yet
    assert not recency.held(dict(e, dated={"published": None, "tries": 1}))              # one more try left
    assert recency.held(dict(e, dated={"published": None, "tries": 2}))                  # gave up: flagged
    assert not recency.held(dict(e, dated={"published": "2026-09-29T20:00:00Z", "tries": 1}))  # dated: current
    assert not recency.held(dict(e, dated={"published": None, "tries": 2}, coverage={"current": 1, "older": 0}))


def test_the_same_outlet_republishing_is_not_current_coverage():
    assert recency._outlet("Mid-Day (via Google News)") == "mid-day"
    assert recency._outlet_of("Yemen's Houthis launch missiles at Israel - Mid-Day") == "mid-day"
