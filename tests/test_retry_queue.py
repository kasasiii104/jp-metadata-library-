import contextlib
import copy
import io
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

import crawler as c
from sources import ehentai as eh, hitomi


class RetryQueueTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('requests.sessions.Session.request',
                                side_effect=AssertionError('Network forbidden')))
        self.enterContext(patch.object(eh.time, 'sleep'))
        self.enterContext(patch.object(eh, 'EH_LATEST_PAGES', 1))
        self.enterContext(patch.object(eh, 'EH_BACKFILL_PAGES', 0))

    def entry(self, failures=1, **extra):
        return dict(failures=failures, last_error='old',
                    first_failed_at='2026-11-09T00:00:00+00:00', **extra)

    def hitomi_run(self, state=None, ids=None, fail=()):
        def fetch(session, gid, base):
            if gid in fail:
                raise RuntimeError('x' * 400)
            return {'source_id': str(gid), 'language': 'japanese'}, ''
        with patch.object(hitomi, '_range_ids', side_effect=[([], 100, ''), (ids or [], 100, '')]), \
             patch.object(hitomi, '_fetch_one', side_effect=fetch) as fetch_mock:
            result = hitomi.collect(state)
        return (*result, fetch_mock)

    def eh_run(self, state=None, refs=None, rows=None, error=None):
        with patch.object(eh, '_discover', return_value=(refs or [], '50', {})), \
             patch.object(eh, 'safe_post_json', return_value={'gmetadata': rows or []},
                          side_effect=error) as post:
            result = eh.collect(state)
        return (*result, post)

    def row(self, gid, japanese=True):
        return {'gid': gid, 'tags': ['language:japanese'] if japanese else [], 'filecount': '1'}

    def test_hitomi_failure_queues_and_advances_offset(self):
        items, state, status, _ = self.hitomi_run({'backfill_offset': 10}, [1, 2], (2,))
        self.assertEqual(state['backfill_offset'], 12)
        self.assertEqual(state['retry_ids']['2']['failures'], 1)
        self.assertEqual(len(state['retry_ids']['2']['last_error']), 300)
        self.assertEqual(status['status'], 'ok')
        self.assertTrue(status['partial'])
        self.assertEqual((status['retry_queued'], status['retry_queue_size']), (1, 1))

    def test_hitomi_next_run_retries_first_and_removes_success(self):
        original = {'retry_ids': {'2': self.entry()}}
        snapshot = copy.deepcopy(original)
        items, state, status, fetch = self.hitomi_run(original, [3, 2])
        self.assertEqual([call.args[1] for call in fetch.call_args_list], [2, 3])
        self.assertEqual(state['retry_ids'], {})
        self.assertEqual(items[0]['source_id'], '2')
        self.assertEqual(status['retry_succeeded'], 1)
        self.assertEqual(original, snapshot)

    def test_hitomi_failure_limit_exhausts(self):
        with patch.object(hitomi, 'HITOMI_RETRY_MAX_FAILURES', 2):
            _, state, status, _ = self.hitomi_run({'retry_ids': {'2': self.entry()}}, fail=(2,))
        self.assertEqual(state['retry_ids'], {})
        self.assertEqual(state['retry_exhausted_ids'][0]['gid'], '2')
        self.assertEqual(status['retry_exhausted'], 1)
        self.assertEqual(status['status'], 'error')

    def test_hitomi_queue_capacity_and_exhausted_history(self):
        with patch.object(hitomi, 'HITOMI_RETRY_QUEUE_MAX', 0):
            _, state, status, _ = self.hitomi_run({'retry_exhausted_ids': [{}] * 200}, [2], (2,))
        self.assertNotIn('retry_ids', state)
        self.assertEqual(len(state['retry_exhausted_ids']), 200)
        self.assertEqual(status['retry_exhausted'], 1)

    def test_hitomi_index_failure_preserves_state(self):
        original = {'backfill_offset': 10, 'retry_ids': {'2': self.entry()}}
        with patch.object(hitomi, '_range_ids', side_effect=RuntimeError('index')), \
             patch.object(hitomi, '_fetch_one') as fetch:
            items, state, status = hitomi.collect(original)
        self.assertEqual(state, original)
        self.assertEqual(status['status'], 'error')
        fetch.assert_not_called()

    def test_hitomi_normal_run_does_not_add_queue(self):
        _, state, status, _ = self.hitomi_run(ids=[1])
        self.assertNotIn('retry_ids', state)
        self.assertNotIn('retry_exhausted_ids', state)
        self.assertFalse(status['partial'])

    def test_eh_missing_and_error_rows_are_queued(self):
        _, state, status, _ = self.eh_run(refs=[(1, 'a'), (2, 'b'), (3, 'c')],
                                        rows=[self.row(1), {'gid': 3, 'error': ''}])
        self.assertEqual(set(state['retry_refs']), {'2', '3'})
        self.assertEqual(state['retry_refs']['2']['token'], 'b')
        self.assertEqual(status['status'], 'ok')
        self.assertTrue(status['partial'])
        self.assertEqual(status['retry_queued'], 2)

    def test_eh_retry_has_separate_budget_and_returns_japanese(self):
        original = {'retry_refs': {'2': self.entry(token='b')}}
        snapshot = copy.deepcopy(original)
        with patch.object(eh, 'EH_MAX_GALLERIES_PER_RUN', 1):
            items, state, status, post = self.eh_run(original, [(3, 'c')], [self.row(2), self.row(3)])
        self.assertEqual([c.args[2]['gidlist'] for c in post.call_args_list], [[[2, 'b']], [[3, 'c']]])
        self.assertEqual({x['source_id'] for x in items}, {'2', '3'})
        self.assertEqual(state['retry_refs'], {})
        self.assertEqual(status['retry_succeeded'], 1)
        self.assertEqual(original, snapshot)

    def test_eh_retry_exhausts_and_all_missing_is_empty_partial(self):
        with patch.object(eh, 'EH_RETRY_MAX_FAILURES', 2):
            _, state, status, _ = self.eh_run({'retry_refs': {'2': self.entry(token='b')}})
        self.assertEqual(state['retry_refs'], {})
        self.assertEqual(state['retry_exhausted_refs'][0]['failures'], 2)
        self.assertEqual(status['retry_exhausted'], 1)
        self.assertEqual(status['status'], 'empty')
        self.assertTrue(status['partial'])

    def test_eh_metadata_exception_keeps_cursor(self):
        with patch.object(eh, 'EH_BACKFILL_PAGES', 1), patch.object(eh, 'EH_LATEST_PAGES', 0):
            _, state, status, _ = self.eh_run({'backfill_next': '100'}, [(2, 'b')], error=RuntimeError('gdata'))
        self.assertEqual(state['backfill_next'], '100')
        self.assertEqual(status['status'], 'error')

    def test_eh_normal_run_does_not_add_queue(self):
        _, state, status, _ = self.eh_run(refs=[(1, 'a')], rows=[self.row(1)])
        self.assertNotIn('retry_refs', state)
        self.assertNotIn('retry_exhausted_refs', state)
        self.assertFalse(status['partial'])

    def test_eh_non_japanese_retry_success_is_saved(self):
        items, state, status, _ = self.eh_run(
            {'retry_refs': {'2': self.entry(token='b')}}, rows=[self.row(2, False)])
        self.assertEqual(items, [])
        self.assertEqual(state['retry_refs'], {})
        self.assertEqual(status['status'], 'ok')
        self.assertEqual(status['retry_succeeded'], 1)

    def test_eh_retry_exception_increments_failures_without_advancing(self):
        original = {'backfill_next': '100', 'retry_refs': {'2': self.entry(token='b')}}
        _, state, status, _ = self.eh_run(original, error=RuntimeError('timeout'))
        self.assertEqual(state['retry_refs']['2']['failures'], 2)
        self.assertEqual(state['backfill_next'], '100')
        self.assertEqual(status['status'], 'error')
        self.assertTrue(status['partial'])

    def test_eh_missing_rows_advance_cursor_and_overflow_exhausts(self):
        with patch.object(eh, 'EH_BACKFILL_PAGES', 1), \
             patch.object(eh, 'EH_LATEST_PAGES', 0), \
             patch.object(eh, 'EH_RETRY_QUEUE_MAX', 1):
            _, state, status, _ = self.eh_run({'backfill_next': '100'}, [(1, 'a'), (2, 'b')])
        self.assertEqual(state['backfill_next'], '50')
        self.assertEqual(set(state['retry_refs']), {'1'})
        self.assertEqual(state['retry_exhausted_refs'][0]['gid'], '2')
        self.assertEqual(status['retry_exhausted'], 1)
        self.assertEqual(status['status'], 'empty')

    def assert_failed_run_preserves_state(self, source, original, collector):
        root = Path(self.enterContext(tempfile.TemporaryDirectory(
            dir=Path(__file__).resolve().parents[1])))
        for name, value in {
            "DOCS_DIR": root / "docs",
            "DATA_FILE": root / "docs/data.json",
            "STATE_FILE": root / "docs/crawl_state.json",
            "STATUS_FILE": root / "docs/source_status.json",
            "FILTER_STATE_FILE": root / "state/filter_audit.json",
        }.items():
            self.enterContext(patch.object(c, name, value))
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.enterContext(patch.object(c, "now_iso", return_value="2026-11-09T00:00:00+00:00"))
        self.enterContext(patch.object(c, "HITOMI_FILTER_AUDIT_LIMIT", 0))
        self.enterContext(patch.object(c.hentai3, "enrich_existing_for_filter", return_value={}))
        self.enterContext(patch.dict(c.SOURCES, {}, clear=True))
        c.save_json(c.STATE_FILE, {source: original})
        c.save_json(c.STATUS_FILE, {source: {"last_success": "previous"}})

        collected = []

        def collect(state):
            items, proposed_state, status, _ = collector(state)
            collected.append((items, proposed_state, status))
            return items, proposed_state, status

        c.SOURCES[source] = collect
        # Save through another healthy source to exercise the per-source guard.
        c.SOURCES['asmhentai'] = lambda state: ([], {'page': 2}, {'status': 'ok'})
        self.assertEqual(c.main(), 0)
        self.assertEqual(len(collected), 1)
        items, proposed_state, status = collected[0]
        self.assertEqual(items, [])
        self.assertNotEqual(status['status'], 'ok')
        self.assertNotEqual(proposed_state, original)
        saved = c.load_json(c.STATE_FILE, {})
        self.assertEqual(saved[source], original)
        self.assertEqual(saved['asmhentai'], {'page': 2})
        recorded = c.load_json(c.STATUS_FILE, {})[source]
        self.assertNotEqual(recorded['status'], 'ok')
        self.assertEqual(recorded['last_success'], 'previous')
        self.assertEqual(recorded['last_attempt'], '2026-11-09T00:00:00+00:00')

    def test_hitomi_all_failed_main_preserves_state(self):
        original = {'backfill_offset': 10, 'retry_ids': {'2': self.entry()}}
        self.assert_failed_run_preserves_state(
            'hitomi', original,
            lambda state: self.hitomi_run(state, [3], (2, 3)))

    def test_eh_all_failed_main_preserves_state(self):
        original = {'backfill_next': '100', 'retry_refs': {'2': self.entry(token='b')}}
        with patch.object(eh, 'EH_BACKFILL_PAGES', 1), patch.object(eh, 'EH_LATEST_PAGES', 0):
            self.assert_failed_run_preserves_state(
                'ehentai', original,
                lambda state: self.eh_run(state, [(3, 'c')]))

    def test_retry_limits_leave_remaining_entries_untouched(self):
        with patch.object(hitomi, 'HITOMI_RETRY_LIMIT', 1):
            _, state, _, fetch = self.hitomi_run({'retry_ids': {'1': self.entry(), '2': self.entry()}})
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(set(state['retry_ids']), {'2'})
        with patch.object(eh, 'EH_RETRY_LIMIT', 1):
            _, state, _, post = self.eh_run({'retry_refs': {'1': self.entry(token='a'), '2': self.entry(token='b')}}, rows=[self.row(1)])
        self.assertEqual(post.call_count, 1)
        self.assertEqual(set(state['retry_refs']), {'2'})


if __name__ == '__main__':
    unittest.main()
