"""IDF evidence is one side, regardless of which publisher repeats its statement."""
from copy import deepcopy
from datetime import datetime, timezone
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

import archive
import config
import extract
import geo
import merge
from sources import rss


def report(group='reuters', summary='IDF says it struck a weapons depot.', **kw):
    return {'source': group, 'group': group, 'side': None, 'kind': 'news', 'platform': 'rss',
            'weight': 3, 'claim': 'report', 'summary': summary, 'time': '2026-10-07T12:00:00Z',
            'url': f'https://example.com/{group}', **kw}


def event(*reports):
    return {'id': 'stable', 'type': 'airstrike', 'theater': 'middle_east', 'lat': 33.8, 'lon': 35.5,
            'time': '2026-10-07T12:00:00Z', 'updated': '2026-10-07T12:00:00Z',
            'severity': 2, 'reports': list(reports)}


class SourceEvidenceTests(unittest.TestCase):
    def status(self, *reports, nearby=None):
        e = event(*reports)
        with patch.object(merge, 'news_domains_near', return_value=set(nearby or [])):
            merge.apply_status([e], [])
        return e

    def test_newsroom_repetitions_are_one_claim(self):
        e = self.status(report(), report('bbc'), report('ap'))
        self.assertEqual((e['status'], e['sources_count']), ('claimed', 1))

    def test_direct_and_repeated_reports_are_one_claim(self):
        e = self.status(report('idf', url='https://www.idf.il/en/updates/1', summary='A depot was struck.'), report())
        self.assertEqual((e['status'], e['sources_count']), ('claimed', 1))

    def test_idf_cannot_corroborate_israeli_aligned_outlet(self):
        self.assertEqual(self.status(report(), report('jfeed', summary='A depot was struck.', side='IL'))['status'], 'claimed')

    def test_nearby_headlines_cannot_confirm_an_idf_claim(self):
        e = self.status(report(), report('bbc'), nearby=['a', 'b', 'c'])
        self.assertEqual(e['status'], 'claimed')
        self.assertEqual(e['news_nearby'], 3)

    def test_weak_news_copies_do_not_bypass_the_gdelt_guard(self):
        e = self.status(report(), report('google-news', summary='A depot was struck.'), nearby=['a', 'b', 'c'])
        self.assertEqual(e['status'], 'claimed')

    def test_independent_report_can_corroborate(self):
        e = self.status(report('bbc'), report(summary='Reuters verified footage of the destroyed depot.', claim_source=None))
        self.assertEqual((e['status'], e['sources_count']), ('corroborated', 2))
        self.assertEqual(e['summary'], 'Reuters verified footage of the destroyed depot.')

    def test_opposing_side_can_corroborate(self):
        self.assertEqual(self.status(report(), report('hezbollah', summary='A depot was struck.', side='LB'))['status'], 'corroborated')

    def test_attacker_mention_is_not_attribution(self):
        e = self.status(report(summary='Witnesses saw IDF aircraft strike a depot.'))
        self.assertEqual(e['status'], 'unconfirmed')

    def test_new_independent_coverage_is_not_reclassified_by_summary(self):
        e = self.status(report(summary='IDF says a depot was struck; witnesses confirmed the blast.', claim_source=None))
        self.assertEqual(e['status'], 'unconfirmed')

    def test_old_summary_with_independent_witnesses_is_not_reclassified(self):
        self.assertFalse(merge.idf_claim(report(summary='IDF says there was no strike; witnesses said a depot exploded.')))

    def test_legacy_attribution_aliases(self):
        for text in ['The Israeli military says it struck a depot.', 'According to the IDF, a depot was struck.',
                     'A depot was struck, IDF said.', 'Israel Defense Forces reported an interception.']:
            with self.subTest(text=text):
                self.assertTrue(merge.idf_claim(report(summary=text)))

    def test_unrelated_url_is_not_an_idf_source(self):
        for url in ['https://notidf.il/news', 'https://idf.il.example.com/news', 'https://example.com/idf.il/news', 'https://[invalid']:
            self.assertFalse(merge.idf_claim(report(summary='An attack was reported.', url=url)))

    def test_known_idf_source_names(self):
        for name in ['IDF', 'IDF (official)', 'Israel Defense Forces (via Google News)']:
            self.assertTrue(merge.idf_claim(report(source=name, summary='An interception was announced.', claim_source=None)))

    def test_published_report_keeps_publisher_and_shows_claim_side(self):
        r = report(source='Reuters')
        original = deepcopy(r)
        e = self.status(r)
        published = merge.public_event(e)['reports'][0]
        self.assertEqual((published['source'], published['url']), (r['source'], r['url']))
        self.assertEqual((published['side'], published['claim'], published['claim_source']), (None, 'official_claim', 'idf'))
        self.assertEqual(r, original)

    def test_archive_roundtrip_preserves_independence(self):
        e = self.status(report('bbc'), report(summary='Independent witnesses saw a strike.', claim_source=None))
        now = datetime(2026, 10, 7, 14, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as folder:
            archive.update(Path(folder), [merge.public_event(e)], {}, [], now, internal=[e])
            restored = archive.recent(Path(folder), now, 1)
        merge.apply_status(restored, [])
        self.assertEqual((restored[0]['status'], restored[0]['sources_count']), ('corroborated', 2))
        self.assertIsNone(restored[0]['reports'][1]['claim_source'])

    def test_extractor_accepts_only_known_claim_source(self):
        item = {'time': '2026-10-07T12:00:00Z'}
        obj = {'relevant': True, 'summary': 'An interception was reported.'}
        self.assertNotIn('claim_source', extract._clean_record(obj, item))
        for value, expected in [('idf', 'idf'), (None, None)]:
            with self.subTest(value=value):
                self.assertEqual(extract._clean_record({**obj, 'claim_source': value}, item)['claim_source'], expected)
        for invalid in ['reuters', {'side': 'IL'}]:
            self.assertNotIn('claim_source', extract._clean_record({**obj, 'claim_source': invalid}, item))

    def test_geocoding_carries_evidence_origin(self):
        class Geocoder:
            def locate(self, *args):
                return (33.8, 35.5)
        item = {'source': 'Reuters', 'platform': 'rss', 'kind': 'news', 'group': 'reuters',
                'url': 'https://example.com/1', 'time': '2026-10-07T12:00:00Z'}
        rec = extract._clean_record({'relevant': True, 'summary': 'IDF says a depot was struck.', 'claim_source': 'idf',
                                    'country': 'LB', 'place': 'Beirut', 'lat': 33.8, 'lon': 35.5,
                                    'theater': 'middle_east'}, item)
        cfg = config.load()
        out = geo.place_record(rec, Geocoder(), cfg.theaters)
        self.assertEqual(out['report']['claim_source'], 'idf')

    def test_direct_google_news_idf_source_is_official(self):
        cfg = config.load()
        src = rss._outlet_src({'name': 'Google News', 'group': 'google-news'},
                             {'source': {'href': 'https://www.idf.il', 'title': 'IDF'}}, cfg.outlets)
        self.assertEqual((src['kind'], src['side'], src['group']), ('official', 'IL', 'idf'))

    def test_non_idf_confidence_rules_still_apply(self):
        e = self.status(report(summary='Witnesses saw an attack.'), report('ap', summary='An attack was verified.'))
        self.assertEqual(e['status'], 'corroborated')
        e = self.status(report('ru-military', summary='Russia says a depot was struck.', side='RU'),
                        report('rybar', summary='A depot was struck.', side='RU'))
        self.assertEqual(e['status'], 'claimed')


if __name__ == '__main__':
    unittest.main()
