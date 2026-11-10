import copy
import unittest
from unittest.mock import patch

import requests

from sources import hitomi


class HitomiBackfillIsolationTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('requests.sessions.Session.request',
                                side_effect=AssertionError('Network forbidden')))

    def test_backfill_failure_keeps_latest_and_offset_on_repeated_runs(self):
        for error in (RuntimeError('index failure'), ValueError('invalid index'),
                      requests.HTTPError('416 Client Error')):
            with self.subTest(error=type(error).__name__):
                state = {'backfill_offset': 123, 'backfill_complete': True}
                for gid in (1, 2):
                    original = copy.deepcopy(state)
                    with patch.object(hitomi, '_range_ids', side_effect=[
                        ([gid], 200, hitomi.RESOURCE_BASES[0]), error,
                    ]) as ranges, patch.object(hitomi, '_fetch_one', return_value=(
                        {'source_id': str(gid)}, hitomi.RESOURCE_BASES[0],
                    )):
                        items, state, status = hitomi.collect(state)
                    self.assertEqual([item['source_id'] for item in items], [str(gid)])
                    self.assertEqual(state['backfill_offset'], 123)
                    self.assertEqual(state['backfill_complete'], original['backfill_complete'])
                    self.assertTrue(status['partial'])
                    self.assertEqual(status['status'], 'ok')
                    self.assertIn('backfill:', status['message'])
                    self.assertEqual(ranges.call_count, 2)
                    self.assertEqual(ranges.call_args_list[1].args[1], 123)

    def test_backfill_failure_continues_retry_queue_and_bounds_error(self):
        original = {'backfill_offset': 123, 'retry_ids': {
            '9': {'failures': 1, 'last_error': 'old'},
        }}
        snapshot = copy.deepcopy(original)

        def fetch(session, gid, base):
            return {'source_id': str(gid)}, base

        with patch.object(hitomi, '_range_ids', side_effect=[
            ([1], 200, hitomi.RESOURCE_BASES[0]), RuntimeError('x' * 1000),
        ]), patch.object(hitomi, '_fetch_one', side_effect=fetch) as fetch_mock:
            items, state, status = hitomi.collect(original)

        self.assertEqual([call.args[1] for call in fetch_mock.call_args_list], [9, 1])
        self.assertEqual([item['source_id'] for item in items], ['9', '1'])
        self.assertEqual(state['retry_ids'], {})
        self.assertEqual(state['backfill_offset'], 123)
        self.assertEqual(status['retry_succeeded'], 1)
        self.assertTrue(status['partial'])
        self.assertEqual(status['message'], 'backfill: ' + 'x' * 300)
        self.assertEqual(original, snapshot)
