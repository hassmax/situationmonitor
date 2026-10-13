import json
from datetime import datetime, timedelta, timezone

import alerts
import embed
import extract
import gate
import merge
import recency
from test_dedupe import NOW, background, ev

SETTINGS = {"daily_llm_calls": 470, "max_calls_per_run": 6}


class Model:
    """Answers each case: the candidates named in `same` (by new event id), `old` for event ids."""

    def __init__(self, same=None, old=None, answer=True):
        self.same, self.old, self.answer, self.calls = same or {}, old or {}, answer, []

    def __call__(self, system, user, *a, **k):
        cases = json.loads(user)["cases"]
        self.calls.append({"cases": cases, "purpose": k.get("purpose")})
        if not self.answer:
            return None
        out = []
        for c in cases:
            i = c["event"]["id"]
            same = [f["id"] for f in c["candidates"] if f["id"] in self.same.get(i, ())]
            out.append({"i": c["i"], "same": same, "type": None, "summary": None,
                        "old": i in self.old, "match": self.old.get(i)})
        return {"results": out}


def no_meaning(monkeypatch):
    monkeypatch.setattr(embed, "vectors", lambda texts: None)


def no_search(monkeypatch, found=None):
    monkeypatch.setattr(recency, "_evidence", lambda session, e: found)


def news(e):
    for r in e["reports"]:
        r["platform"] = "rss"
    return e


def test_duplicate_of_an_event_on_the_map_is_folded_before_publishing(monkeypatch):
    no_meaning(monkeypatch)
    no_search(monkeypatch)
    old = ev("a", "An Israeli strike killed one Palestinian in Gaza City.", "airstrike", hours_ago=5)
    new = ev("n", "Israeli missile attack in Gaza City killed a Palestinian man.", "missile_drone", hours_ago=1)
    events = background() + [old, new]
    model = Model(same={"n": ["a"]})
    out, folded = gate.check(events, {"n"}, None, model, {}, SETTINGS, NOW, set(), [])
    assert [f["id"] for f in folded] == ["n"]
    assert "n" not in {e["id"] for e in out}
    kept = next(e for e in out if e["id"] == "a")
    assert {r["url"] for r in kept["reports"]} == {"u-a", "u-n"}
    assert model.calls[0]["purpose"] == "gate"


def test_new_event_with_nothing_like_it_passes_without_a_model_call(monkeypatch):
    no_meaning(monkeypatch)
    no_search(monkeypatch)
    new = ev("n", "Venezuelan navy seized a fishing boat near Aruba.", "naval", "VE", "Aruba", 1, 12.5, -70.0)
    model = Model()
    out, folded = gate.check(background() + [new], {"n"}, None, model, {}, SETTINGS, NOW, set(), [])
    assert not model.calls and not folded
    assert next(e for e in out if e["id"] == "n")["gate"] == "ok"


def test_old_story_found_by_search_is_dropped(monkeypatch):
    no_meaning(monkeypatch)
    older = [{"title": "US submarine sinks Iranian ship - Reuters", "when": NOW - timedelta(days=60), "link": "x"}]
    no_search(monkeypatch, ([], older))
    new = news(ev("n", "A US submarine sank an Iranian ship.", "naval", "IR", "Gulf of Oman", 1, 24.0, 58.0))
    state = {}
    out, folded = gate.check(background() + [new], {"n"}, None, Model(old={"n": 0}), state, SETTINGS, NOW, set(), [])
    assert "n" not in {e["id"] for e in out}
    assert state["dropped_as_old"][0]["match"].startswith("US submarine")


def test_unanswered_event_is_published_unchecked_and_not_alerted(monkeypatch):
    no_meaning(monkeypatch)
    no_search(monkeypatch)
    old = ev("a", "An Israeli strike killed one Palestinian in Gaza City.", "airstrike", hours_ago=5)
    new = ev("n", "Israeli strike killed one Palestinian in Gaza City, medics said.", "airstrike", hours_ago=1, severity=3)
    out, _ = gate.check(background() + [old, new], {"n"}, None, Model(answer=False), {}, SETTINGS, NOW, set(), [])
    n = next(e for e in out if e["id"] == "n")
    assert gate.unchecked(n)
    pub = {**merge.public_event(n), "unchecked": True, "status": "corroborated"}
    assert "gate" not in pub
    assert alerts.event_matches(pub, {"rules": {"major_corroborated": {}}}) == []
    # next run: answered, checked, and no longer unchecked
    later = NOW + timedelta(minutes=15)
    out, _ = gate.check(out, set(), None, Model(), {}, SETTINGS, later, set(), [])
    assert next(e for e in out if e["id"] == "n")["gate"] == "ok"


def test_unchecked_event_is_given_up_after_six_hours(monkeypatch):
    no_meaning(monkeypatch)
    no_search(monkeypatch)
    old = ev("a", "An Israeli strike killed one Palestinian in Gaza City.", "airstrike", hours_ago=5)
    new = ev("n", "Israeli strike killed one Palestinian in Gaza City, medics said.", "airstrike", hours_ago=1)
    out, _ = gate.check([old, new], {"n"}, None, Model(answer=False), {}, SETTINGS, NOW, set(), [])
    out, _ = gate.check(out, set(), None, Model(), {}, SETTINGS, NOW + timedelta(hours=7), set(), [])
    n = next(e for e in out if e["id"] == "n")
    assert n["gate"] == "gave_up" and gate.unchecked(n)


def test_answers_are_kept_where_the_same_story_check_reads_them(monkeypatch):
    no_meaning(monkeypatch)
    no_search(monkeypatch)
    old = ev("a", "An Israeli strike killed one Palestinian in Gaza City.", "airstrike", hours_ago=5)
    new = ev("n", "Israeli strike killed one Palestinian in Gaza City, medics said.", "airstrike", hours_ago=1)
    state = {}
    gate.check(background() + [old, new], {"n"}, None, Model(), state, SETTINGS, NOW, set(), [])
    assert state["dedupe"]["judged"]["a|n"]["same"] is False


def test_meaning_match_finds_a_duplicate_the_words_miss(monkeypatch):
    no_search(monkeypatch)
    old = ev("a", "Kyiv says Russian glide bombs hit a school in Kostiantynivka.", "airstrike", "UA", "Kostiantynivka",
             5, 48.5, 37.7)
    new = ev("n", "Aerial munitions struck an educational building, Ukrainian officials report.", "missile_drone", "UA",
             "Donetsk Oblast", 1, 48.5, 37.7)
    texts = {t: None for t in gate._texts(old) + gate._texts(new)}
    monkeypatch.setattr(embed, "vectors", lambda ts: {t: t for t in ts})
    monkeypatch.setattr(embed, "similarity", lambda a, b, v: 0.9 if (a[0][:4], b[0][:4]) in {("Aeri", "Kyiv"), ("Kyiv", "Aeri")} else 0.1)
    assert texts
    model = Model(same={"n": ["a"]})
    out, folded = gate.check(background() + [old, new], {"n"}, None, model, {}, SETTINGS, NOW, set(), [])
    assert [f["id"] for f in folded] == ["n"]


def test_gate_asks_gemini_first_then_the_providers(monkeypatch):
    import providers

    order = []
    monkeypatch.setattr(extract, "_ask_gemini", lambda *a, **k: order.append("gemini"))
    monkeypatch.setattr(providers, "ask_routed", lambda *a, **k: order.append("cerebras") or {"results": []})
    assert extract.ask_json("s", "u", {}, SETTINGS, NOW, purpose="gate") == {"results": []}
    assert order == ["gemini", "cerebras"]
    order.clear()
    extract.ask_json("s", "u", {}, SETTINGS, NOW, purpose="recency")
    assert order[0] == "cerebras"


def test_gate_share_and_routes_add_up():
    import providers

    assert extract.SHARES["gate"] == "gate_daily_max"
    assert sum(v["cerebras"] for v in providers.ROUTES.values()) == 900_000
    assert gate.unchecked({"gate": "pending"}) and not gate.unchecked({}) and not gate.unchecked({"gate": "ok"})


def test_prompt_carries_both_checks_rules():
    assert "same specific incident" in gate.PROMPT and "Similar wording is not enough" in gate.PROMPT
    assert "Reply with one JSON object" in gate.PROMPT and "{" in gate.PROMPT


def test_settings_file_routes_match():
    import yaml
    from pathlib import Path

    cfg = yaml.safe_load((Path(__file__).parent.parent / "pipeline/config/sources.yaml").read_text())
    routes = cfg["settings"]["provider_routes"]
    assert routes["gate"] == {"cerebras": 100000}
    assert sum(v["cerebras"] for v in routes.values()) == 900000


_ = datetime, timezone


def test_meaning_readings_are_kept_between_runs(tmp_path, monkeypatch):
    import numpy as np

    read = []

    class Fake:
        def embed(self, texts, batch_size=64):
            read.extend(texts)
            return [np.ones(4) * (i + 1) for i, _ in enumerate(texts)]

    monkeypatch.setattr(embed, "_model", Fake())
    monkeypatch.setattr(embed, "_cache", {})
    monkeypatch.setattr(embed, "_used", set())
    assert set(embed.vectors(["a", "b"])) == {"a", "b"}
    embed.save(tmp_path / "e.npz")
    monkeypatch.setattr(embed, "_cache", {})
    embed.load(tmp_path / "e.npz")
    assert set(embed.vectors(["a", "b", "c"])) == {"a", "b", "c"}
    assert read == ["a", "b", "c"]  # "a" and "b" were not read again
    assert abs(embed.similarity(["a"], ["b"], embed.vectors(["a", "b"])) - 1.0) < 1e-3
