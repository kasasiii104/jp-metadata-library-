import io
import json
from contextlib import ExitStack, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools import cache_3hentai_thumbs as thumbs


class ThumbnailAttemptsTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        for name, path in {'DATA': root / 'data.json', 'STATUS': root / 'status.json',
                           'ATTEMPTS': root / 'state/attempts.json',
                           'OUTDIR': root / 'thumbs'}.items():
            self.stack.enter_context(patch.object(thumbs, name, path))
        self.stack.enter_context(patch.object(thumbs, 'LIMIT', 1))
        self.stack.enter_context(patch.object(thumbs, 'DELAY', 0))
        self.stack.enter_context(patch.object(thumbs, 'RETRY_SEC', 86400))
        self.stack.enter_context(patch.object(thumbs.time, 'sleep'))
        self.stack.enter_context(patch.object(thumbs.requests, 'Session',
                                             return_value=Mock(get=Mock(side_effect=AssertionError('network')))))
        self.download = self.stack.enter_context(patch.object(
            thumbs, 'download_thumbnail', return_value=(None, 'not image', False)))
        self.page = self.stack.enter_context(patch.object(
            thumbs, 'fetch_page_candidates', return_value=([], 'gallery_no_image_candidates', False)))
        self.resolve = self.stack.enter_context(patch.object(
            thumbs, 'resolve_search_fallback', return_value=(None, 'no exact-title match', False)))
        self.now = datetime(2026, 1, 3, tzinfo=timezone.utc)
        self.catalog('a', 'b', 'c')

    def catalog(self, *ids):
        thumbs.save(thumbs.DATA, {'items': [
            {'uid': '3hentai:' + sid, 'source': '3hentai', 'source_id': sid,
             'thumbnail': 'https://example.test/' + sid} for sid in ids]})

    def entry(self, age=25, stamp=None):
        return {'last_attempt': stamp or (self.now - timedelta(hours=age)).isoformat(),
                'reason': 'previous failure', 'failures': 2}

    def seed(self, entries):
        thumbs.save(thumbs.ATTEMPTS, {'version': 1, 'items': entries})

    def run_main(self, now=None):
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(thumbs.main(now=now or self.now), 0)
        return output.getvalue()

    def records(self):
        return json.loads(thumbs.ATTEMPTS.read_text())['items']

    def status(self):
        return json.loads(thumbs.STATUS.read_text())['3hentai']['thumbnail_cache']

    def tried(self):
        return [call.args[1] for call in self.download.call_args_list]

    def test_second_run_reaches_untried_after_permanent_failure(self):
        self.run_main()
        self.run_main()
        self.assertEqual(self.tried(), [['https://example.test/a'], ['https://example.test/b']])
        self.assertEqual(self.status()['deferred'], 1)
        self.assertEqual(self.status()['tracked_failures'], 2)

    def test_load_attempts_missing_file_returns_fresh_empty_records(self):
        """Missing state has its own message and never shares mutable defaults."""
        with redirect_stdout(io.StringIO()) as output:
            first = thumbs.load_attempts()
            first['items']['a'] = self.entry()
            second = thumbs.load_attempts()
        self.assertEqual(second, {'version': 1, 'items': {}})
        self.assertEqual(output.getvalue().count('attempt state missing; using empty records'), 2)

    def test_load_attempts_invalid_file_returns_empty_records(self):
        """Empty files, missing items, wrong types, versions and entries recover."""
        thumbs.ATTEMPTS.parent.mkdir()
        for raw in ('', '{}', '{"version": 1}', '{"version": 1, "items": []}',
                    '[]', '{"version": 2, "items": {}}',
                    '{"version": 1, "items": {"a": {}}}'):
            with self.subTest(raw=raw):
                thumbs.ATTEMPTS.write_text(raw)
                with redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(thumbs.load_attempts(), {'version': 1, 'items': {}})
                self.assertIn('invalid attempt state; using empty records:', output.getvalue())

    def test_load_attempts_revalidates_unexpected_mock_returns(self):
        """Even loaders that bypass validation cannot leak malformed state."""
        self.seed({})
        for value in ({}, [], {'version': 1}, {'version': 1, 'items': []},
                      {'version': True, 'items': {}},
                      {'version': 1, 'items': {'a': {}}}):
            with self.subTest(value=value), patch.object(thumbs, 'load', return_value=value):
                with redirect_stdout(io.StringIO()) as output:
                    first = thumbs.load_attempts()
                    self.assertEqual(first, {'version': 1, 'items': {}})
                    first['items']['a'] = self.entry()
                    self.assertEqual(thumbs.load_attempts(), {'version': 1, 'items': {}})
                self.assertIn('invalid attempt state; using empty records:', output.getvalue())

    def test_load_attempts_preserves_valid_entries(self):
        """Valid state keeps timestamps, reasons and failure counts unchanged."""
        entries = {'a': self.entry()}
        self.seed(entries)
        self.assertEqual(thumbs.load_attempts(), {'version': 1, 'items': entries})

    def test_main_saves_valid_state_after_unexpected_load_return(self):
        """Recovery also protects the state passed to save, including new failures."""
        self.seed({})
        original_load = thumbs.load
        for value in ({}, [], {'version': 1}):
            with self.subTest(value=value):
                with patch.object(thumbs, 'load', side_effect=lambda path, default, validator:
                                  value if path == thumbs.ATTEMPTS else original_load(path, default, validator)), \
                        patch.object(thumbs, 'save') as save:
                    self.run_main()
                states = [call.args[1] for call in save.call_args_list
                          if call.args[0] == thumbs.ATTEMPTS]
                self.assertEqual(len(states), 1)
                thumbs.validate_attempts(states[0])
                self.assertEqual(states[0], {'version': 1, 'items': {'a': {
                    'last_attempt': self.now.isoformat(), 'reason': 'not image', 'failures': 1}}})

    def test_retry_interval_boundary_and_oldest_first(self):
        self.catalog('a', 'b')
        self.seed({'a': self.entry(age=23), 'b': self.entry(age=26)})
        self.run_main()
        self.assertEqual(self.tried(), [['https://example.test/b']])
        self.run_main(self.now + timedelta(minutes=59))
        self.assertEqual(self.status()['attempted'], 0)
        self.run_main(self.now + timedelta(hours=1))
        self.assertEqual(self.tried()[-1], ['https://example.test/a'])
        self.seed({'a': self.entry(age=25), 'b': self.entry(age=27)})
        self.run_main()
        self.assertEqual(self.tried()[-1], ['https://example.test/b'])

    def test_untried_precedes_due_retry_and_invalid_timestamp_is_due(self):
        self.seed({'a': self.entry(stamp='bad timestamp'), 'b': self.entry()})
        self.run_main()
        self.assertEqual(self.tried(), [['https://example.test/c']])
        self.run_main()
        self.assertEqual(self.tried()[-1], ['https://example.test/a'])

    def test_success_reused_and_removed_records_are_cleaned(self):
        self.catalog("a", "b")
        self.seed({sid: self.entry() for sid in ('a', 'b', 'removed')})
        thumbs.OUTDIR.mkdir()
        (thumbs.OUTDIR / 'b.webp').write_bytes(b'x' * 513)
        self.download.return_value = (b'x' * 600, 'image url', False)
        self.run_main()
        self.assertEqual(self.records(), {})
        self.assertEqual(self.status()['reused'], 1)

    def test_rate_limit_and_upstream_stop_do_not_record_failure(self):
        for route in ('image', 'page', 'search429', 'upstream'):
            with self.subTest(route=route):
                self.catalog('a', 'b')
                self.seed({})
                self.download.return_value = (None, '429 from image', route == 'image')
                data = json.loads(thumbs.DATA.read_text())
                item = data['items'][0]
                if route != 'image':
                    item.update(thumbnail='', source_url='https://example.test/d/a', source_url_kind='direct')
                if route in ('search429', 'upstream'):
                    item.update(source_url='https://example.test/search?q=a', source_url_kind='search')
                    self.resolve.return_value = (None, '429' if route == 'search429' else 'HTTP 503', True)
                self.page.return_value = ([], '429 from gallery', True)
                thumbs.save(thumbs.DATA, data)
                self.run_main()
                self.assertEqual(self.records(), {})
                self.assertEqual(self.status()['attempted'], 1)
                self.assertTrue(self.status()['upstream_unavailable' if route == 'upstream' else 'rate_limited'])

    def test_missing_corrupt_and_wrong_shape_start_in_catalog_order(self):
        for raw in (None, '{broken', '{"version": 2, "items": {}}',
                    '{"version": 1, "items": {"a": {}}}'):
            with self.subTest(raw=raw):
                if raw is not None:
                    thumbs.ATTEMPTS.parent.mkdir(exist_ok=True)
                    thumbs.ATTEMPTS.write_text(raw)
                self.download.reset_mock()
                output = self.run_main()
                self.assertEqual(self.tried(), [['https://example.test/a']])
                self.assertIn('using empty records', output)
                thumbs.validate_attempts(json.loads(thumbs.ATTEMPTS.read_text()))

    def test_missing_state_each_run_preserves_original_order(self):
        for _ in range(2):
            with patch.object(thumbs.ATTEMPTS.__class__, 'exists', autospec=True,
                              side_effect=lambda path: False if path == thumbs.ATTEMPTS else path.is_file()):
                self.run_main()
        self.assertEqual(self.tried(), [['https://example.test/a']] * 2)

    def test_saved_schema_reason_truncation_and_failure_increment(self):
        self.catalog('a')
        self.seed({'a': self.entry()})
        self.download.return_value = (None, 'x' * 300, False)
        self.run_main()
        stored = json.loads(thumbs.ATTEMPTS.read_text())
        self.assertEqual(stored, {'version': 1, 'items': {'a': {
            'last_attempt': self.now.isoformat(), 'reason': 'x' * 200, 'failures': 3}}})

    def test_page_and_search_failure_reasons(self):
        for search in (False, True):
            self.catalog('a')
            self.seed({})
            data = json.loads(thumbs.DATA.read_text())
            data['items'][0].update(thumbnail='', source_url='https://example.test/' + ('search' if search else 'd/a'),
                                    source_url_kind='search' if search else 'direct')
            thumbs.save(thumbs.DATA, data)
            self.download.return_value = (None, '', False)
            self.run_main()
            self.assertEqual(self.records()['a']['reason'],
                             'no exact-title match' if search else 'gallery_no_image_candidates')


if __name__ == '__main__':
    unittest.main()
