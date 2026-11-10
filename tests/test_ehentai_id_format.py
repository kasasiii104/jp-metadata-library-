import unittest
from unittest.mock import patch

from sources import ehentai as eh


class GalleryIdFormatTests(unittest.TestCase):
    invalid_gids = ('１２３', '١٢٣', '123x', '')
    invalid_tokens = ('ghijklmnop', 'abcdef123', 'abcdef12345',
                      'abcdef1234x', 'abcdef1234/', '')

    def test_html_rejects_invalid_gid_and_token(self):
        for gid in self.invalid_gids:
            with self.subTest(gid=gid):
                self.assertEqual(eh._refs_from_html(f'/g/{gid}/abcdef1234/'), [])
        for token in self.invalid_tokens:
            if token.endswith('/'):
                continue  # A slash is a valid HTML URL boundary.
            with self.subTest(token=token):
                self.assertEqual(eh._refs_from_html(f'/g/123/{token}/'), [])

    def test_html_accepts_valid_token_and_requires_boundary(self):
        for suffix in ('/', '', '?x=1'):
            with self.subTest(suffix=suffix):
                self.assertEqual(eh._refs_from_html(f'/g/123/ABCDEF1234{suffix}'),
                                 [(123, 'abcdef1234')])

    def test_html_extracts_quoted_link_without_trailing_slash(self):
        self.assertEqual(eh._refs_from_html('<a href="/g/1/abcdef1234">link</a>'),
                         [(1, 'abcdef1234')])
        self.assertEqual(eh._refs_from_html('<a href="/g/1/abcdef12345">link</a>'), [])

    def test_normalize_rejects_invalid_gid_and_token(self):
        for field, values in (('gid', self.invalid_gids),
                              ('token', self.invalid_tokens)):
            for value in values:
                with self.subTest(field=field, value=value):
                    row = {'gid': 123, 'token': 'abcdef1234', field: value}
                    with self.assertRaises(ValueError):
                        eh._normalize(row)

    def test_normalize_accepts_valid_token_and_current_key(self):
        for field in ('token', 'current_key'):
            for token in ('abcdef1234', 'ABCDEF1234'):
                with self.subTest(field=field, token=token):
                    item = eh._normalize({'gid': 123, field: token})
                    self.assertEqual(item['source_id'], '123')
                    self.assertEqual(item['source_token'], token)

    def test_invalid_metadata_is_queued_without_losing_valid_item(self):
        for token in self.invalid_tokens[:-1]:
            with self.subTest(token=token), \
                 patch('requests.sessions.Session.request',
                       side_effect=AssertionError('Network forbidden')), \
                 patch.object(eh, 'EH_LATEST_PAGES', 1), \
                 patch.object(eh, 'EH_BACKFILL_PAGES', 0), \
                 patch.object(eh, '_discover', return_value=(
                     [(1, 'abcdef1234'), (2, 'abcdef1234')], '', {})), \
                 patch.object(eh, 'safe_post_json', return_value={'gmetadata': [
                     {'gid': 1, 'token': 'abcdef1234', 'tags': ['language:japanese']},
                     {'gid': 2, 'token': token}]}):
                items, state, status = eh.collect()
            self.assertEqual([item['source_id'] for item in items], ['1'])
            self.assertEqual(set(state['retry_refs']), {'2'})
            self.assertTrue(state['retry_refs']['2']['last_error'])
            self.assertTrue(status['partial'])

    def test_unicode_gid_response_is_an_individual_failure(self):
        for gid in ('２', '٢'):
            with self.subTest(gid=gid), patch.object(
                    eh, 'safe_post_json', return_value={'gmetadata': [
                        {'gid': 1}, {'gid': gid}]}):
                failures = {}
                items = eh._metadata(None, [(1, 'abcdef1234'), (2, 'abcdef1234')],
                                     failures)
            self.assertEqual([item['source_id'] for item in items], ['1'])
            self.assertEqual(set(failures), {'2'})
