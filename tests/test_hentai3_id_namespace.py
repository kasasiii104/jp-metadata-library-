import contextlib
import copy
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import crawler as c
from sources import hentai3 as h, hentai3_ids as ids
from tools.json_storage import load_json, save_json, validate_hentai3_id_map

STAMP = '2026-11-09T00:00:00+00:00'


def row(sid, **extra):
    return dict(uid='3hentai:' + sid, source='3hentai', source_id=sid,
                title='Sample ' + sid, language='japanese', tags=['sample'],
                filter_metadata_checked='3hentai-gallery-v1', **extra)


class IdentityTests(unittest.TestCase):
    def test_probe_detail_public_id_registers_and_folds(self):
        for identity_key in ('uid', 'source_id'):
            with self.subTest(identity_key=identity_key):
                base = h._normalize({'id': 999, 'title': 'Sample'})
                detail = h._normalize({'id': 999, 'title': 'Sample',
                                       'url': 'https://3hentai.net/d/123'})
                if identity_key == 'source_id':
                    base.pop('uid')
                    detail.pop('uid')
                self.assertTrue(h._merge_probe_detail(base, detail))
                self.assertEqual(base['public_gallery_id'], '123')
                base['uid'] = '3hentai:999'
                mapping = ids.empty_id_map()
                catalog = {'3hentai:g123': row('g123', public_gallery_id='123')}
                stats = ids.migration_stats()
                incoming = ids.normalize_incoming(base, catalog, {}, mapping,
                                                  STAMP, c.merge_item, stats)
                self.assertEqual(incoming['uid'], '3hentai:999')
                self.assertEqual(mapping['public_to_internal'], {'123': '3hentai:999'})
                self.assertNotIn('3hentai:g123', catalog)
                self.assertIn('3hentai:999', catalog)
                self.assertEqual(stats['total'], 1)

    def test_probe_detail_different_internal_id_does_not_inherit_public_id(self):
        base = row('999')
        h._merge_probe_detail(base, row('888', public_gallery_id='123'))
        self.assertFalse(base.get('public_gallery_id'))

    def test_probe_detail_preserves_existing_public_id(self):
        base = row('999', public_gallery_id='456')
        h._merge_probe_detail(base, row('999', public_gallery_id='123'))
        self.assertEqual(base['public_gallery_id'], '456')

    def test_normalize_internal_and_public_namespaces(self):
        for raw, uid in [({'id': 123}, '3hentai:123'), ({}, '3hentai:g123')]:
            item = h._normalize(dict(raw, title='Sample', url='https://3hentai.net/d/123'))
            self.assertEqual(item['uid'], uid)
            self.assertEqual(item['source_id'], uid.split(':')[1])
            self.assertEqual(item['public_gallery_id'], '123')
            self.assertEqual(item['source_url_kind'], 'direct')

    def test_migration_all_stores_blocked_priority_and_idempotence(self):
        mapping = ids.empty_id_map()
        mapping['public_to_internal']['123'] = '3hentai:999'
        old = row('123', source_url='https://3hentai.net/d/123', first_seen='2020')
        target = row('999', first_seen='2021')
        store = {'records': {
            old['uid']: {'status': 'blocked', 'reason': 'blocked:evidence', 'item': old},
            target['uid']: {'status': 'pending', 'reason': 'metadata_unverified', 'item': target}},
            'verified_checkpoints': {old['uid']: old},
            'hentai3_attempts': {old['uid']: {'last_attempt': 1, 'failures': 7},
                                target['uid']: {'last_attempt': 2, 'failures': 2}}}
        items, stats = ids.migrate([target, old], store, mapping, STAMP, c.merge_item)
        self.assertEqual(stats['total'], 4)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['first_seen'], '2020')
        self.assertEqual(store['records'][target['uid']]['status'], 'blocked')
        self.assertEqual(store['records'][target['uid']]['reason'], 'blocked:evidence')
        self.assertEqual(store['records'][target['uid']]['item']['uid'], target['uid'])
        self.assertEqual(store['hentai3_attempts'][target['uid']]['failures'], 7)
        self.assertEqual({x['store'] for x in mapping['migrations']},
                         {'catalog', 'records', 'verified_checkpoints', 'hentai3_attempts'})
        for entry in mapping['migrations']:
            self.assertEqual((entry['old_uid'], entry['new_uid']), ('3hentai:123', '3hentai:999'))
        before = copy.deepcopy(mapping)
        _, stats = ids.migrate(items, store, mapping, STAMP, c.merge_item)
        self.assertEqual(stats['total'], 0)
        self.assertEqual(mapping, before)

    def test_overlapping_migration_sources_remain_distinct_and_idempotent(self):
        for order in (('123', '999'), ('999', '123')):
            with self.subTest(order=order):
                mapping = ids.empty_id_map()
                mapping['public_to_internal'] = {'123': '3hentai:999', '999': '3hentai:888'}
                old = [row(sid, source_url='https://3hentai.net/d/' + sid,
                           first_seen='2020' if sid == '123' else '2021') for sid in order]
                store = {
                    'records': {x['uid']: {'status': 'blocked' if x['source_id'] == '123'
                                          else 'pending', 'item': x} for x in old},
                    'verified_checkpoints': {x['uid']: x for x in old},
                    'hentai3_attempts': {x['uid']: {'failures': int(x['source_id'])}
                                        for x in old}}
                items, stats = ids.migrate(old, store, mapping, STAMP, c.merge_item)
                expected = {'3hentai:999': 'Sample 123', '3hentai:888': 'Sample 999'}
                self.assertEqual({x['uid']: x['title'] for x in items}, expected)
                self.assertEqual({uid: rec['item']['title']
                                  for uid, rec in store['records'].items()}, expected)
                self.assertEqual({uid: x['title'] for uid, x in
                                  store['verified_checkpoints'].items()}, expected)
                self.assertEqual(store['records']['3hentai:888']['status'], 'pending')
                self.assertEqual(store['records']['3hentai:999']['status'], 'blocked')
                self.assertEqual(store['hentai3_attempts'], {
                    '3hentai:999': {'failures': 123, 'id_gen': 2},
                    '3hentai:888': {'failures': 999, 'id_gen': 2}})
                self.assertEqual(stats['total'], 8)
                for name in ('catalog', 'records', 'verified_checkpoints', 'hentai3_attempts'):
                    self.assertEqual([(x['old_uid'], x['new_uid']) for x in
                                      mapping['migrations'] if x['store'] == name],
                                     [('3hentai:' + sid, mapping['public_to_internal'][sid])
                                      for sid in order])
                before = copy.deepcopy((items, store, mapping))
                items, stats = ids.migrate(items, store, mapping, STAMP, c.merge_item)
                self.assertEqual(stats['total'], 0)
                self.assertEqual((items, store, mapping), before)

    def test_restart_with_legacy_catalog_preserves_migrated_stores(self):
        for order, marked in ((order, marked) for order in
                              (('123', '999'), ('999', '123')) for marked in (False, True)):
            with self.subTest(order=order, marked=marked):
                mapping = ids.empty_id_map()
                mapping['public_to_internal'] = {'123': '3hentai:999', '999': '3hentai:888'}
                old = [row(sid, source_url='https://3hentai.net/d/' + sid) for sid in order]
                store = {'records': {}, 'verified_checkpoints': {}, 'hentai3_attempts': {}}
                # Simulate the persisted stores from a partial migration before restart.
                for item in old:
                    uid = mapping['public_to_internal'][item['source_id']]
                    migrated = dict(item, uid=uid, source_id=uid.split(':')[1],
                                    public_gallery_id=item['source_id'])
                    store['records'][uid] = {
                        'status': 'blocked' if item['source_id'] == '123' else 'pending',
                        'reason': 'evidence:' + item['source_id'], 'item': migrated}
                    store['verified_checkpoints'][uid] = copy.deepcopy(migrated)
                    store['hentai3_attempts'][uid] = {
                        'failures': int(item['source_id']),
                        'last_attempt': 20 if item['source_id'] == '123' else 10}
                    if marked:
                        store['hentai3_attempts'][uid]['id_gen'] = 2
                with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as directory:
                    path = Path(directory) / 'filter.json'
                    save_json(path, store)
                    store = load_json(path, {})
                before = copy.deepcopy(store)
                for attempt in before['hentai3_attempts'].values():
                    attempt['id_gen'] = 2
                items, stats = ids.migrate(old, store, mapping, STAMP, c.merge_item)
                self.assertEqual(store, before)
                self.assertEqual({x['uid']: x['title'] for x in items},
                                 {'3hentai:999': 'Sample 123', '3hentai:888': 'Sample 999'})
                self.assertEqual(stats['by_store'], {'catalog': 2})
                snapshot = copy.deepcopy((items, store, mapping))
                items, stats = ids.migrate(items, store, mapping, STAMP, c.merge_item)
                self.assertEqual(stats['total'], 0)
                self.assertEqual((items, store, mapping), snapshot)

    def test_attempts_only_unmarked_records_move_and_marking_is_not_migration(self):
        for marked in (False, True):
            with self.subTest(marked=marked):
                mapping = ids.empty_id_map()
                mapping['public_to_internal']['123'] = '3hentai:999'
                old = row('123', source_url='https://3hentai.net/d/123')
                attempt = {'failures': 7, 'last_attempt': 1}
                if marked:
                    attempt['id_gen'] = 2
                store = {'hentai3_attempts': {'3hentai:123': attempt,
                                            '3hentai:456': {'failures': 1}}}
                items, stats = ids.migrate([old], store, mapping, STAMP, c.merge_item)
                expected_uid = '3hentai:123' if marked else '3hentai:999'
                self.assertEqual(store['hentai3_attempts'], {
                    expected_uid: {'failures': 7, 'last_attempt': 1, 'id_gen': 2},
                    '3hentai:456': {'failures': 1, 'id_gen': 2}})
                self.assertEqual(stats['total'], 1 if marked else 2)
                self.assertEqual([x['store'] for x in mapping['migrations']],
                                 ['catalog'] if marked else ['catalog', 'hentai3_attempts'])
                _, stats = ids.migrate(items, store, mapping, STAMP, c.merge_item)
                self.assertEqual(stats['total'], 0)

    def test_unmapped_legacy_and_pending_migrate_to_public(self):
        old = row('123', source_url='https://3hentai.net/d/123')
        store = {'records': {old['uid']: {'status': 'pending', 'item': old}}}
        items, stats = ids.migrate([old], store, ids.empty_id_map(), STAMP, c.merge_item)
        self.assertEqual(items[0]['uid'], '3hentai:g123')
        self.assertEqual(items[0]['source_id'], 'g123')
        self.assertEqual(items[0]['public_gallery_id'], '123')
        self.assertIn('3hentai:g123', store['records'])
        self.assertEqual(stats['total'], 2)

    def test_fold_pending_record_preserves_identity_and_blocked_evidence(self):
        for status in ('pending', 'blocked'):
            with self.subTest(status=status):
                mapping = ids.empty_id_map()
                public = row('g123', public_gallery_id='123')
                store = {'records': {public['uid']: {
                    'status': status, 'reason': 'evidence', 'item': public}}}
                stats = ids.migration_stats()
                ids.normalize_incoming(row('999', public_gallery_id='123'), {}, store,
                                       mapping, STAMP, c.merge_item, stats)
                self.assertEqual(store['records']['3hentai:999']['status'], status)
                self.assertEqual(store['records']['3hentai:999']['reason'], 'evidence')
                self.assertEqual(store['records']['3hentai:999']['item']['uid'], '3hentai:999')
                self.assertEqual(mapping['migrations'][0]['reason'], 'fold')

    def test_conflicting_pair_does_not_fold_or_overwrite(self):
        mapping = ids.empty_id_map()
        mapping['public_to_internal']['123'] = '3hentai:999'
        catalog = {'3hentai:g123': row('g123', public_gallery_id='123')}
        stats = ids.migration_stats()
        incoming = ids.normalize_incoming(row('888', public_gallery_id='123'), catalog, {},
                                          mapping, STAMP, c.merge_item, stats)
        self.assertEqual(incoming['uid'], '3hentai:888')
        self.assertIn('3hentai:g123', catalog)
        self.assertEqual(mapping['public_to_internal']['123'], '3hentai:999')
        self.assertEqual(stats['conflicts'], 1)
        self.assertEqual(mapping['migrations'], [])

    def test_ambiguous_title_cards_are_not_enriched(self):
        cards = [{'title': 'Sample', 'gallery_id': str(i),
                  'source_url': 'https://3hentai.net/d/' + str(i)} for i in (123, 456)]
        with contextlib.ExitStack() as stack:
            for name, value in {'HENTAI3_LATEST_PAGES': 1, 'HENTAI3_BACKFILL_PAGES': 0,
                                'HENTAI3_GET_DEBUG_LIMIT': 0, 'HENTAI3_GALLERY_METADATA_LIMIT': 0}.items():
                stack.enter_context(patch.object(h, name, value))
            stack.enter_context(patch.object(h.requests, 'Session', return_value=Mock()))
            stack.enter_context(patch.object(h, '_direct_search_cards', return_value=(cards, {})))
            stack.enter_context(patch.object(h, '_get_json', return_value=([{'id': 999, 'title': 'Sample'}], 'mock')))
            stack.enter_context(patch.object(h, '_find_candidate_dicts', side_effect=lambda x: x))
            stack.enter_context(patch.object(h.time, 'sleep'))
            items, _, _ = h.collect({})
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['uid'], '3hentai:999')
        self.assertEqual(items[0]['source_url_kind'], 'search')
        self.assertFalse(items[0].get('resolved_gallery_id'))
        self.assertFalse(items[0].get('public_gallery_id'))


class MainIdentityTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])))
        for key, value in {'DOCS_DIR': self.root, 'DATA_FILE': self.root / 'data.json',
                           'STATE_FILE': self.root / 'state.json', 'STATUS_FILE': self.root / 'status.json',
                           'FILTER_STATE_FILE': self.root / 'filter.json',
                           'HENTAI3_ID_MAP_FILE': self.root / 'map.json'}.items():
            self.enterContext(patch.object(c, key, value))
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.enterContext(patch.object(c, 'HITOMI_FILTER_AUDIT_LIMIT', 0))
        self.enterContext(patch.object(h, 'enrich_existing_for_filter', return_value={}))
        self.enterContext(patch.object(h.requests.Session, 'request', side_effect=AssertionError('Network forbidden')))
        self.incoming = []
        self.enterContext(patch.dict(c.SOURCES, {'3hentai': lambda state: (self.incoming, state, {'status': 'ok'})}, clear=True))

    def run_main(self, items):
        save_json(c.DATA_FILE, {'items': items})
        self.assertEqual(c.main(), 0)
        return load_json(c.DATA_FILE, {})['items']

    def test_records_migration_log_survives_checkpoint_and_failed_collectors(self):
        old = row('123', source_url='https://3hentai.net/d/123')
        save_json(c.FILTER_STATE_FILE, {'records': {old['uid']: {
            'status': 'blocked', 'reason': 'blocked:evidence', 'item': old}}})
        hitomi_rows = [dict(uid=f'hitomi:{n}', source='hitomi', source_id=str(n),
                            title=f'Sample {n}', language='japanese', tags=['sample'])
                       for n in range(1000, 1025)]
        save_json(c.DATA_FILE, {'items': hitomi_rows})
        def fetch(session, number, base):
            return dict(hitomi_rows[number - 1000],
                        filter_metadata_checked=c.hitomi.FILTER_METADATA_VERSION)
        with patch.object(c, 'HITOMI_FILTER_AUDIT_LIMIT', 25), \
             patch.object(c.hitomi, 'fetch_filter_metadata', side_effect=fetch) as audit, \
             patch.object(c, 'polite_sleep'), \
             patch.dict(c.SOURCES, {'3hentai': lambda state: ([], state, {'status': 'error'})}, clear=True):
            self.assertEqual(c.main(), 1)
        self.assertEqual(audit.call_count, 25)
        self.assertIn('3hentai:g123', load_json(c.FILTER_STATE_FILE, {})['records'])
        log = load_json(c.HENTAI3_ID_MAP_FILE, {})['migrations']
        self.assertEqual([(x['old_uid'], x['new_uid'], x['store']) for x in log],
                         [('3hentai:123', '3hentai:g123', 'records')])
        self.assertEqual(c.main(), 0)
        self.assertEqual(load_json(c.HENTAI3_ID_MAP_FILE, {})['migrations'], log)

    def test_map_only_saved_repeated_migration_does_not_duplicate_log(self):
        old = row('123', source_url='https://3hentai.net/d/123')
        save_json(c.FILTER_STATE_FILE, {'records': {old['uid']: {
            'status': 'blocked', 'reason': 'blocked:evidence', 'item': old}}})
        original = c.FILTER_STATE_FILE.read_bytes()
        with patch.dict(c.SOURCES, {'3hentai': lambda state: ([], state, {'status': 'error'})}, clear=True):
            self.assertEqual(c.main(), 1)
            log = load_json(c.HENTAI3_ID_MAP_FILE, {})['migrations']
            self.assertEqual(len(log), 1)
            self.assertEqual(c.FILTER_STATE_FILE.read_bytes(), original)
            self.assertEqual(c.main(), 1)
            self.assertEqual(load_json(c.HENTAI3_ID_MAP_FILE, {})['migrations'], log)
        self.assertEqual(c.main(), 0)
        self.assertEqual(load_json(c.HENTAI3_ID_MAP_FILE, {})['migrations'], log)

    def test_equal_numbers_remain_distinct_in_main(self):
        self.incoming = [h._normalize({'title': 'Public', 'url': 'https://3hentai.net/d/123', 'language': 'japanese'})]
        self.incoming[0].update(filter_metadata_checked='3hentai-gallery-v1', tags=['sample'])
        items = self.run_main([row('123', source_url='https://3hentai.net/search?q=internal')])
        self.assertEqual({x['uid'] for x in items}, {'3hentai:123', '3hentai:g123'})
        self.assertEqual({x['title'] for x in items}, {'Sample 123', 'Public'})

    def test_incoming_internal_folds_public_catalog(self):
        public = row('g123', public_gallery_id='123', first_seen='2020')
        self.incoming = [row('999', public_gallery_id='123')]
        items = self.run_main([public])
        self.assertEqual([x['uid'] for x in items], ['3hentai:999'])
        self.assertEqual(items[0]['first_seen'], '2020')
        mapping = load_json(c.HENTAI3_ID_MAP_FILE, {}, validate_hentai3_id_map)
        self.assertEqual(mapping['public_to_internal'], {'123': '3hentai:999'})
        self.assertEqual({x['store'] for x in mapping['migrations']}, {'catalog'})
        self.assertTrue(all(x['reason'] == 'fold' for x in mapping['migrations']))

    def test_mapped_incoming_public_uses_internal(self):
        mapping = ids.empty_id_map()
        mapping['public_to_internal']['123'] = '3hentai:999'
        save_json(c.HENTAI3_ID_MAP_FILE, mapping)
        self.incoming = [row('g123', public_gallery_id='123')]
        items = self.run_main([row('999')])
        self.assertEqual([x['uid'] for x in items], ['3hentai:999'])

    def test_title_resolution_is_never_identity_evidence(self):
        self.incoming = [row('999', resolved_gallery_id='123')]
        items = self.run_main([row('888', resolved_gallery_id='123')])
        self.assertEqual({x['uid'] for x in items}, {'3hentai:888', '3hentai:999'})
        self.assertEqual(load_json(c.HENTAI3_ID_MAP_FILE, {})['public_to_internal'], {})

    def test_main_migrates_before_audit_and_reports_idempotent_stats(self):
        mapping = ids.empty_id_map()
        mapping['public_to_internal']['123'] = '3hentai:999'
        save_json(c.HENTAI3_ID_MAP_FILE, mapping)
        old = row('123', source_url='https://3hentai.net/d/123', first_seen='2020')
        blocked = row('456', source_url='https://3hentai.net/d/456')
        save_json(c.FILTER_STATE_FILE, {'records': {blocked['uid']: {
            'status': 'blocked', 'reason': 'blocked:evidence', 'item': blocked}},
            'hentai3_attempts': {blocked['uid']: {'last_attempt': 1, 'failures': 2}}})
        items = self.run_main([old, row('999', first_seen='2021')])
        self.assertEqual([x['uid'] for x in items], ['3hentai:999'])
        self.assertEqual(items[0]['first_seen'], '2020')
        store = load_json(c.FILTER_STATE_FILE, {})
        self.assertEqual(store['records']['3hentai:g456']['status'], 'blocked')
        self.assertEqual(store['records']['3hentai:g456']['item']['source_id'], 'g456')
        self.assertIn('3hentai:g456', store['hentai3_attempts'])
        status = load_json(c.STATUS_FILE, {})
        self.assertEqual(status['3hentai']['id_migration']['total'], 3)
        self.assertEqual(status['filters']['id_migration'], status['3hentai']['id_migration'])
        log = load_json(c.HENTAI3_ID_MAP_FILE, {})['migrations']
        self.assertEqual(c.main(), 0)
        self.assertEqual(load_json(c.STATUS_FILE, {})['3hentai']['id_migration']['total'], 0)
        self.assertEqual(load_json(c.HENTAI3_ID_MAP_FILE, {})['migrations'], log)

    def test_map_missing_defaults_and_corruption_fails_loud(self):
        self.assertEqual(load_json(c.HENTAI3_ID_MAP_FILE, ids.empty_id_map(), validate_hentai3_id_map), ids.empty_id_map())
        for content in ('{broken', '{}', '{"version": 1, "updated_at": "", "namespaces": {}, "public_to_internal": [], "migrations": []}'):
            c.HENTAI3_ID_MAP_FILE.write_text(content)
            with self.subTest(content=content), self.assertRaises(ValueError):
                c.main()
            self.assertEqual(c.HENTAI3_ID_MAP_FILE.read_text(), content)

    def test_all_sources_failed_does_not_save_map(self):
        mapping = ids.empty_id_map()
        save_json(c.HENTAI3_ID_MAP_FILE, mapping)
        with patch.dict(c.SOURCES, {'3hentai': lambda state: ([], state, {'status': 'error'})}, clear=True):
            self.assertEqual(c.main(), 1)
        self.assertEqual(load_json(c.HENTAI3_ID_MAP_FILE, {}), mapping)
