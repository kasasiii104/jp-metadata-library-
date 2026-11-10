import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sources import ehentai as eh


class CursorTerminalTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('requests.sessions.Session.request',
                                side_effect=AssertionError('Network forbidden')))
        self.enterContext(patch.object(eh, 'EH_LATEST_PAGES', 1))
        self.enterContext(patch.object(eh, 'EH_BACKFILL_PAGES', 1))

    def run_listing(self, pagination, state=None):
        latest = '<a href="/g/2/abcdef1234/">latest</a>'
        history = '<a href="/g/1/abcdef1234/">history</a>' + pagination
        with patch.object(eh, 'safe_get', side_effect=[
                SimpleNamespace(text=latest), SimpleNamespace(text=history)]), \
             patch.object(eh, '_metadata', side_effect=lambda session, refs, failures: [
                 {'source_id': str(gid), 'language': 'japanese'} for gid, _ in refs]):
            return eh.collect(state or {'backfill_next': '100'})

    def test_next_cursor_advances(self):
        for marker in ('unext', 'dnext'):
            with self.subTest(marker=marker):
                items, state, status = self.run_listing(
                    f'<a id="{marker}" href="/?next=50">Next</a>')
                self.assertEqual(state['backfill_next'], '50')
                self.assertFalse(state.get('backfill_exhausted'))
                self.assertFalse(status['partial'])

    def test_explicit_terminal_exhausts(self):
        for pagination in ('<span id="unext">Next</span>',
                           '<span id="dnext">Next</span>',
                           '<a id="unext">Next</a>'):
            with self.subTest(pagination=pagination):
                _, state, status = self.run_listing(pagination)
                self.assertTrue(state['backfill_exhausted'])
                self.assertNotIn('backfill_next', state)
                self.assertFalse(status['partial'])

    def test_unparseable_href_is_unknown(self):
        for pagination in ('<a id="unext" href="/">Next</a>',
                           '<a id="unext" href="/?next=abc">Next</a>',
                           '<span id="dnext"><a href="/">Next</a></span>'):
            with self.subTest(pagination=pagination):
                self.assertIsNone(eh._extract_next_cursor(pagination))
                _, state, status = self.run_listing(pagination)
                self.assertEqual(state, {'backfill_next': '100'})
                self.assertTrue(status['partial'])

    def test_missing_pagination_preserves_state_and_latest_items(self):
        original = {'backfill_next': '100', 'custom': 'retained'}
        items, state, status = self.run_listing('', original)
        self.assertEqual(state, original)
        self.assertFalse(state.get('backfill_exhausted'))
        self.assertIn('2', {item['source_id'] for item in items})
        self.assertTrue(status['partial'])
        self.assertIn('Unable to determine next backfill cursor', status['message'])

    def test_later_parse_failure_preserves_original_cursor(self):
        with patch.object(eh, 'EH_LATEST_PAGES', 0), \
             patch.object(eh, 'EH_BACKFILL_PAGES', 2), \
             patch.object(eh.time, 'sleep'), \
             patch.object(eh, 'safe_get', side_effect=[
                 SimpleNamespace(text='<a href="/g/1/abcdef1234/"></a>'
                                      '<a id="unext" href="/?next=50">Next</a>'),
                 SimpleNamespace(text='<a href="/g/2/abcdef1234/"></a>')]), \
             patch.object(eh, '_metadata', return_value=[]):
            _, state, status = eh.collect({'backfill_next': '100'})
        self.assertEqual(state, {'backfill_next': '100'})
        self.assertTrue(status['partial'])
