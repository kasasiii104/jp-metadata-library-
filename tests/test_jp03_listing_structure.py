"""Real captured listings and DOM-derived synthetic structure variants."""
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bs4 import BeautifulSoup
from sources import ehentai as eh, hentai3, hitomi

FIXTURES = Path(__file__).parent / 'fixtures' / 'jp03'


class ListingStructureTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('requests.sessions.Session.request',
                                side_effect=AssertionError('Network forbidden')))
        self.enterContext(patch.object(eh.time, 'sleep'))

    def html(self, name):
        return (FIXTURES / ('html_list_' + name + '.html')).read_text(encoding='utf-8')

    def variant(self, html, selector, operation):
        # These variants are synthetic; they do not prove actual empty-page markup.
        soup = BeautifulSoup(html, 'html.parser')
        container = soup.select_one(selector)
        self.assertIsNotNone(container)
        if operation == 'unwrap':
            container.unwrap()
        elif operation == 'empty':
            for child in list(container.contents):
                child.extract()
        else:
            container.decompose()
            if operation == 'remove_all':
                # Also drop the pager so the page exposes no next cursor.
                for link in soup.select('a[href*="next="]'):
                    link.decompose()
        result = str(soup)
        if operation == 'remove_all':
            self.assertIsNone(eh._extract_next_cursor(result))
        if operation != 'unwrap':
            self.assertEqual(eh._refs_from_html(result), [])
            self.assertIsNone(eh.GALLERY_RE.search(result))
        return result

    def discover(self, html):
        response = SimpleNamespace(text=html, content=html.encode('utf-8'),
                                   url='https://e-hentai.org/?next=100')
        with patch.object(eh, 'safe_get', return_value=response):
            return eh._discover(object())

    def direct(self, html):
        response = SimpleNamespace(text=html, status_code=200,
                                   url='https://3hentai.net/search',
                                   raise_for_status=lambda: None)
        session = Mock()
        session.get.return_value = response
        return hentai3._direct_search_cards(session, 'language:japanese', 1)

    def test_eh_real_listings_and_unwrapped_refs(self):
        for name, cursor in [('ehentai_latest', '4332765'),
                             ('ehentai_japanese', '4332681')]:
            original = self.html(name)
            for operation, observation in [(None, 'present_nonempty'),
                                           ('unwrap', 'missing_nonempty')]:
                with self.subTest(name=name, operation=operation):
                    html = original if operation is None else self.variant(original, '.itg', operation)
                    refs, next_cursor, debug = self.discover(html)
                    self.assertEqual(len(refs), 25)
                    self.assertEqual(refs, eh._refs_from_html(original))
                    self.assertEqual(next_cursor, cursor)
                    self.assertEqual(debug['listing_container_present'], operation is None)
                    self.assertEqual(debug['listing_observation'], observation)

    def test_eh_present_empty_is_accepted(self):
        for name in ('ehentai_latest', 'ehentai_japanese'):
            with self.subTest(name=name):
                html = self.variant(self.html(name), '.itg', 'empty')
                refs, _, debug = self.discover(html)
                self.assertEqual(refs, [])
                self.assertTrue(debug['listing_container_present'])
                self.assertEqual(debug['listing_observation'], 'present_empty')

    def test_eh_missing_empty_raises(self):
        for name in ('ehentai_latest', 'ehentai_japanese'):
            with self.subTest(name=name):
                html = self.variant(self.html(name), '.itg', 'remove_all')
                with self.assertRaisesRegex(ValueError, 'listing_structure_missing:.*https://e-hentai.org/.*bytes='):
                    self.discover(html)

    def test_eh_real_zero_hit_page_is_a_valid_empty(self):
        # Captured zero-hit search: no `.itg`, no /g/ links, "No hits found".
        html = self.html('ehentai_empty')
        self.assertIsNone(BeautifulSoup(html, 'html.parser').select_one('.itg'))
        refs, next_cursor, debug = self.discover(html)
        self.assertEqual(refs, [])
        self.assertIsNone(next_cursor)
        self.assertFalse(debug['listing_container_present'])
        self.assertEqual(debug['listing_observation'], 'missing_empty')
        self.assertTrue(debug['listing_no_hits'])

    def test_eh_zero_hit_page_without_marker_raises(self):
        html = self.html('ehentai_empty').replace('No hits found', '')
        with self.assertRaisesRegex(ValueError, 'listing_structure_missing'):
            self.discover(html)

    def test_eh_real_listings_have_no_zero_hit_marker(self):
        for name in ('ehentai_latest', 'ehentai_japanese'):
            with self.subTest(name=name):
                _, _, debug = self.discover(self.html(name))
                self.assertFalse(debug['listing_no_hits'])

    def test_eh_collect_real_zero_hit_pages_are_empty_not_error(self):
        empty = self.html('ehentai_empty')
        items, state, status = self.collect_pages([empty, empty])
        self.assertEqual(items, [])
        self.assertEqual(state, {'backfill_next': '100', 'custom': 'retained'})
        self.assertEqual(status['status'], 'empty')
        self.assertFalse(status['partial'])
        self.assertEqual(status['message'], '')

    def test_eh_collect_real_zero_hit_latest_does_not_mark_partial(self):
        items, state, status = self.collect_pages(
            [self.html('ehentai_empty'), self.html('ehentai_japanese')])
        self.assertEqual(len(items), 25)
        self.assertEqual(state['backfill_next'], '4332681')
        self.assertEqual(status['status'], 'ok')
        self.assertFalse(status['partial'])
        self.assertEqual(status['message'], '')

    def test_3h_real_nonsense_search_still_returns_cards(self):
        # Captured nonsense-term search: the site falls back to results, so a
        # zero-card shape was never observed. Observation only, no failure.
        cards, info = self.direct(self.html('3hentai_empty'))
        self.assertTrue(cards)
        self.assertTrue(info['listing_container_present'])
        self.assertEqual(info['listing_observation'], 'present_nonempty')
        self.assertNotIn('warning', info)
        self.assertEqual(info['error'], '')

    def test_eh_missing_empty_with_next_cursor_is_not_rejected(self):
        # Listing gone but the pager still gives a cursor: keep flowing as the
        # baseline did so later pages stay reachable.
        html = self.variant(self.html('ehentai_latest'), '.itg', 'remove')
        refs, next_cursor, debug = self.discover(html)
        self.assertEqual(refs, [])
        self.assertEqual(next_cursor, '4332765')
        self.assertEqual(debug['listing_observation'], 'missing_empty')
        self.assertFalse(debug['listing_no_hits'])

    def test_eh_collect_empty_latest_page_with_cursor_still_reaches_page_two(self):
        first = self.variant(self.html('ehentai_latest'), '.itg', 'remove')
        responses = [SimpleNamespace(text=html, content=html.encode('utf-8'),
                                     url='https://e-hentai.org/')
                     for html in (first, self.html('ehentai_japanese'))]
        with patch.object(eh, 'EH_LATEST_PAGES', 2), \
             patch.object(eh, 'EH_BACKFILL_PAGES', 1), \
             patch.object(eh, 'EH_MAX_GALLERIES_PER_RUN', 100), \
             patch.object(eh, 'safe_get', side_effect=responses) as get, \
             patch.object(eh, '_metadata', side_effect=lambda session, refs, failures: [
                 {'source_id': str(gid), 'language': 'japanese'} for gid, _ in refs]):
            items, state, status = eh.collect({'backfill_exhausted': True})
        self.assertEqual(get.call_count, 2)
        self.assertEqual(get.call_args_list[1].kwargs['params'], {'next': '4332765'})
        self.assertEqual(len(items), 25)
        self.assertEqual(status['status'], 'ok')
        self.assertEqual(status['latest_pages_fetched'], 2)
        self.assertEqual(status['message'], '')
        self.assertEqual(state, {'backfill_exhausted': True})

    def test_eh_container_parse_failure_is_not_rejected(self):
        html = self.variant(self.html('ehentai_latest'), '.itg', 'remove_all')
        with patch.object(eh, 'BeautifulSoup', side_effect=[
                ValueError('synthetic parser failure'), BeautifulSoup(html, 'html.parser')]):
            refs, _, debug = self.discover(html)
        self.assertEqual(refs, [])
        self.assertTrue(debug['listing_container_present'])
        self.assertEqual(debug['listing_observation'], 'present_empty')

    def test_eh_missing_diagnostic_has_byte_length_and_short_title(self):
        html = self.variant(self.html('ehentai_latest'), '.itg', 'remove_all')
        soup = BeautifulSoup(html, 'html.parser')
        soup.title.string = '診断' * 100
        html = str(soup)
        with self.assertRaises(ValueError) as raised:
            self.discover(html)
        message = str(raised.exception)
        self.assertIn(f'bytes={len(html.encode("utf-8"))}', message)
        self.assertEqual(message.split(' title=')[1], ('診断' * 100)[:120])

    def collect_pages(self, pages):
        responses = [SimpleNamespace(text=html, content=html.encode('utf-8'),
                                     url='https://e-hentai.org/') for html in pages]
        with patch.object(eh, 'EH_LATEST_PAGES', 1), \
             patch.object(eh, 'EH_BACKFILL_PAGES', 1), \
             patch.object(eh, 'EH_MAX_GALLERIES_PER_RUN', 100), \
             patch.object(eh, 'safe_get', side_effect=responses), \
             patch.object(eh, '_metadata', side_effect=lambda session, refs, failures: [
                 {'source_id': str(gid), 'language': 'japanese'} for gid, _ in refs]):
            return eh.collect({'backfill_next': '100', 'custom': 'retained'})

    def test_eh_collect_missing_latest_keeps_history_progress(self):
        missing = self.variant(self.html('ehentai_latest'), '.itg', 'remove_all')
        items, state, status = self.collect_pages([missing, self.html('ehentai_japanese')])
        self.assertEqual(len(items), 25)
        self.assertEqual(state['backfill_next'], '4332681')
        self.assertEqual(state['custom'], 'retained')
        self.assertEqual(status['status'], 'ok')
        self.assertTrue(status['partial'])
        self.assertIn('listing_structure_missing', status['message'])

    def test_eh_collect_all_missing_is_error_and_preserves_cursor(self):
        missing = self.variant(self.html('ehentai_latest'), '.itg', 'remove_all')
        items, state, status = self.collect_pages([missing, missing])
        self.assertEqual(items, [])
        self.assertEqual(state, {'backfill_next': '100', 'custom': 'retained'})
        self.assertEqual(status['status'], 'error')
        self.assertTrue(status['partial'])
        self.assertIn('latest-global 1: listing_structure_missing', status['message'])
        self.assertIn('backfill-japanese 1: listing_structure_missing', status['message'])

    def test_3h_real_and_unwrapped_cards(self):
        original = self.html('3hentai_search')
        expected, _ = self.direct(original)
        for operation, observation in [(None, 'present_nonempty'), ('unwrap', 'missing_nonempty')]:
            with self.subTest(operation=operation):
                html = original if operation is None else self.variant(original, '.listing-container', operation)
                cards, info = self.direct(html)
                self.assertEqual(len(cards), 14)
                self.assertEqual(cards, expected)
                self.assertEqual(info['listing_container_present'], operation is None)
                self.assertEqual(info['listing_observation'], observation)
                self.assertNotIn('warning', info)
                self.assertEqual(info['error'], '')

    def test_3h_missing_empty_warns_without_error(self):
        html = self.variant(self.html('3hentai_search'), '.listing-container', 'remove')
        cards, info = self.direct(html)
        self.assertEqual(cards, [])
        self.assertEqual(info['warning'], 'listing_structure_missing')
        self.assertFalse(info['listing_container_present'])
        self.assertEqual(info['listing_observation'], 'missing_empty')
        self.assertEqual(info['error'], '')

    def test_3h_present_empty_has_no_warning(self):
        html = self.variant(self.html('3hentai_search'), '.listing-container', 'empty')
        cards, info = self.direct(html)
        self.assertEqual(cards, [])
        self.assertTrue(info['listing_container_present'])
        self.assertEqual(info['listing_observation'], 'present_empty')
        self.assertNotIn('warning', info)
        self.assertEqual(info['error'], '')


class HitomiStructureRegressionTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('requests.sessions.Session.request',
                                side_effect=AssertionError('Network forbidden')))

    def response(self, data):
        return SimpleNamespace(status_code=200, content=data, headers={})

    def test_invalid_first_host_falls_back(self):
        with patch.object(hitomi, 'safe_get', side_effect=[
                self.response(b'bad'), self.response((42).to_bytes(4, 'big'))]) as get:
            self.assertEqual(hitomi._range_ids(object(), 0, 1),
                             ([42], None, hitomi.RESOURCE_BASES[1]))
        self.assertEqual([call.args[1] for call in get.call_args_list],
                         [base + hitomi.INDEX_PATH for base in hitomi.RESOURCE_BASES])

    def test_all_hosts_misaligned_fail(self):
        with patch.object(hitomi, 'safe_get', return_value=self.response(b'bad')) as get:
            with self.assertRaisesRegex(RuntimeError, 'all Hitomi resource hosts failed') as raised:
                hitomi._range_ids(object(), 0, 1)
        self.assertEqual(get.call_count, len(hitomi.RESOURCE_BASES))
        for base in hitomi.RESOURCE_BASES:
            self.assertIn(base, str(raised.exception))

    def test_full_200_at_or_beyond_eof_is_empty(self):
        for start in (1, 2):
            with self.subTest(start=start), patch.object(hitomi, 'safe_get',
                    return_value=self.response((42).to_bytes(4, 'big'))):
                self.assertEqual(hitomi._fetch_range_from_base(object(), 'https://example.invalid', start, 1),
                                 ([], None))

    def test_nonpositive_count_does_not_fetch(self):
        with patch.object(hitomi, 'safe_get') as get:
            for count in (0, -1):
                with self.subTest(count=count):
                    self.assertEqual(hitomi._fetch_range_from_base(object(), hitomi.RESOURCE_BASES[0], 0, count),
                                     ([], None))
                    self.assertEqual(hitomi._range_ids(object(), 0, count),
                                     ([], None, hitomi.RESOURCE_BASES[0]))
            get.assert_not_called()
