"""International commitments and relations: leaving treaties and bodies, expulsions, sanctions."""
import config
import extract
import geo

GRECO = "US withdraws from European anti-corruption panel in latest shift away from global groups"


def test_the_keyword_filter_passes_treaty_and_membership_news():
    for text in (GRECO, "EU adopts new sanctions package against Russia", "Mali expels French ambassador",
                 "Hungary quits the International Criminal Court", "Burkina Faso suspends membership of ECOWAS"):
        assert extract.is_candidate({"text": text}), text
    assert not extract.is_candidate({"text": "Recipe of the week: roasted pumpkin soup"})


def test_posts_rejected_by_an_older_filter_are_found_for_a_second_look():
    assert extract.rejected_before_added_words({"text": GRECO}, 2)             # passes only with the new words
    assert not extract.rejected_before_added_words({"text": GRECO}, 3)         # already current
    assert not extract.rejected_before_added_words({"text": "Drone strike on Kharkiv"}, 2)  # passed before too
    assert extract.rejected_before_added_words({"text": "Leaders met in Doha"}, 1)
    assert not extract.rejected_before_added_words({"text": "Leaders met in Doha"}, 2)


def test_bodies_with_longer_short_names_are_kept_as_parties():
    rec = extract._clean_record({"relevant": True, "type": "diplomacy", "summary": "The US left GRECO.",
                                 "parties": ["us", "GRECO", "UNESCO", "Council of Europe"], "theater": "global"},
                                {"time": "2026-09-29T15:00:00Z"})
    assert rec["parties"] == ["GRECO", "UNESCO", "US"] and rec["theater"] == "global"


def test_a_step_in_the_global_theater_is_placed_where_the_model_says():
    theaters = config.load().theaters
    assert "global" in {t["id"] for t in theaters}
    assert geo.theater_for_iso(38.9, -77.0, "US", theaters) is None   # no country maps to it by itself

    class NoLookup:
        def get(self, *a, **k):
            raise OSError("offline")
    rec = {"item": {"time": "2026-09-29T15:00:00Z", "source": "s", "platform": "rss", "kind": "news", "group": "g",
                    "url": "u"}, "place": "Washington", "admin1": None, "country": "US", "lat": 38.9, "lon": -77.04,
           "type": "diplomacy", "theater": "global", "severity": 2, "summary": "The US left GRECO.", "claim": "report",
           "killed": None, "injured": None, "parties": ["US", "GRECO"]}
    c = geo.place_record(rec, geo.Geocoder({}, NoLookup(), budget=0), theaters)
    assert c and c["theater"] == "global" and (c["lat"], c["lon"]) == (38.9, -77.04)
