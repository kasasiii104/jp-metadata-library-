import unittest
from unittest.mock import patch

from sources import ehentai as eh


class PartialMetadataTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('requests.sessions.Session.request',
                                side_effect=AssertionError('Network forbidden')))
        self.enterContext(patch.object(eh.time, 'sleep'))

    def refs(self):
        return [(gid, 'abcdef1234') for gid in range(1, 29)]

    def responses(self, **invalid):
        rows = [{'gid': gid, 'tags': ['language:japanese'], 'filecount': '1'}
                for gid, _ in self.refs()]
        rows[26].update(invalid)
        return [{'gmetadata': rows[:25]}, {'gmetadata': rows[25:]}]

    def test_second_batch_normalization_failure_keeps_other_items(self):
        for invalid in ({'filecount': 'invalid'}, {'rating': 'invalid'}, {'tags': 123}):
            with self.subTest(invalid=invalid), patch.object(
                    eh, 'safe_post_json', side_effect=self.responses(**invalid)) as post:
                failures = {}
                items = eh._metadata(None, self.refs(), failures)
            self.assertEqual({item['source_id'] for item in items},
                             {str(gid) for gid in range(1, 29)} - {'27'})
            self.assertEqual(set(failures), {'27'})
            self.assertTrue(failures['27'])
            self.assertEqual(post.call_count, 2)

    def test_collect_queues_only_invalid_gid_and_reports_partial(self):
        with patch.object(eh, 'EH_LATEST_PAGES', 1), \
             patch.object(eh, 'EH_BACKFILL_PAGES', 0), \
             patch.object(eh, 'EH_MAX_GALLERIES_PER_RUN', 28), \
             patch.object(eh, '_discover', return_value=(self.refs(), '', {})), \
             patch.object(eh, 'safe_post_json', side_effect=self.responses(filecount='invalid')):
            items, state, status = eh.collect()
        self.assertEqual({item['source_id'] for item in items},
                         {str(gid) for gid in range(1, 29)} - {'27'})
        self.assertEqual(set(state['retry_refs']), {'27'})
        self.assertEqual(state['retry_refs']['27']['token'], 'abcdef1234')
        self.assertEqual(state['retry_refs']['27']['failures'], 1)
        self.assertEqual(status['status'], 'ok')
        self.assertTrue(status['partial'])
        self.assertEqual(status['retry_queued'], 1)
        self.assertEqual(status['retry_queue_size'], 1)

    def test_whole_response_failure_keeps_cursor(self):
        for response in ({}, {'gmetadata': None}, {'gmetadata': {}}, []):
            with self.subTest(response=response), \
                 patch.object(eh, 'EH_LATEST_PAGES', 0), \
                 patch.object(eh, 'EH_BACKFILL_PAGES', 1), \
                 patch.object(eh, '_discover', return_value=([(1, 'abcdef1234')], '50', {})), \
                 patch.object(eh, 'safe_post_json', return_value=response):
                items, state, status = eh.collect({'backfill_next': '100'})
            self.assertEqual(items, [])
            self.assertEqual(state, {'backfill_next': '100'})
            self.assertEqual(status['status'], 'error')
            self.assertTrue(status['partial'])
