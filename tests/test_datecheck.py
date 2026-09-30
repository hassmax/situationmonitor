"""Article date check: the publication date an article's own page gives."""
import base64
from datetime import datetime, timedelta, timezone

import datecheck

NOW = datetime(2026, 9, 30, 4, 0, tzinfo=timezone.utc)


class Resp:
    def __init__(self, html, status=200):
        self.status_code, self.encoding = status, "utf-8"
        self.raw = type("Raw", (), {"read": lambda _s, n, decode_content=True: html.encode()})()
        self.text = html


class Session:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def get(self, url, **k):
        self.calls.append(url)
        return Resp(self.pages.get(url, ""), 200 if url in self.pages else 404)


def google_link(url):
    record = b'\x08\x13"' + bytes([len(url)]) + url.encode() + b"\xd2\x01\x00"
    return "https://news.google.com/rss/articles/" + base64.urlsafe_b64encode(record).decode().rstrip("=") + "?oc=5"


def event(url, hours_ago=6, severity=3):
    t = (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"id": "e", "summary": "Yemen's Houthis launched ballistic missiles at Israel.", "severity": severity,
            "time": t, "reports": [{"platform": "rss", "url": url, "time": t}]}


def test_publication_date_is_read_from_meta_tags_and_structured_data():
    assert datecheck.published_in('<meta property="article:published_time" content="2026-03-14T08:00:00+05:30">') \
        == datetime(2026, 3, 14, 2, 30, tzinfo=timezone.utc)
    assert datecheck.published_in('<meta content="2026-03-14" name="pubdate">').date().isoformat() == "2026-03-14"
    assert datecheck.published_in('<script>{"@type":"NewsArticle","datePublished":"2026-03-14T10:00:00Z"}</script>') \
        == datetime(2026, 3, 14, 10, tzinfo=timezone.utc)
    assert datecheck.published_in("<p>no date here</p>") is None


def test_old_google_news_ids_carry_the_address():
    link = google_link("https://www.mid-day.com/news/houthis-missiles.html")
    assert datecheck._publisher_url(Session({}), link) == "https://www.mid-day.com/news/houthis-missiles.html"


def test_an_old_article_listed_with_a_fresh_date_is_dropped():
    article = "https://www.mid-day.com/news/houthis-missiles.html"
    s = Session({article: '<meta property="article:published_time" content="2026-03-14T08:00:00Z">'})
    state = {}
    out = datecheck.check([event(google_link(article))], s, state, NOW)
    assert out == [] and state["dropped_as_old"][0]["match_date"] == "2026-03-14"


def test_a_few_days_old_article_moves_the_event_back_to_its_date():
    article = "https://example.com/a.html"
    s = Session({article: '<meta property="article:published_time" content="2026-09-27T10:00:00Z">'})
    out = datecheck.check([event(article)], s, {}, NOW)
    assert out[0]["time"] == "2026-09-27T10:00:00Z"


def test_unreadable_pages_change_nothing_and_are_tried_twice_at_most():
    e = event("https://blocked.example.com/a.html")
    s = Session({})
    out = datecheck.check([e], s, {}, NOW)
    assert out == [e] and e["time"] == event("x")["time"]
    datecheck.check(out, s, {}, NOW)
    datecheck.check(out, s, {}, NOW)
    assert len(s.calls) == 2


def test_only_notable_single_report_news_events_are_opened():
    s = Session({})
    minor = event("https://example.com/a.html", severity=1)
    two = dict(event("https://example.com/b.html"), reports=event("u")["reports"] * 2)
    old = event("https://example.com/c.html", hours_ago=24 * 5)
    datecheck.check([minor, two, old], s, {}, NOW)
    assert s.calls == []


def test_a_photo_is_dated_by_when_it_was_taken():
    html = ('<meta property="article:published_time" content="2026-09-29T20:00:00Z">'
            '<div><b>Date Taken:</b> 06.12.2026</div>')
    assert datecheck.published_in(html) == datetime(2026, 6, 12, tzinfo=timezone.utc)


def test_military_sites_are_checked_whatever_the_severity():
    photo = event("https://www.cpf.navy.mil/Newsroom/Photos/igphoto/123/", severity=1)
    photo["reports"][0]["source"] = "cpf.navy.mil (via Google News)"
    s = Session({"https://www.cpf.navy.mil/Newsroom/Photos/igphoto/123/": "<b>Date Taken:</b> 06.12.2026"})
    state = {}
    assert datecheck.check([photo], s, state, NOW) == [] and state["dropped_as_old"][0]["match_date"] == "2026-06-12"


def test_the_most_serious_and_longest_waiting_are_opened_first(monkeypatch):
    monkeypatch.setattr(datecheck, "PER_RUN", 2)
    s = Session({})
    evs = [dict(event(f"https://example.com/{i}", hours_ago=h, severity=sev), id=str(i))
           for i, (h, sev) in enumerate([(1, 2), (20, 2), (5, 3), (2, 2)])]
    datecheck.check(evs, s, {}, NOW)
    assert s.calls == ["https://example.com/2", "https://example.com/1"]  # severity 3, then the oldest


def test_no_new_article_is_opened_after_the_time_budget(monkeypatch):
    clock = iter([0, 0, 50, 50, 50])
    monkeypatch.setattr(datecheck.time, "monotonic", lambda: next(clock))
    s = Session({})
    evs = [dict(event(f"https://example.com/{i}", hours_ago=i + 1), id=str(i)) for i in range(3)]
    datecheck.check(evs, s, {}, NOW)
    assert len(s.calls) == 1
