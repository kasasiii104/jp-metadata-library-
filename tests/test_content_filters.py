import contextlib
import copy
import io
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import crawler as c
from sources import hitomi


class ContentFilterTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for name, value in {
            'DOCS_DIR': self.root / 'docs', 'DATA_FILE': self.root / 'docs/data.json',
            'STATE_FILE': self.root / 'docs/crawl_state.json',
            'STATUS_FILE': self.root / 'docs/source_status.json',
            'FILTER_STATE_FILE': self.root / 'state/filter_audit.json',
        }.items():
            self.enterContext(patch.object(c, name, value))
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.enterContext(patch.object(c, 'now_iso', return_value='2026-09-29T11:00:00+00:00'))
        self.enterContext(patch.object(c, 'HITOMI_FILTER_AUDIT_LIMIT', 0))
        self.enterContext(patch.object(c, 'HITOMI_FILTER_AUDIT_DELAY_SEC', 0))
        self.enterContext(patch.object(c.hentai3, 'enrich_existing_for_filter', return_value={}))
        self.enterContext(patch.object(hitomi.requests.Session, 'get', side_effect=AssertionError('Network forbidden')))
        self.enterContext(patch.dict(c.SOURCES, {}, clear=True))

    def item(self, num='9000001', **extra):
        return {'uid': 'hitomi:' + num, 'source_id': num, 'source': 'hitomi',
                'title': '中立なサンプル作品', 'language': 'japanese', 'category': 'test',
                'tags': ['sample'], 'first_seen': '2026-01-01T00:00:00+00:00',
                'thumbnail': 'thumbs/hitomi/' + num + '.webp', **extra}

    def fresh(self, num='9000001', **extra):
        return self.item(num, filter_metadata_checked=hitomi.FILTER_METADATA_VERSION,
                         filter_metadata_checked_at=c.now_iso(), **extra)

    def collect(self, rows):
        c.SOURCES['hitomi'] = lambda state: (rows, state, {'status': 'ok'})

    def save_items(self, items):
        c.save_json(c.DATA_FILE, {'items': items})

    def saved(self):
        return c.load_json(c.DATA_FILE, {})['items']

    def state(self):
        return c.load_json(c.FILTER_STATE_FILE, {})

    def test_existing_requested_terms_and_namespace_normalization(self):
        for tag in ('female:scat', 'male:MINIGUY', 'other:vore', 'blood', 'male:male',
                    'male:boys_love', 'male:boys’ love', 'ｆｅｍａｌｅ：ｆａｒｔｉｎｇ'):
            with self.subTest(tag=tag):
                self.assertTrue(c.blocked_reason(self.item(tags=[tag])))

    def test_japanese_and_english_aliases_apply_to_tags_and_titles(self):
        aliases = {'スカトロ': 'scat', 'おなら': 'farting', 'オナラ': 'farting',
                   'リョナ': 'ryona', '拷問': 'torture', '丸呑み': 'vore', '捕食': 'vore',
                   'ボーイズラブ': 'yaoi', 'ミニガイ': 'miniguy', '流血': 'blood', 'gore': 'guro'}
        for alias, canonical in aliases.items():
            with self.subTest(alias=alias):
                self.assertIn(canonical, c.blocked_reason(self.item(tags=['female:' + alias])))
                self.assertIn(canonical, c.blocked_reason(self.item(title='中立テスト【' + alias + '】')))

    def test_short_terms_do_not_match_benign_words_or_creator_fields(self):
        for title in ('Blue sky', 'Scattered samples', 'Bloodless example', 'グローバルな視点', 'マグロの図鑑'):
            with self.subTest(title=title):
                self.assertEqual(c.blocked_reason(self.item(title=title, artists=['blood'], groups=['guro'])), '')
        self.assertIn('guro', c.blocked_reason(self.item(title='中立テスト【グロ注意】')))

    def test_normalizer_only_marks_real_nonempty_tag_metadata_verified(self):
        for raw_tags in (None, [], {}, 'sample', [{'name': ''}]):
            with self.subTest(tags=raw_tags):
                value = hitomi._normalize(9000001, {'title': 'サンプル', 'language': 'japanese', 'tags': raw_tags}, '')
                self.assertTrue(c.unverified_hitomi(value, fresh=True))
                self.assertEqual(value['filter_metadata_checked'], '')
        value = hitomi._normalize(9000001, {'title': 'サンプル', 'language': 'japanese', 'tags': [{'tag': 'sample'}]}, '')
        self.assertFalse(c.unverified_hitomi(value, fresh=True))

    def test_tagless_new_work_is_held_not_published(self):
        self.collect([self.item(tags=[])])
        c.main()
        self.assertEqual(self.saved(), [])
        self.assertEqual(self.state()['records']['hitomi:9000001']['status'], 'pending')

    def test_missing_identity_fields_are_pending_not_permanently_blocked(self):
        self.collect([self.item(language='', title='', tags=[])])
        c.main()
        self.assertEqual(self.state()['records']['hitomi:9000001']['status'], 'pending')

    def test_existing_catalog_is_fully_rechecked_without_remote_requests(self):
        items = [self.item(tags=['blood']), self.item('9000002', title='サンプル【丸呑み】'),
                 self.item('9000003', tags=[]), self.item('9000004')]
        self.save_items(items)
        c.main(audit_only=True)
        self.assertEqual([x['uid'] for x in self.saved()], ['hitomi:9000004'])
        records = self.state()['records']
        self.assertEqual(records['hitomi:9000001']['status'], 'blocked')
        self.assertEqual(records['hitomi:9000003']['status'], 'pending')
        self.assertFalse(str(c.FILTER_STATE_FILE).startswith(str(c.DOCS_DIR)))
        self.assertEqual(records['hitomi:9000003']['item']['thumbnail'], items[2]['thumbnail'])

    def test_newly_discovered_blocked_tag_removes_older_visible_row(self):
        self.save_items([self.item()])
        self.collect([self.fresh(tags=['male:scat'])])
        c.main()
        self.assertEqual(self.saved(), [])
        record = self.state()['records']['hitomi:9000001']
        self.assertEqual(record['reason'], 'blocked:male:scat')
        self.assertEqual(record['item']['tags'], ['male:scat'])

    def test_confirmed_exclusion_cannot_resurface_with_incomplete_or_changed_tags(self):
        self.save_items([self.item(tags=['blood'])])
        c.main(audit_only=True)
        evidence = copy.deepcopy(self.state()['records']['hitomi:9000001'])
        for later in (self.item(tags=[]), self.fresh(tags=['sample'])):
            self.collect([later])
            c.main()
            self.assertEqual(self.saved(), [])
            self.assertEqual(self.state()['records']['hitomi:9000001'], evidence)

    def test_audit_verifies_and_restores_pending_work_preserving_metadata(self):
        original = self.item(tags=[])
        self.save_items([original])
        c.main(audit_only=True)
        with patch.object(c, 'HITOMI_FILTER_AUDIT_LIMIT', 1), \
             patch.object(hitomi, 'fetch_filter_metadata', return_value=self.fresh(thumbnail='')):
            c.main(audit_only=True)
        saved = self.saved()[0]
        self.assertEqual(saved['thumbnail'], original['thumbnail'])
        self.assertEqual(saved['first_seen'], original['first_seen'])
        self.assertEqual(saved['filter_metadata_checked'], hitomi.FILTER_METADATA_VERSION)
        self.assertNotIn(original['uid'], self.state()['records'])
        self.assertEqual(self.state()['last_audit']['restored'], 1)

    def test_audit_discovers_blocked_metadata_and_removes_visible_row(self):
        self.save_items([self.item()])
        with patch.object(c, 'HITOMI_FILTER_AUDIT_LIMIT', 1), \
             patch.object(hitomi, 'fetch_filter_metadata', return_value=self.fresh(tags=['farting'])):
            c.main(audit_only=True)
        self.assertEqual(self.saved(), [])
        self.assertEqual(self.state()['last_audit']['blocked'], 1)

    def test_audit_prioritizes_pending_and_respects_per_work_retry(self):
        self.save_items([self.item(), self.item('9000002', tags=[])])
        with patch.object(c, 'HITOMI_FILTER_AUDIT_LIMIT', 1), \
             patch.object(hitomi, 'fetch_filter_metadata', side_effect=TimeoutError) as fetch:
            c.main(audit_only=True)
            self.assertEqual(fetch.call_args.args[1], 9000002)
            c.main(audit_only=True)
            self.assertEqual(fetch.call_args.args[1], 9000001)
            c.main(audit_only=True)
            self.assertEqual(fetch.call_count, 2)
        self.assertEqual(len(self.saved()), 1)
        self.assertEqual(self.state()['records']['hitomi:9000002']['status'], 'pending')

    def test_access_denial_stops_audit_and_persists_host_cooldown(self):
        self.save_items([self.item(str(num)) for num in range(9000001, 9000010)])
        with patch.object(c, 'HITOMI_FILTER_AUDIT_LIMIT', 20), \
             patch.object(hitomi, 'fetch_filter_metadata', side_effect=RuntimeError('HTTP 403')) as fetch:
            c.main(audit_only=True)
            c.main(audit_only=True)
            self.assertEqual(fetch.call_count, 1)
        self.assertTrue(self.state()['last_audit']['stopped'])
        self.assertEqual(len(self.saved()), 9)

    def test_three_network_failures_stop_without_erasing_saved_records(self):
        self.save_items([self.item(str(num)) for num in range(9000001, 9000010)])
        with patch.object(c, 'HITOMI_FILTER_AUDIT_LIMIT', 20), \
             patch.object(hitomi, 'fetch_filter_metadata', side_effect=TimeoutError) as fetch:
            c.main(audit_only=True)
            self.assertEqual(fetch.call_count, 3)
        self.assertEqual(len(self.saved()), 9)

    def test_verified_rows_do_not_repeat_audit_until_due(self):
        self.save_items([self.fresh()])
        with patch.object(c, 'HITOMI_FILTER_AUDIT_LIMIT', 1), \
             patch.object(hitomi, 'fetch_filter_metadata') as fetch:
            c.main(audit_only=True)
        fetch.assert_not_called()

    def test_restoration_checkpoint_survives_interrupted_catalog_write(self):
        self.save_items([self.item(tags=[])])
        c.main(audit_only=True)
        save = c.save_json
        def interrupted(path, payload):
            if path == c.DATA_FILE:
                raise OSError('simulated interrupted write')
            save(path, payload)
        with patch.object(c, 'HITOMI_FILTER_AUDIT_LIMIT', 1), \
             patch.object(hitomi, 'fetch_filter_metadata', return_value=self.fresh()), \
             patch.object(c, 'save_json', side_effect=interrupted):
            with self.assertRaises(OSError):
                c.main(audit_only=True)
        self.assertIn('hitomi:9000001', self.state()['verified_checkpoints'])
        c.main(audit_only=True)
        self.assertEqual(len(self.saved()), 1)
        self.assertEqual(self.state()['verified_checkpoints'], {})

    def test_metadata_only_fetch_does_not_request_images_or_switch_hosts(self):
        response = Mock(text='var galleryinfo = {"id":"9000001","title":"サンプル","language":"japanese","tags":[{"tag":"sample"}]};')
        session = Mock()
        with patch.object(hitomi, 'safe_get', return_value=response) as get, \
             patch.object(hitomi, '_thumbnail') as thumbnail:
            value = hitomi.fetch_filter_metadata(session, 9000001)
        self.assertEqual(value['thumbnail'], '')
        self.assertEqual(get.call_count, 1)
        thumbnail.assert_not_called()
        with patch.object(hitomi, 'safe_get', side_effect=RuntimeError('HTTP 429')) as get:
            with self.assertRaises(RuntimeError):
                hitomi.fetch_filter_metadata(session, 9000001)
        self.assertEqual(get.call_count, 1)

    def test_wrong_gallery_identity_cannot_restore_pending_work(self):
        self.save_items([self.item(tags=[])])
        with patch.object(c, 'HITOMI_FILTER_AUDIT_LIMIT', 1), \
             patch.object(hitomi, 'fetch_filter_metadata', return_value=self.fresh('9000002')):
            c.main(audit_only=True)
        self.assertEqual(self.saved(), [])
        self.assertEqual(self.state()['last_audit']['failed'], 1)


if __name__ == '__main__':
    unittest.main()
