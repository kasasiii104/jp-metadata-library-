import unittest
from unittest.mock import call, patch

from sources import asmhentai


class ASMHentaiDedupeBudgetTests(unittest.TestCase):
    def test_duplicates_across_results_leave_budget_for_next_id(self):
        for keys, pages in ((['japanese'], 2), (['japanese', '日本語'], 1)):
            with self.subTest(keys=keys, pages=pages):
                # Unqualified rows keep collection going across search keys.
                first = {'id': '123', 'title': 'first'}
                second = {'id': '456', 'title': 'second'}
                with patch.object(asmhentai, 'SEARCH_KEYS', keys), \
                     patch.object(asmhentai, 'PAGES', pages), \
                     patch.object(asmhentai, 'DETAIL_LIMIT', 2), \
                     patch.object(asmhentai.requests, 'Session'), \
                     patch.object(asmhentai, '_search', side_effect=[
                         [first], [dict(first), second]]), \
                     patch.object(asmhentai, '_get', return_value={}) as get:
                    _, state, status = asmhentai.collect({})

                self.assertEqual(get.call_args_list, [
                    call(unittest.mock.ANY, '123'),
                    call(unittest.mock.ANY, '456'),
                ])
                self.assertEqual(status['detail_attempts'], 2)
                self.assertEqual(status['detail_candidates'], 2)
                self.assertEqual(state['detail_cursor'], 0)

    def test_cursor_counts_only_unique_valid_ids(self):
        rows = [
            {'id': '../bad', 'title': 'invalid'},
            {'id': '123', 'title': 'first'},
            {'id': '123', 'title': 'duplicate'},
            {'id': '456', 'title': 'second'},
        ]
        with patch.object(asmhentai, 'SEARCH_KEYS', ['japanese']), \
             patch.object(asmhentai, 'PAGES', 1), \
             patch.object(asmhentai, 'DETAIL_LIMIT', 1), \
             patch.object(asmhentai.requests, 'Session'), \
             patch.object(asmhentai, '_search', return_value=rows), \
             patch.object(asmhentai, '_get', return_value={}) as get:
            _, state, status = asmhentai.collect({'detail_cursor': 1})

        get.assert_called_once_with(unittest.mock.ANY, '456')
        self.assertEqual(status['detail_candidates'], 2)
        self.assertEqual(status['detail_attempts'], 1)
        self.assertEqual(state['detail_cursor'], 0)


if __name__ == '__main__':
    unittest.main()
