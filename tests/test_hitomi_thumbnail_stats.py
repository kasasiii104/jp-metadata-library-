import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sources import hitomi


class HitomiThumbnailStatsTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('requests.sessions.Session.request',
                                side_effect=AssertionError('Network forbidden')))

    def collect(self, raw, **get_kwargs):
        with patch.object(hitomi, '_range_ids',
                          side_effect=[([1], 1, ''), ([], 1, '')]), \
             patch.object(hitomi, '_fetch_gallery_js', return_value=(raw, '')), \
             patch.object(hitomi, 'safe_get', **get_kwargs) as get:
            items, _, status = hitomi.collect()
        return items, status, get

    def test_html_exception_keeps_guessed_url_and_counts_reason(self):
        items, status, get = self.collect(
            {'files': [{'hash': 'abcdef'}]}, side_effect=TimeoutError('private detail'))
        self.assertEqual(items[0]['thumbnail'], 'https://tn.hitomi.la/bigtn/f/de/abcdef.jpg')
        self.assertEqual(status['thumbnails_found'], 1)
        self.assertEqual(status['thumbnails_missing'], 0)
        self.assertEqual(status['thumbnails_guessed'], 1)
        self.assertEqual(status['thumbnail_sources']['guessed'], 1)
        self.assertEqual(status['thumbnail_failure_reasons'], {'TimeoutError': get.call_count})
        self.assertNotIn('private detail', json.dumps(status))
        self.assertIn('https://tn.hitomi.la/bigtn/f/de/abcdef.jpg', json.dumps(items))
        self.assertEqual(copy.deepcopy(items)[0]['thumbnail'].source, 'guessed')

    def test_api_thumbnail_counts_as_api(self):
        items, status, get = self.collect({'thumbnail': 'https://example.com/cover.jpg'})
        self.assertEqual(items[0]['thumbnail'], 'https://example.com/cover.jpg')
        self.assertEqual(status['thumbnail_sources']['api'], 1)
        self.assertEqual(status['thumbnails_found'], 1)
        self.assertEqual(status['thumbnails_guessed'], 0)
        self.assertEqual(status['thumbnail_failure_reasons'], {})
        get.assert_not_called()

    def test_html_thumbnail_counts_as_html(self):
        items, status, _ = self.collect(
            {}, return_value=SimpleNamespace(text='<meta property="og:image" content="https://example.com/html.jpg">'))
        self.assertEqual(items[0]['thumbnail'], 'https://example.com/html.jpg')
        self.assertEqual(status['thumbnail_sources']['html'], 1)
        self.assertEqual(status['thumbnails_guessed'], 0)

    def test_missing_thumbnail_counts_as_missing(self):
        items, status, _ = self.collect({}, return_value=SimpleNamespace(text=''))
        self.assertEqual(items[0]['thumbnail'], '')
        self.assertEqual(status['thumbnail_sources']['missing'], 1)
        self.assertEqual(status['thumbnails_found'], 0)
        self.assertEqual(status['thumbnails_missing'], 1)

    def test_failure_reason_types_are_bounded(self):
        failures = [type(f'Failure{i}', (Exception,), {})() for i in range(8)]
        with patch.object(hitomi, 'GALLERY_PAGE_URLS', ['https://example.com/{gid}'] * 9):
            _, status, _ = self.collect({}, side_effect=failures + [failures[0]])
        self.assertEqual(len(status['thumbnail_failure_reasons']), 6)
        self.assertEqual(status['thumbnail_failure_reasons']['Failure0'], 2)

    def test_legacy_fetch_mock_still_works(self):
        with patch.object(hitomi, '_range_ids', side_effect=[([1], 1, ''), ([], 1, '')]), \
             patch.object(hitomi, '_fetch_one', return_value=({'thumbnail': 'legacy'}, '')):
            items, _, status = hitomi.collect()
        self.assertEqual(items, [{'thumbnail': 'legacy'}])
        self.assertEqual(status['thumbnails_found'], 1)
        self.assertEqual(status['thumbnail_sources']['unknown'], 1)
