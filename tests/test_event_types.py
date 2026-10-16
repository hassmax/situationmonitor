"""Every event type the model can pick is labelled, drawn and grouped everywhere it is shown."""
import re
from pathlib import Path

import alerts
import analyst
import extract
import merge
import share

APP = (Path(__file__).resolve().parents[1] / "site/app.js").read_text(encoding="utf-8")


def _js_keys(name):
    body = re.search(r"const " + name + r" = \{(.*?)\n  \};", APP, re.S).group(1)
    return set(re.findall(r"(\w+):", body))


def test_every_type_has_its_labels_icon_and_family():
    for kind in extract.EVENT_TYPES:
        assert kind in merge.FAMILY and merge.FAMILY[kind] in merge.RADIUS_KM, kind
        assert kind in analyst.TYPES and kind in analyst.FAMILY, kind
        assert kind in share.TYPES, kind
        assert kind in _js_keys("TYPES") and kind in _js_keys("CAT"), kind
    assert "weapons_test" in alerts.TYPES


def test_weapons_tests_have_their_own_kind_and_are_not_attacks():
    assert "weapons_test" in extract.EVENT_TYPES
    assert "a test launch or test firing where nothing is attacked is weapons_test" in extract.SYSTEM_PROMPT
    assert merge.FAMILY["weapons_test"] != merge.FAMILY["missile_drone"]
