import contextlib
import unittest
from unittest.mock import Mock, patch

from sources import hentai3 as h


class DuplicateTitleTests(unittest.TestCase):
    def collect_with_cards(self, cards):
        with contextlib.ExitStack() as stack:
            for name, value in {
                'HENTAI3_LATEST_PAGES': 1,
                'HENTAI3_BACKFILL_PAGES': 0,
                'HENTAI3_MAX_GALLERIES_PER_RUN': 10,
                'HENTAI3_GET_DEBUG_LIMIT': 0,
                'HENTAI3_GALLERY_METADATA_LIMIT': 0,
            }.items():
                stack.enter_context(patch.object(h, name, value))
            session = Mock()
            session.get.return_value.status_code = 200
            session.request.side_effect = AssertionError('Network forbidden')
            stack.enter_context(patch.object(h.requests, 'Session', return_value=session))
            stack.enter_context(patch.object(
                h.requests.sessions.Session, 'request',
                side_effect=AssertionError('Network forbidden')))
            stack.enter_context(patch.object(
                h, '_direct_search_cards', return_value=(cards, {})))
            stack.enter_context(patch.object(
                h, '_get_json',
                return_value=([{'id': 999, 'title': 'Sample Title'}], 'mock')))
            stack.enter_context(patch.object(h.time, 'sleep'))
            items, _, status = h.collect({})
        self.assertEqual(status['status'], 'ok')
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['uid'], '3hentai:999')
        return items[0]

    def card(self, title, gallery_id):
        return {
            'title': title,
            'gallery_id': gallery_id,
            'source_url': 'https://3hentai.net/d/' + gallery_id,
            'thumbnail': 'https://3hentai.net/images/' + gallery_id + '.jpg',
        }

    def test_normalized_duplicate_titles_do_not_attach_card_metadata(self):
        cards = [self.card('Ｓａｍｐｌｅ　Ｔｉｔｌｅ', '123'),
                 self.card('sample-title', '456')]
        for order in (cards, list(reversed(cards))):
            with self.subTest(first_gallery_id=order[0]['gallery_id']):
                item = self.collect_with_cards(order)
                self.assertEqual(item['source_url_kind'], 'search')
                for card in cards:
                    self.assertNotEqual(item.get('source_url'), card['source_url'])
                self.assertFalse(item.get('thumbnail'))
                self.assertFalse(item.get('resolved_gallery_id'))
                self.assertFalse(item.get('public_gallery_id'))

    def test_single_exact_title_match_attaches_card_metadata(self):
        card = self.card('Sample Title', '123')
        item = self.collect_with_cards([self.card('Other Title', '456'), card])
        self.assertEqual(item['source_url_kind'], 'direct')
        self.assertEqual(item['source_url'], card['source_url'])
        self.assertEqual(item['thumbnail'], card['thumbnail'])
        self.assertEqual(item['resolved_gallery_id'], '123')
