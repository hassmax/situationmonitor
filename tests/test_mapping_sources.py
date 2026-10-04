"""2026-10-04: mapping like ISW and Black Bird Group: the Critical Threats Africa File, capture
headlines and French in the keyword filter, and satellite heat for the reviewer."""
import json
from datetime import datetime, timezone

import extract
from frontline import heat, isw

NOW = datetime(2026, 10, 4, 15, 0, tzinfo=timezone.utc)


class Resp:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class Session:
    def __init__(self, pages):
        self.pages, self.asked = pages, []

    def get(self, url, **kw):
        self.asked.append(url)
        for k, v in self.pages.items():
            if k in url:
                return Resp(v)
        return Resp("missing", 404)


def test_africa_file_list_from_page_data():
    items = [{"slug": "ethiopia-tigray-tplf-m23-drc-rwanda-uganda", "title": "…: Africa File, October 1, 2026", "published_timestamp": 1790863200},
             {"slug": "old-one-africa-file-may-7-2026", "title": "…", "published_timestamp": 1778178840 - 86400 * 400},
             {"slug": "2025-year-in-review-africa-file", "title": "…", "published_timestamp": 1790863200}]
    page = f'<script>var INI_LIST = {json.dumps(items)}</script>'
    out = isw.africa_file(Session({"criticalthreats.org/analysis/africa-file": page}), NOW)
    assert [r["url"] for r in out] == ["https://www.criticalthreats.org/analysis/ethiopia-tigray-tplf-m23-drc-rwanda-uganda"]
    assert out[0]["publisher"] == "Critical Threats" and out[0]["conflicts"] == isw.AFRICA and out[0]["series"] == "Africa File"


def test_africa_file_links_when_the_page_has_no_data():
    page = ('<a href="https://www.criticalthreats.org/analysis/sudan-rsf-saf-africa-file-september-24-2026">x</a>'
            '<a href="https://www.criticalthreats.org/analysis/sudan-rsf-saf-africa-file-september-24-2026#Sudan">y</a>'
            '<a href="https://www.criticalthreats.org/analysis/old-africa-file-march-12-2026">z</a>')
    out = isw.africa_file(Session({"africa-file": page}), NOW)
    assert [r["url"].rsplit("/", 1)[-1] for r in out] == ["sudan-rsf-saf-africa-file-september-24-2026"]


def test_capture_headlines_and_french_pass_the_filter():
    for t in ["Sudan Army Captures Mazroub in North Kordofan", "Resistance forces take control of the town",
              "Junta troops retook the base", "Les FARDC reprennent Walikale", "Attaque jihadiste à Djibo: des dizaines de morts"]:
        assert extract.CONFLICT_RE.search(t), t
    for t in ["Price control measures announced", "Le président a reçu le ministre"]:
        assert not extract.CONFLICT_RE.search(t), t
    assert extract.PREFILTER_VERSION >= 6
    # posts only the new words let through get one more look
    assert extract.rejected_before_added_words({"text": "Sudan Army Captures Mazroub"}, 5)


CSV = ("latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_ti5,frp,daynight\n"
       "48.001,37.001,330,0.4,0.4,2026-10-03,1012,N,VIIRS,n,2.0NRT,290,5,D\n"
       "48.002,37.002,330,0.4,0.4,2026-10-03,1012,N,VIIRS,h,2.0NRT,290,5,D\n"   # same ~1 km cell: counted once
       "48.050,37.050,330,0.4,0.4,2026-10-03,1012,N,VIIRS,n,2.0NRT,290,5,D\n"
       "48.003,37.003,330,0.4,0.4,2026-09-25,1012,N,VIIRS,n,2.0NRT,290,5,D\n"
       "48.004,37.004,330,0.4,0.4,2026-10-03,1012,N,VIIRS,l,2.0NRT,290,5,D\n"   # low confidence: left out
       "49.500,38.500,330,0.4,0.4,2026-10-03,1012,N,VIIRS,n,2.0NRT,290,5,D\n")  # far away


def _fl():
    return {"places": {"ua:x": {"name": "X", "conflict": "ukraine", "lat": 48.0, "lon": 37.0, "claims": []},
                       "ua:y": {"name": "Y", "conflict": "ukraine", "lat": None, "lon": None, "claims": []}}}


CONFLICTS = [{"id": "ukraine", "center": [48.5, 34], "radius_km": 700, "area_km": 10}]


def test_heat_does_nothing_without_a_key():
    fl, s = _fl(), Session({"firms": CSV})
    assert heat.update(CONFLICTS, fl, s, NOW, env={}) == 0 and not s.asked
    assert heat.near(fl, "ua:x", NOW) is None


def test_heat_counts_cells_near_settlements_by_day():
    fl, s = _fl(), Session({"firms": CSV})
    assert heat.update(CONFLICTS, fl, s, NOW, env={"FIRMS_MAP_KEY": "k"}) > 0
    assert len(s.asked) == len(heat.SOURCES)
    assert fl["heat"]["places"]["ua:x"] == {"2026-10-03": 2, "2026-09-25": 1}
    assert heat.near(fl, "ua:x", NOW) == {"last_7_days": 2, "previous_7_days": 1}
    # checked again only after FETCH_EVERY
    assert heat.update(CONFLICTS, fl, s, NOW, env={"FIRMS_MAP_KEY": "k"}) == 0


def test_heat_error_message_is_not_data_and_key_is_not_logged(capsys):
    fl, s = _fl(), Session({"firms": "Invalid MAP_KEY k123"})
    heat.update(CONFLICTS, fl, s, NOW, env={"FIRMS_MAP_KEY": "k123"})
    assert "k123" not in capsys.readouterr().out
    assert not fl["heat"]["places"]
