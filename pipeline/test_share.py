"""Share pages have usable preview metadata without JavaScript or URL fragments."""
from copy import deepcopy
from html.parser import HTMLParser
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from common import save_json
import publish
import share

TITLE = 'Reports indicate U.S. President Trump is weighing a potential military strike against Iran before the midterms.'
EVENT = {'id': 'a99b66331f72', 'summary': TITLE, 'type': 'deployment', 'place': 'Washington', 'country': 'US',
         'lat': 38.9, 'lon': -77.0, 'status': 'corroborated', 'sources_count': 2, 'severity': 2,
         'time': '2026-10-07T23:49:37Z', 'updated': '2026-10-08T02:32:00Z',
         'reports': [{'source': 'Sample newsroom', 'kind': 'news', 'platform': 'rss', 'side': None,
                      'claim': 'report', 'url': 'https://example.com/report', 'time': '2026-10-08T02:32:00Z',
                      'summary': TITLE}]}


class Metadata(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.values = {}
        self.feed(text)
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'meta':
            self.values[a.get('property') or a.get('name')] = a.get('content')


class ShareTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data, self.archive, self.out, self.cache = [self.root / n for n in ('data', 'archive', 'site', 'cache')]
        self.out.mkdir()
        template = (Path(__file__).parent.parent / 'site/index.html').read_text()
        (self.out / 'index.html').write_text(template)
        self.write([deepcopy(EVENT)])
    def write(self, events):
        publish.write(self.data, {'generated_at': '2026-10-08T03:00:00Z', 'events': events})
    def build(self, **kw):
        return share.build(self.data, self.archive, self.out, self.cache, 'https://hassmax.github.io/situationmonitor/', **kw)
    def folder(self):
        return self.out / 'events' / EVENT['id']

    def test_requested_headline_is_exact_in_preview_and_page_title(self):
        self.assertEqual(self.build(), 1)
        text = (self.folder() / 'index.html').read_text()
        meta = Metadata(text).values
        self.assertEqual(meta['og:title'], TITLE)
        self.assertEqual(meta['twitter:title'], TITLE)
        self.assertIn('<title>' + TITLE + ' | Global Situation Monitor</title>', text)
        self.assertEqual(meta['og:url'], 'https://hassmax.github.io/situationmonitor/events/a99b66331f72/')
        self.assertIn('Washington', meta['og:description'])
        self.assertIn('Corroborated', meta['og:description'])

    def test_image_exists_with_declared_size_and_type(self):
        self.build()
        meta = Metadata((self.folder() / 'index.html').read_text()).values
        with Image.open(self.folder() / meta['og:image'].rsplit('/', 1)[1]) as image:
            self.assertEqual(image.size, (1200, 630))
            self.assertEqual(image.format, 'PNG')
        self.assertEqual((meta['og:image:width'], meta['og:image:height'], meta['og:image:type']), ('1200', '630', 'image/png'))

    def test_shared_page_uses_the_full_app_and_correct_asset_root(self):
        self.build()
        text = (self.folder() / 'index.html').read_text()
        self.assertIn('<base href="../../">', text)
        self.assertIn('src="app.js?v=__BUILD__"', text)
        self.assertIn('id="globe"', text)
        self.assertNotIn('http-equiv="refresh"', text)

    def test_snapshot_preserves_reports_and_event_date(self):
        self.build()
        e = json.loads((self.folder() / 'event.json').read_text())
        self.assertEqual(e['time'], EVENT['time'])
        self.assertEqual(e['reports'], EVENT['reports'])

    def test_archive_survives_departure_from_live_feed(self):
        save_json(self.archive / '2026-10-07.json', {'events': [EVENT]})
        self.write([])
        self.assertEqual(self.build(), 1)
        self.assertTrue((self.folder() / 'event.json').exists())

    def test_current_corrected_publication_wins(self):
        save_json(self.archive / '2026-10-07.json', {'events': [{**EVENT, 'summary': 'An earlier summary.'}]})
        self.build()
        self.assertEqual(Metadata((self.folder() / 'index.html').read_text()).values['og:title'], TITLE)

    def test_corrections_apply_to_archived_previews(self):
        self.write([])
        save_json(self.archive / '2026-10-07.json', {'events': [EVENT]})
        self.build(entries=[{'id': EVENT['id'], 'note': 'Clarified wording.', 'edit': {'summary': 'A possible strike is being considered.'}}])
        self.assertEqual(Metadata((self.folder() / 'index.html').read_text()).values['og:title'], 'A possible strike is being considered.')

    def test_hidden_and_removed_pages_and_images_are_deleted(self):
        self.build()
        self.assertEqual(self.build(entries=[{'id': EVENT['id'], 'note': 'Removed.', 'hide': True}]), 0)
        self.assertFalse(self.folder().exists())
        self.assertFalse(list(self.cache.glob('*.png')))
        self.assertEqual(self.build(removed={EVENT['id']}), 0)

    def test_internal_archive_metadata_is_not_exposed(self):
        e = deepcopy(EVENT)
        e['headline'] = 'Internal dedupe field'
        e['reports'][0].update(group='internal', weight=3, title='Original article text')
        self.write([])
        save_json(self.archive / '2026-10-07.json', {'events': [e]})
        self.build()
        text = (self.folder() / 'index.html').read_text()
        self.assertNotIn('Internal dedupe field', text)
        self.assertNotIn('Original article text', text)
        snapshot = json.loads((self.folder() / 'event.json').read_text())
        self.assertNotIn('group', snapshot['reports'][0])

    def test_scripts_in_summaries_are_inert(self):
        self.write([{**EVENT, 'summary': '</script><script>alert("x")</script> & "test"'}])
        self.build()
        text = (self.folder() / 'index.html').read_text()
        self.assertNotIn('</script><script>alert', text)
        self.assertIn('\\u003c/script>', text)
        self.assertEqual(Metadata(text).values['og:title'], '</script><script>alert("x")</script> & "test"')

    def test_invalid_ids_cannot_escape_output_folder(self):
        self.write([{**EVENT, 'id': '../escape'}])
        self.assertEqual(self.build(), 0)
        self.assertFalse((self.out / 'escape').exists())

    def test_unchanged_cards_are_reused(self):
        self.build()
        with patch.object(share, 'card', side_effect=AssertionError('unchanged image regenerated')):
            self.build()

    def test_changed_headline_gets_new_image_url(self):
        self.build()
        old = Metadata((self.folder() / 'index.html').read_text()).values['og:image']
        self.write([{**EVENT, 'summary': 'Reports describe preparations, not an attack.'}])
        self.build()
        self.assertNotEqual(Metadata((self.folder() / 'index.html').read_text()).values['og:image'], old)
        self.assertEqual(len(list(self.cache.glob('*.png'))), 1)

    def test_uncertainty_is_kept(self):
        self.write([{**EVENT, 'status': 'claimed', 'possibly_old': True}])
        self.build()
        self.assertIn('Possibly an old story', Metadata((self.folder() / 'index.html').read_text()).values['og:description'])

    def test_custom_dashboard_base_and_rejection_of_fragments(self):
        share.build(self.data, self.archive, self.out, self.cache, 'https://example.com/monitor/')
        self.assertEqual(Metadata((self.folder() / 'index.html').read_text()).values['og:url'], 'https://example.com/monitor/events/a99b66331f72/')
        for base in ['javascript:alert(1)', 'https://example.com/#event', 'https://example.com/?event=1']:
            with self.assertRaises(ValueError):
                share.build(self.data, self.archive, self.out, self.cache, base)


if __name__ == '__main__':
    unittest.main()
