"""The front-line social media and satellite imagery agents (2026-10-05): who posted decides how
much a claim weighs; pictures are compared only when clear, and reach the reviewer as supporting
evidence, never as control."""
import io
import json
from datetime import datetime, timedelta, timezone

from frontline import assess, imagery, review, social

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
UA = {"id": "ukraine", "name": "Russia–Ukraine", "countries": ["UA", "RU"], "center": [48.4, 36.5], "radius_km": 700,
      "actors": [{"id": "RU", "name": "Russia", "sources": ["RU"]}, {"id": "UA", "name": "Ukraine", "sources": ["UA"]}]}
CONFLICTS = [UA]


def t(hours_ago):
    return (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def post(text, source="Some channel", side=None, kind="osint", platform="telegram", hours_ago=1, pid=None):
    return {"id": pid or f"p{abs(hash((text, source)))}", "source": source, "platform": platform, "kind": kind, "side": side,
            "group": source, "url": f"https://t.me/x/{abs(hash(text))}", "time": t(hours_ago), "text": text}


def said(**kw):
    return {"settlement": "Ulanove", "region": "Sumy Oblast", "country": "UA", "conflict": "ukraine", "date": None,
            "note": "the post says so", **kw}


# ----------------------------------------------------------------------------- social media agent

def test_only_recent_social_posts_about_control_are_queued_once():
    fl = {"places": {}}
    items = [post("Russian forces raised a flag in Ulanove"),
             post("Air raid alert in Kyiv"),                                     # nothing about control
             post("Captured Ulanove", platform="rss"),                          # not social media
             post("Штурмовики зайшли в Уланове", hours_ago=80)]                 # too old
    assert social.intake(items, fl, NOW) == 1
    assert social.intake(items, fl, NOW) == 0                                    # already queued
    assert [p["text"] for p in fl["social"]["queue"]] == ["Russian forces raised a flag in Ulanove"]


def test_a_sides_own_channel_is_its_claim_whatever_footage_it_shows():
    p = post("Видео: наши бойцы водрузили флаг в Уланове", source="Two Majors", side="RU", kind="partisan")
    c = social.clean(said(change="took", actor="RU", claimed_by="RU", basis="footage", geolocated_by=None), p, CONFLICTS)["claim"]
    assert (c["basis"], c["aligned"], c["claimed_by"], c["change"]) == ("party", "RU", "RU", "took")
    assert c["summary"].startswith("Two Majors: ") and "водрузили" not in c["summary"]   # the model's words, not the post's


def test_a_side_saying_the_other_took_a_place_admits_its_loss():
    p = post("Противник занял Уланове", source="DeepState", side="UA")
    found = social.clean(said(change="took", actor="RU", claimed_by=None, basis="party"), p, CONFLICTS)
    c = found["claim"]
    assert (c["change"], c["actor"], c["aligned"]) == ("lost", "UA", "UA")
    out = assess.assess({"claims": [c]}, UA, NOW)
    assert (out["holder"], out["status"], out["basis"]) == ("RU", "assessed", "the side that lost it says so")


def test_relaying_the_other_sides_claim_is_not_an_admission():
    p = post("Росіяни брешуть, що взяли Уланове", source="Operatyvnyi ZSU", side="UA", kind="partisan")
    c = social.clean(said(change="took", actor="RU", claimed_by="RU", basis="party"), p, CONFLICTS)["claim"]
    assert (c["change"], c["actor"], c["aligned"], c["basis"]) == ("took", "RU", "RU", "party")
    assert assess.assess({"claims": [c]}, UA, NOW)["status"] == "claimed"


def test_unaligned_footage_counts_only_when_the_post_says_it_was_located():
    plain = post("Video shows Russian troops in Ulanove", source="Aggregator")
    c = social.clean(said(change="contested", actor="RU", claimed_by=None, basis="footage"), plain, CONFLICTS)["claim"]
    assert c["basis"] == "unattributed"
    located = post("Geolocated: Russian troops in central Ulanove 51.2, 34.6", source="GeoConfirmed")
    c = social.clean(said(change="contested", actor="RU", claimed_by=None, basis="footage", geolocated_by="GeoConfirmed"),
                     located, CONFLICTS)["claim"]
    assert c["basis"] == "footage" and "geolocated by GeoConfirmed" in c["summary"]
    # an aggregator's own word is not an analyst's assessment
    c = social.clean(said(change="took", actor="RU", claimed_by=None, basis="analyst"), plain, CONFLICTS)["claim"]
    assert c["basis"] == "unattributed"


def test_run_reads_the_queue_once_under_its_own_budget():
    fl = {"places": {}}
    state = {"frontline": fl}
    social.intake([post("Russian forces captured Ulanove", source="Two Majors", side="RU", kind="partisan")], fl, NOW)
    asked = []

    def ask(system, text, state_, settings, now, max_tokens=0, purpose="", images=None):
        asked.append((purpose, json.loads(text)["posts"][0]))
        return {"posts": [{"i": 0, "claims": [said(change="took", actor="RU", claimed_by="RU", basis="party")]}]}
    found = social.run(CONFLICTS, state, {}, NOW, ask, budget=1)
    assert [a[0] for a in asked] == ["frontline_social"] and asked[0][1]["speaks_for"] == ["RU (ukraine)"]
    assert len(found) == 1 and found[0]["claim"]["via"] == "social" and not fl["social"]["queue"]
    assert social.run(CONFLICTS, state, {}, NOW, ask, budget=1) == [] and len(asked) == 1
    social.intake([post("Russian forces captured Ulanove", source="Two Majors", side="RU", kind="partisan")], fl, NOW)
    assert not fl["social"]["queue"]                                            # read before: not queued again


# ----------------------------------------------------------------------------- imagery agent

def png(color, cloud_share=0.0):
    from PIL import Image
    im = Image.new("RGB", (100, 100), color)
    for i in range(int(100 * cloud_share)):
        for x in range(100):
            im.putpixel((x, i), (255, 255, 255))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


class Resp:
    def __init__(self, status=200, body=None, content=b"", ctype="application/json"):
        self.status_code, self._body, self.content, self.headers = status, body, content, {"content-type": ctype}

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class Session:
    """Two recent passes (the clearest one clouded over the town) and one earlier pass."""
    def __init__(self):
        self.crops = []

    def post(self, url, json=None, timeout=0):
        start = json["datetime"].split("/")[0]
        if start >= "2026-09-20":
            feats = [{"id": "recent-cloudy", "properties": {"datetime": "2026-10-03T08:00:00Z", "eo:cloud_cover": 2}},
                     {"id": "recent-clear", "properties": {"datetime": "2026-10-01T08:00:00Z", "eo:cloud_cover": 9}}]
        else:
            feats = [{"id": "earlier", "properties": {"datetime": "2026-08-20T08:00:00Z", "eo:cloud_cover": 1}}]
        return Resp(body={"features": feats})

    def get(self, url, timeout=0):
        item = url.split("item=")[1].split("&")[0]
        self.crops.append(item)
        return Resp(content=png((70, 80, 60), 0.4 if item == "recent-cloudy" else 0), ctype="image/png")


def ledger_with(status="claimed"):
    claim = {"time": t(5), "actor": "RU", "change": "took", "claimed_by": "RU", "basis": "party", "aligned": "RU",
             "group": "g", "source": "g", "url": "u", "summary": "s", "event": None}
    return {"places": {"ua:ulanove": {"name": "Ulanove", "region": "Sumy Oblast", "country": "UA", "conflict": "ukraine",
                                      "lat": 51.2, "lon": 34.6, "claims": [claim],
                                      "published": {"status": status, "holder": "RU"}}}}


def test_cloudy_crops_are_passed_over_and_the_pair_is_compared_once():
    fl = ledger_with()
    sess = Session()
    asked = []

    def ask(system, text, state, settings, now, max_tokens=0, purpose="", images=None):
        asked.append((purpose, len(images or []), json.loads(text)))
        return {"usable": True, "why_not": None, "changes": [{"what": "new burn scars", "where": "east edge", "confidence": "medium"}],
                "summary": "Burn scars appeared along the eastern edge."}
    assert imagery.run(CONFLICTS, fl, {}, {}, sess, NOW, ask, budget=3, pending=[]) == 1
    assert sess.crops == ["recent-cloudy", "recent-clear", "earlier"]            # the clouded crop was skipped
    assert asked == [("frontline_imagery", 2, {"settlement": "Ulanove", "first_image": "2026-08-20", "second_image": "2026-10-01"})]
    seen = imagery.near(fl, "ua:ulanove", NOW)
    assert seen["changes"][0]["what"] == "new burn scars" and seen["before"] == "2026-08-20"
    # not looked at again for EVERY days
    assert imagery.run(CONFLICTS, fl, {}, {}, sess, NOW + timedelta(days=1), ask, budget=3, pending=[]) == 0 and len(asked) == 1
    assert imagery.near(fl, "ua:ulanove", NOW + imagery.FRESH + timedelta(days=1)) is None


def test_settled_places_are_not_looked_at_and_cloud_means_no_answer():
    assert imagery.candidates(ledger_with("assessed"), [], NOW) == []
    assert imagery.candidates(ledger_with("assessed"), ["ua:ulanove"], NOW) == ["ua:ulanove"]    # waiting for review
    assert imagery.cloudy(png((70, 80, 60), 0.3)) > imagery.CLOUDY > imagery.cloudy(png((70, 80, 60)))


def test_the_reviewer_sees_the_pictures_as_evidence_only():
    fl = ledger_with()
    fl["imagery"] = {"ua:ulanove": {"checked": t(1), "before": "2026-08-20", "after": "2026-10-01", "usable": True,
                                    "changes": [{"what": "destroyed blocks", "where": "centre", "confidence": "high"}],
                                    "summary": "Much of the centre is destroyed."}}
    sent = []

    def ask(system, text, state, settings, now, max_tokens=0, purpose=""):
        sent.append((system, json.loads(text)))
        return {"items": [{"n": 0, "verdict": "downgrade", "reason": "only Russia's claim"}]}
    proposed = {"holder": "RU", "status": "assessed", "since": t(5), "basis": "x", "sources": 1}
    review.run([("ua:ulanove", proposed)], CONFLICTS, fl, {}, {}, NOW, ask, budget=1)
    system, payload = sent[0]
    assert payload["items"][0]["satellite_imagery"]["summary"] == "Much of the centre is destroyed."
    assert "never who holds a place" in system
