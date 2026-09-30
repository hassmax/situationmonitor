from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

import alerts

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)
RULES = yaml.safe_load((Path(__file__).resolve().parents[1] / "pipeline/config/alerts.yaml").read_text())
ENV = {"TELEGRAM_BOT_TOKEN": "123:SECRET", "TELEGRAM_CHAT_ID": "42", "GITHUB_REPOSITORY": "Owner/situationmonitor"}
NAMES = {"ukraine": "Russia-Ukraine", "mideast": "Middle East"}


def ev(i, **kw):
    base = {"id": i, "theater": "ukraine", "type": "missile_drone", "place": "Kyiv", "summary": f"event {i}",
            "status": "corroborated", "severity": 3, "sources_count": 3, "time": "2026-09-27T10:00:00Z"}
    return {**base, **kw}


class Outbox:
    def __init__(self, ok=True):
        self.msgs, self.ok = [], ok

    def __call__(self, token, chat, text):
        self.msgs.append(text)
        return self.ok


def started():
    return {"alerts": {"started": "2026-09-01T00:00:00Z", "sent": {}, "bridges": []}}


def run(state, events, fleet=(), rules=RULES, env=ENV, now=NOW):
    box = Outbox()
    alerts.run(state, list(events), list(fleet), rules, NAMES, now, env, send=box)
    return box.msgs


def test_rules():
    assert alerts.event_matches(ev("a"), RULES) == ["major"]
    assert alerts.event_matches(ev("b", status="unconfirmed"), RULES) == []
    assert alerts.event_matches(ev("c", severity=2), RULES) == []
    assert "wave" in alerts.event_matches(ev("w", wave=True, launched=120, status="claimed"), RULES)
    assert "wave" in alerts.event_matches(ev("w2", wave=True, targets=[{}] * 8, severity=2), RULES)
    assert alerts.event_matches(ev("w3", wave=True, launched=40, targets=[{}] * 3, severity=2), RULES) == []
    assert alerts.event_matches(ev("l", type="legal", severity=1, status="unconfirmed"), RULES) == ["legal"]


def test_first_run_sends_only_alerts_are_on_and_records_everything():
    state = {}
    msgs = run(state, [ev("a"), ev("b", type="legal")])
    assert len(msgs) == 1 and msgs[0].startswith("Alerts are on")
    assert {"a:major", "b:major", "b:legal"} <= set(state["alerts"]["sent"])
    assert run(state, [ev("a"), ev("b", type="legal")]) == []          # no backlog afterwards


def test_first_run_retries_if_the_message_fails():
    state = {}
    alerts.run(state, [ev("a")], [], RULES, NAMES, NOW, ENV, send=Outbox(ok=False))
    assert not state["alerts"].get("started") and state["alerts"]["sent"] == {}


def test_never_twice_but_escalation_alerts_again():
    state = started()
    wave = ev("w", wave=True, launched=40, targets=[{}] * 8, status="unconfirmed", severity=2)
    assert len(run(state, [wave])) == 1                      # 8 locations: wave rule
    assert run(state, [wave]) == []                          # same thing: no repeat
    wave.update(launched=150)
    assert run(state, [wave]) == []                          # still the wave rule: no repeat
    wave.update(status="corroborated", severity=3)
    msgs = run(state, [wave])                                # escalates to a new rule
    assert len(msgs) == 1 and "major corroborated event" in msgs[0]


def test_message_format_and_link():
    msgs = run(started(), [ev("abc123", summary="Russian drones hit a substation.")])
    text = msgs[0]
    assert "Drone or missile attack: Kyiv, Russia-Ukraine" in text
    assert "Corroborated, 3 sources" in text
    assert text.endswith("https://owner.github.io/situationmonitor/#abc123")
    env = {**ENV, "DASHBOARD_URL": "https://example.org/map"}
    assert run(started(), [ev("x")], env=env)[0].endswith("https://example.org/map/#x")


def test_more_than_eight_become_one_digest():
    state = started()
    msgs = run(state, [ev(f"e{i}") for i in range(12)])
    assert len(msgs) == 1 and msgs[0].startswith("12 new alerts:")
    assert all(f"e{i}:major" in state["alerts"]["sent"] for i in range(12))


def test_theater_filter_and_quiet_hours():
    rules = {**RULES, "theaters": ["mideast"]}
    assert run(started(), [ev("u")], rules=rules) == []
    assert len(run(started(), [ev("m", theater="mideast")], rules=rules)) == 1
    quiet = {**RULES, "quiet_hours": {"start": 22, "end": 7}}
    state = started()
    assert run(state, [ev("q")], rules=quiet, now=NOW.replace(hour=23)) == []
    assert "q:major" not in state["alerts"]["sent"]         # held, not dropped
    assert len(run(state, [ev("q")], rules=quiet, now=NOW.replace(hour=8))) == 1


def test_carrier_departs_heading_and_big_move():
    c = {"hull": "CVN-78", "name": "USS Gerald R. Ford", "status": "departed", "departed_at": "2026-09-27T08:00:00Z",
         "place": "Norfolk", "lat": 36.9, "lon": -76.3, "as_of": "2026-09-27T08:00:00Z", "source": "USNI News"}
    state = started()
    msgs = run(state, [], [c])
    assert len(msgs) == 1 and "departed" in msgs[0] and msgs[0].endswith("#CVN-78")
    assert run(state, [], [c]) == []
    c2 = {**c, "status": "underway", "heading_to": {"place": "Mediterranean Sea", "lat": 35, "lon": 18}}
    assert "heading to Mediterranean Sea" in run(state, [], [c2])[0]
    small = {**c2, "prev": {"lat": 36.9, "lon": -76.3}, "moved_at": "2026-09-28T08:00:00Z", "lat": 37.5, "lon": -75.0}
    assert run(state, [], [small]) == []                     # under 500 km
    big = {**small, "moved_at": "2026-09-29T08:00:00Z", "lat": 36.0, "lon": -60.0}
    assert "moved about" in run(state, [], [big])[0]


def test_new_bridge_alerts_once_and_again_after_it_lapses():
    def delivery(i, hours_ago, flights=1):
        t = (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return ev(i, type="arms_transfer", severity=2, status="unconfirmed", time=t,
                  transfer={"kind": "delivery", "supplier": "US", "recipient": "IL", "mode": "air", "flights": flights})
    state = started()
    two = [delivery("d1", 5), delivery("d2", 10)]
    assert run(state, two) == []                             # 2 deliveries: not yet a bridge
    three = two + [delivery("d3", 20)]
    msgs = run(state, three)
    assert len(msgs) == 1 and "New arms bridge: US → IL" in msgs[0] and "3 deliveries" in msgs[0]
    assert run(state, three) == []                           # still active: no repeat
    assert run(state, []) == []                              # lapses
    assert len(run(state, three)) == 1                       # restarts: new bridge
    assert len(run(started(), [delivery("big", 2, flights=4)])) == 1   # one report of 4 flights counts


def test_missing_secrets_skip_without_marking(capsys):
    state = {}
    msgs = run(state, [ev("a")], env={"GITHUB_REPOSITORY": "o/r"})
    assert msgs == [] and "alerts" not in state
    assert "not set up" in capsys.readouterr().out


def test_token_never_logged(monkeypatch, capsys):
    import requests

    def boom(url, **kw):
        raise requests.ConnectionError(f"failed to reach {url}")
    monkeypatch.setattr(requests, "post", boom)
    assert alerts.send_telegram("123:SECRET", "42", "hi") is False
    assert "SECRET" not in capsys.readouterr().out


def test_shin_plus_another_osint_telegram_channel():
    def rep(url, platform="telegram", kind="osint"):
        return {"url": url, "platform": platform, "kind": kind}
    base = dict(status="unconfirmed", severity=1)
    shin = rep("https://t.me/shin_persian/101")
    assert "tg_pair" in alerts.event_matches(ev("p", reports=[shin, rep("https://t.me/DeepStateUA/55")], **base), RULES)
    assert alerts.event_matches(ev("q", reports=[shin], **base), RULES) == []                     # Shin alone
    assert alerts.event_matches(ev("r", reports=[shin, rep("https://t.me/shin_persian/102")], **base), RULES) == []
    assert alerts.event_matches(ev("s", reports=[shin, rep("https://t.me/kpszsu/9", kind="official")], **base), RULES) == []
    assert alerts.event_matches(ev("t", reports=[shin, rep("https://x.com/a", platform="bluesky")], **base), RULES) == []
    msg = run(started(), [ev("p", reports=[shin, rep("https://t.me/DeepStateUA/55")], **base)])[0]
    assert "reported by two OSINT Telegram channels" in msg


def test_possibly_old_stories_send_no_alert():
    import alerts
    e = {"id": "o", "severity": 3, "status": "corroborated", "theater": "mideast", "possibly_old": True}
    assert alerts.event_matches(e, {"rules": {"major_corroborated": {"enabled": True}}}) == []
