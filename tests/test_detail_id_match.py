import unittest
from unittest.mock import patch

from sources import asmhentai, hitomi


class DetailIdMatchTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('requests.sessions.Session.request',
                                side_effect=AssertionError('Network forbidden')))

    def run_hitomi(self, raw):
        with patch.object(hitomi, '_range_ids',
                          side_effect=[([123], 1000, ''), ([], 1000, '')]), \
             patch.object(hitomi, '_fetch_gallery_js', return_value=(raw, '')), \
             patch.object(hitomi, '_thumbnail', return_value='thumb') as thumb:
            result = hitomi.collect({})
        return (*result, thumb)

    def test_hitomi_mismatch_queues_without_thumbnail(self):
        items, state, status, thumb = self.run_hitomi(
            {'id': 456, 'title': 'wrong', 'language': 'japanese'})
        self.assertEqual(items, [])
        self.assertEqual(state['retry_ids']['123']['failures'], 1)
        self.assertIn('identity mismatch', state['retry_ids']['123']['last_error'])
        self.assertEqual(status['retry_queued'], 1)
        thumb.assert_not_called()

    def test_hitomi_matching_and_missing_ids_are_accepted(self):
        for identity in ({'id': '123'}, {'id': 123}, {}, {'id': None}):
            with self.subTest(identity=identity):
                items, state, _, thumb = self.run_hitomi(
                    dict(title='correct', language='japanese', **identity))
                self.assertEqual([item['source_id'] for item in items], ['123'])
                self.assertNotIn('123', state.get('retry_ids', {}))
                thumb.assert_called_once()

    def run_asmhentai(self, detail):
        raw = {'id': '123', 'title': 'search', 'language': 'japanese'}
        with patch.object(asmhentai, 'SEARCH_KEYS', ['japanese']), \
             patch.object(asmhentai, 'PAGES', 1), \
             patch.object(asmhentai, 'DETAIL_LIMIT', 1), \
             patch.object(asmhentai, '_search', return_value=[raw]), \
             patch.object(asmhentai, '_get', return_value=detail):
            return asmhentai.collect({})

    def test_asmhentai_mismatch_uses_existing_failure_fallback(self):
        for key in ('id', 'gallery_id', 'galleryId', 'book', 'source_id', 'gid'):
            with self.subTest(key=key):
                items, _, status = self.run_asmhentai(
                    {key: 456, 'title': 'wrong', 'language': 'japanese'})
                self.assertEqual([(x['source_id'], x['title']) for x in items],
                                 [('123', 'search')])
                self.assertEqual(status['detail_success'], 0)
                self.assertIn('get=123: ASMHentai gallery identity mismatch', status['message'])

    def test_asmhentai_matching_ids_are_accepted(self):
        for identity in (123, '123'):
            with self.subTest(identity=identity):
                items, _, status = self.run_asmhentai(
                    {'id': identity, 'title': 'detail', 'language': 'japanese'})
                self.assertEqual([(x['source_id'], x['title']) for x in items],
                                 [('123', 'detail')])
                self.assertEqual(status['detail_success'], 1)


if __name__ == '__main__':
    unittest.main()
