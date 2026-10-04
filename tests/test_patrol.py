"""2026-10-04: OSINT posts on force movements (OSINTtechnical: "At least 10 of the B-1s have departed
RAF Fairford") reach the map: aircraft words in the keyword filter, and a Bluesky search patrol."""
from datetime import datetime, timezone

import extract
import merge
from sources import bluesky
from test_outlets import HAVANA, event, report

NOW = datetime(2026, 10, 4, 17, 0, tzinfo=timezone.utc)


def test_aircraft_movements_pass_the_filter():
    for t in ["B-52 bombers arrive at RAF Fairford", "squadron of F-15Es redeploys", "Four KC-135s landed at Al Udeid",
              "Tu-95s took off from Engels", "F-35As arrive at Lakenheath"]:
        assert extract.CONFLICT_RE.search(t), t
        assert extract.rejected_before_added_words({"text": t}, 6), t   # seen posts get one more look
    for t in ["B2B sales up", "Airline adds flights to Paris", "RAFAEL wins contract"]:
        assert not extract.CONFLICT_RE.search(t), t
    assert extract.PREFILTER_VERSION == 7


class Resp:
    def __init__(self, data, status=200):
        self.data, self.status_code = data, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self.data


def post(handle, rkey, text, at="2026-10-04T16:30:00Z", name=None):
    return {"uri": f"at://did:plc:{handle}/app.bsky.feed.post/{rkey}", "author": {"handle": handle, "displayName": name},
            "record": {"text": text, "createdAt": at}}


class Session:
    def __init__(self, posts, followers):
        self.posts, self.followers, self.asked = posts, followers, []

    def get(self, url, params=None, **kw):
        self.asked.append(url)
        if url == bluesky.SEARCH_URL:
            return Resp({"posts": self.posts})
        return Resp({"profiles": [{"handle": h, "followersCount": self.followers.get(h, 0)} for _, h in params]})


def test_patrol_keeps_well_followed_and_listed_accounts():
    posts = [post("osint.bsky.social", "a1", "At least 10 of the B-1s have departed RAF Fairford", name="OSINT X"),
             post("nobody.bsky.social", "b1", "bombers over my house lol"),
             post("kyivindependent.com", "c1", "B-52 bombers seen over Poland"),
             post("osint.bsky.social", "a0", "Old bombers post", at="2026-10-03T01:00:00Z")]
    s = Session(posts, {"osint.bsky.social": 50000, "nobody.bsky.social": 40})
    listed = [{"handle": "kyivindependent.com", "name": "The Kyiv Independent", "kind": "news", "group": "kyivindependent"}]
    health = {}
    items = bluesky.search({"queries": ["bombers", "B-1B"], "min_followers": 2000}, listed, s, health, NOW)
    by = {i["url"].rsplit("/", 1)[-1]: i for i in items}
    assert set(by) == {"a1", "c1"}                    # a stranger with 40 followers and an old post are left out
    assert by["a1"]["group"] == bluesky.SEARCH_GROUP and by["a1"]["source"] == "OSINT X (@osint.bsky.social, Bluesky)"
    assert by["a1"]["url"] == "https://bsky.app/profile/osint.bsky.social/post/a1" and by["a1"]["prefilter"]
    assert by["c1"]["group"] == "kyivindependent" and by["c1"]["source_id"] == "bsky:kyivindependent.com"
    assert bluesky.SEARCH_ID in health
    # the same post found by two queries is one item
    assert len(items) == 2
    assert bluesky.search({"queries": []}, listed, s, {}, NOW) == []


def test_weak_sources_never_corroborate_each_other():
    found = {**report("https://bsky.app/profile/a/post/1"), "platform": "bluesky", "kind": "osint", "group": "bluesky-search"}
    e = event("a", "Fairford", *HAVANA, "2026-10-04T12:00:00Z", "x", reports=[found, report("https://example.com/g")])
    merge.apply_status([e], [])
    assert e["status"] == "unconfirmed" and e["sources_count"] == 1
    listed = {**report("https://t.me/OSINTdefender/1"), "platform": "telegram", "kind": "osint", "group": "osint-aggregators"}
    e = event("b", "Fairford", *HAVANA, "2026-10-04T12:00:00Z", "x", reports=[found, listed])
    merge.apply_status([e], [])
    assert e["status"] == "unconfirmed" and e["sources_count"] == 1   # the patrol's find counts only alone
