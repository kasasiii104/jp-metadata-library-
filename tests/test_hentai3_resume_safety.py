"""In-memory regressions for legacy holds, identity folds and partial saves."""
import copy
import contextlib
import io
import unittest
from unittest.mock import patch

import crawler as c
from sources import hentai3 as h, hentai3_ids as ids

STAMP = '2026-11-13T00:00:00+00:00'
DIRECT = 'https://3hentai.net/d/123'


def row(sid='3000123', url=DIRECT, checked=False):
    item = dict(uid='3hentai:' + sid, source='3hentai', source_id=sid,
                title='Sample', language='japanese', tags=['sample'], source_url=url)
    if checked:
        item['filter_metadata_checked'] = '3hentai-gallery-v1'
    return item


def pending(item, **kw):
    return dict(item=copy.deepcopy(item), status='pending', reason='metadata_unverified', **kw)


class ResumeSafetyTests(unittest.TestCase):
    def setUp(self):
        self.disk = {}
        self.writes = []
        self.incoming = []
        self.status = 'ok'
        self.enterContext(patch.object(c, 'load_json', side_effect=lambda path, default, *a:
                                      copy.deepcopy(self.disk.get(path, default))))
        def save(path, value):
            self.disk[path] = copy.deepcopy(value)
            self.writes.append(path)
        self.enterContext(patch.object(c, 'save_json', side_effect=save))
        self.enterContext(patch.object(c, 'now_iso', return_value=STAMP))
        self.enterContext(patch.object(c, 'HITOMI_FILTER_AUDIT_LIMIT', 0))
        self.enterContext(patch.object(h, 'enrich_existing_for_filter', return_value={}))
        self.enterContext(patch.object(h.requests.Session, 'request', side_effect=AssertionError('No network')))
        self.enterContext(patch.object(c, 'polite_sleep'))
        self.enterContext(patch.dict(c.SOURCES, {'3hentai': lambda state:
                          (self.incoming, state, {'status': self.status})}, clear=True))
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.enterContext(contextlib.redirect_stderr(io.StringIO()))

    def seed(self, items, records=None, checkpoints=None, mapping=None):
        self.disk.clear()
        self.disk[c.DATA_FILE] = {'items': copy.deepcopy(items)}
        self.disk[c.FILTER_STATE_FILE] = dict(records=copy.deepcopy(records or {}),
                                            verified_checkpoints=copy.deepcopy(checkpoints or {}))
        self.disk[c.HENTAI3_ID_MAP_FILE] = copy.deepcopy(mapping or ids.empty_id_map())

    def public(self):
        return {x['uid'] for x in self.disk[c.DATA_FILE]['items']}

    def records(self):
        return self.disk[c.FILTER_STATE_FILE]['records']

    def test_r2_startup_uid_moves_with_empty_hold_two_runs(self):
        for checkpoint in (True, False):
            with self.subTest(checkpoint=checkpoint):
                old = row(url='' if checkpoint else 'https://3hentai.net/d/3000123',
                          checked=not checkpoint)
                held = pending(row(url=''), hold_source_url='',
                               hold_detail='3hentai_mislinked_split',
                               repair_original={'thumbnail': 'wrong.webp'})
                saved = row(url='https://3hentai.net/d/3000123', checked=True)
                self.seed([old], records={} if checkpoint else {old['uid']: held},
                          checkpoints={old['uid']: saved} if checkpoint else {})
                for run in range(2):
                    with self.subTest(run=run):
                        self.assertEqual(c.main(), 0)
                        self.assertEqual(self.public(), set())
                        self.assertEqual(len(self.records()), 1)
                        rec = next(iter(self.records().values()))
                        self.assertEqual(rec['status'], 'pending')
                        self.assertEqual(rec['hold_source_url'], '')
                        self.assertEqual(rec['hold_detail'], '3hentai_unaudited' if checkpoint
                                         else '3hentai_mislinked_split')
                        if not checkpoint:
                            self.assertEqual(rec['repair_original'], held['repair_original'])

    def test_r2_conflicting_store_identities_fail_closed_two_runs(self):
        old = row('777', url='https://3hentai.net/d/777', checked=True)
        different = dict(row('777', url='https://3hentai.net/d/888', checked=True),
                         public_gallery_id='888')
        held = pending(different, hold_source_url=different['source_url'],
                       hold_detail='3hentai_mislinked_split',
                       repair_original={'thumbnail': 'wrong.webp'})
        self.seed([old], records={old['uid']: held}, checkpoints={old['uid']: different})
        for run in range(2):
            with self.subTest(run=run):
                self.assertEqual(c.main(), 0)
                self.assertEqual(self.public(), set())
                self.assertEqual(self.records()[old['uid']]['repair_original'], held['repair_original'])
                self.assertEqual(self.records()[old['uid']]['hold_detail'], held['hold_detail'])
                self.assertEqual(self.records()['3hentai:g777']['status'], 'pending')

    def test_r2_fold_blocked_excludes_public_before_url_skip(self):
        old = dict(row(checked=True), repair_evidence='detail_id')
        held = dict(pending(row('g456', url='https://3hentai.net/d/456')),
                    status='blocked', reason='blocked:r18g')
        self.seed([old], records={'3hentai:g456': held})
        self.incoming = [dict(row(url='https://3hentai.net/d/456'), public_gallery_id='456')]
        self.assertEqual(c.main(), 0)
        self.assertEqual(self.public(), set())
        self.assertEqual(self.records()[old['uid']]['status'], 'blocked')
        self.assertEqual(self.records()[old['uid']]['reason'], 'blocked:r18g')

    def test_r2_final_gate_alias_holds(self):
        for relation in ('map', 'public_gallery_id', 'legacy', 'frozen_legacy'):
            for status in ('pending', 'blocked'):
                for audit_only in (False, True):
                    with self.subTest(relation=relation, status=status, audit_only=audit_only):
                        visible = row('g777', url='https://3hentai.net/d/777', checked=True)
                        held_item = row('3000777', url='')
                        mapping = ids.empty_id_map()
                        if relation == 'map':
                            mapping['public_to_internal']['777'] = held_item['uid']
                        elif relation == 'public_gallery_id':
                            held_item['public_gallery_id'] = '777'
                        else:
                            held_item = row('777', url='https://3hentai.net/d/777'
                                            if relation == 'legacy' else '')
                        rec = pending(held_item, hold_source_url='https://3hentai.net/d/777'
                                      if relation == 'frozen_legacy' else '',
                                      hold_detail='3hentai_mislinked_split')
                        rec.update(status=status, reason='blocked:r18g' if status == 'blocked'
                                   else 'metadata_unverified')
                        self.seed([visible], records={held_item['uid']: rec}, mapping=mapping)
                        self.assertEqual(c.main(audit_only=audit_only), 0)
                        self.assertEqual(self.public(), set())
                        self.assertEqual(len(self.records()), 1)
                        self.assertEqual(next(iter(self.records().values()))['reason'], rec['reason'])
                        if relation != 'legacy':
                            self.assertEqual(self.disk[c.STATUS_FILE]['3hentai']['held_identity_gate'], 1)

    def test_catalog_without_record_checkpoint_holds_original_url(self):
        for url in ('', 'https://3hentai.net/search?q=Sample', DIRECT):
            with self.subTest(url=url):
                original = row(url=url)
                saved = row(checked=True)
                self.seed([original], checkpoints={saved['uid']: saved})
                self.assertEqual(c.main(), 0)
                if url == DIRECT:
                    self.assertEqual(self.public(), {saved['uid']})
                else:
                    self.assertEqual(self.public(), set())
                    rec = self.records()[original['uid']]
                    self.assertEqual(rec['status'], 'pending')
                    self.assertEqual(rec['hold_source_url'], url)
                    self.assertFalse(c.hentai3_can_restore(saved, rec))

    def test_catalog_non_direct_overrides_old_direct_pending_hold(self):
        for url in ('', 'https://3hentai.net/search?q=Sample'):
            for explicit_hold in (False, True):
                with self.subTest(url=url, explicit_hold=explicit_hold):
                    original, saved = row(url=url), row(checked=True)
                    rec = pending(row())
                    if explicit_hold:
                        rec['hold_source_url'] = DIRECT
                    self.seed([original], records={original['uid']: rec},
                              checkpoints={saved['uid']: saved})
                    self.assertEqual(c.main(), 0)
                    self.assertEqual(self.public(), set())
                    held = self.records()[original['uid']]
                    self.assertEqual(held['status'], 'pending')
                    self.assertEqual(held['hold_source_url'], url)
                    self.assertTrue(ids.needs_repair(held))

    def test_catalog_record_direct_conflict_stays_pending(self):
        for checked in (False, True):
            for explicit_hold in (False, True):
                with self.subTest(checked=checked, explicit_hold=explicit_hold):
                    original, saved = row(checked=checked), row(checked=True)
                    rec = pending(row(url='https://3hentai.net/d/456'))
                    if explicit_hold:
                        rec['hold_source_url'] = rec['item']['source_url']
                    self.seed([original], records={original['uid']: rec},
                              checkpoints={saved['uid']: saved})
                    self.assertEqual(c.main(), 0)
                    self.assertEqual(self.public(), set())
                    held = self.records()[original['uid']]
                    self.assertEqual(held['status'], 'pending')
                    self.assertEqual(held['hold_detail'], '3hentai_url_conflict')
                    self.assertTrue(ids.needs_repair(held))

    def test_catalog_reconciliation_preserves_existing_holds_and_blocked(self):
        for status in ('pending', 'blocked'):
            for detail in ('3hentai_unaudited', '3hentai_mislinked_split'):
                with self.subTest(status=status, detail=detail):
                    original, saved = row(url=''), row(checked=True)
                    held_url = 'https://3hentai.net/search?q=Old'
                    rec = pending(row(), hold_source_url=held_url, hold_detail=detail,
                                  repair_original={'thumbnail': 'wrong.webp'})
                    rec['status'] = status
                    if status == 'blocked':
                        rec['reason'] = 'blocked:test'
                    self.seed([original], records={original['uid']: rec},
                              checkpoints={saved['uid']: saved})
                    self.assertEqual(c.main(), 0)
                    self.assertEqual(self.public(), set())
                    held = self.records()[original['uid']]
                    self.assertEqual(held['status'], status)
                    self.assertEqual(held['hold_source_url'], held_url)
                    self.assertEqual(held['hold_detail'], detail)
                    self.assertEqual(held['repair_original'], rec['repair_original'])
                    self.assertEqual(held['reason'], rec['reason'])

    def test_catalog_record_same_direct_hold_restores(self):
        for explicit_hold in (False, True):
            with self.subTest(explicit_hold=explicit_hold):
                original, saved = row(), row(checked=True)
                rec = pending(original)
                if explicit_hold:
                    rec['hold_source_url'] = DIRECT
                self.seed([original], records={original['uid']: rec},
                          checkpoints={saved['uid']: saved})
                self.assertEqual(c.main(), 0)
                self.assertEqual(self.public(), {saved['uid']})
                self.assertNotIn(saved['uid'], self.records())

    def test_checkpoint_without_catalog_or_record_recovers_new_row(self):
        saved = row(checked=True)
        self.seed([], checkpoints={saved['uid']: saved})
        self.assertEqual(c.main(), 0)
        self.assertEqual(self.public(), {saved['uid']})

    def test_checkpoint_conflicting_direct_gallery_stays_pending(self):
        for checked in (False, True):
            with self.subTest(checked=checked):
                original = row(checked=checked)
                saved = row(url='https://3hentai.net/d/456', checked=True)
                self.seed([original], checkpoints={saved['uid']: saved})
                self.assertEqual(c.main(), 0)
                self.assertEqual(self.public(), set())
                self.assertEqual(self.records()[original['uid']]['hold_source_url'], DIRECT)
                self.assertFalse(c.hentai3_can_restore(saved, self.records()[original['uid']]))

    def test_startup_migration_cannot_replace_empty_hold_with_direct_url(self):
        for target in ('3000123', 'g123'):
            with self.subTest(target=target):
                internal = row(target, url='')
                legacy = row('123')
                saved = row(target, checked=True)
                mapping = ids.empty_id_map()
                if target == '3000123':
                    mapping['public_to_internal']['123'] = internal['uid']
                self.seed([internal, legacy], records={internal['uid']: pending(internal),
                          legacy['uid']: pending(legacy)}, checkpoints={saved['uid']: saved}, mapping=mapping)
                self.assertEqual(c.main(), 0)
                self.assertEqual(self.public(), set())
                rec = self.records()[internal['uid']]
                self.assertEqual(rec['hold_source_url'], '')
                self.assertFalse(c.hentai3_can_restore(saved, rec))

    def test_fold_preserves_repair_hold_on_either_side(self):
        for held_side in ('internal', 'public'):
            for held_newer in (False, True):
                with self.subTest(held_side=held_side, held_newer=held_newer):
                    internal, public = row(), row('g123')
                    records = {x['uid']: pending(x) for x in (internal, public)}
                    held = records[(internal if held_side == 'internal' else public)['uid']]
                    other = records[(public if held_side == 'internal' else internal)['uid']]
                    held.update(hold_source_url='', hold_detail='3hentai_mislinked_split',
                                repair_original={'thumbnail': 'wrong.webp', 'tags': ['wrong']})
                    held['item'].update(source_url='', last_seen='2027' if held_newer else '2020')
                    other['item']['last_seen'] = '2021'
                    store = {'records': records, 'verified_checkpoints': {public['uid']: row('g123', checked=True)}}
                    ids.normalize_incoming(dict(internal, public_gallery_id='123'), {}, store,
                                           ids.empty_id_map(), STAMP, c.merge_item, ids.migration_stats())
                    rec = records[internal['uid']]
                    self.assertEqual(rec['hold_source_url'], '')
                    self.assertEqual(rec['hold_detail'], '3hentai_mislinked_split')
                    self.assertEqual(rec['repair_original'], held['repair_original'])
                    self.assertFalse(c.hentai3_can_restore(row(checked=True), rec))
                    self.assertFalse(c.hentai3_can_restore(store['verified_checkpoints'][internal['uid']], rec))

    def test_fold_direct_holds_and_simple_move(self):
        for target_exists in (False, True):
            public, internal = row('g123'), row()
            records = {public['uid']: pending(public)}
            if target_exists:
                records[internal['uid']] = pending(internal)
            ids.normalize_incoming(dict(internal, public_gallery_id='123'), {}, {'records': records},
                                   ids.empty_id_map(), STAMP, c.merge_item, ids.migration_stats())
            self.assertEqual(records[internal['uid']]['hold_source_url'], DIRECT)
            self.assertTrue(c.hentai3_can_restore(row(checked=True), records[internal['uid']]))

    def test_simple_move_freezes_empty_url_before_later_item_changes(self):
        public = row('g123', url='')
        records = {public['uid']: pending(public)}
        ids.normalize_incoming(dict(row(), public_gallery_id='123'), {}, {'records': records},
                               ids.empty_id_map(), STAMP, c.merge_item, ids.migration_stats())
        rec = records[row()['uid']]
        self.assertEqual(rec['hold_source_url'], '')
        rec['item']['source_url'] = DIRECT
        self.assertFalse(c.hentai3_can_restore(row(checked=True), rec))

    def test_fold_unaudited_empty_hold_remains_repair_required(self):
        for empty_side in ('3000123', 'g123'):
            internal, public = row(), row('g123')
            records = {x['uid']: pending(x) for x in (internal, public)}
            records['3hentai:' + empty_side]['item']['source_url'] = ''
            ids.normalize_incoming(dict(internal, public_gallery_id='123'), {}, {'records': records},
                                   ids.empty_id_map(), STAMP, c.merge_item, ids.migration_stats())
            self.assertEqual(records[internal['uid']]['hold_source_url'], '')
            self.assertFalse(c.hentai3_can_restore(row(checked=True), records[internal['uid']]))

    def test_collect_fold_and_moved_checkpoint_cannot_bypass_hold_on_restart(self):
        internal, public = row(url=''), row('g123')
        original = {'thumbnail': 'wrong.webp'}
        self.seed([], records={internal['uid']: pending(internal, hold_source_url='',
                  hold_detail='3hentai_mislinked_split', repair_original=original),
                  public['uid']: pending(dict(public, last_seen='2027'))},
                  checkpoints={public['uid']: row('g123', checked=True)})
        # Keep the public checkpoint held until it is folded by collect.
        self.disk[c.FILTER_STATE_FILE]['records'][public['uid']]['hold_source_url'] = ''
        self.incoming = [dict(row(checked=True), public_gallery_id='123')]
        self.assertEqual(c.main(), 0)
        self.assertEqual(self.public(), set())
        rec = self.records()[internal['uid']]
        self.assertEqual(rec['repair_original'], original)
        self.assertEqual(rec['hold_detail'], '3hentai_mislinked_split')
        # Model an interruption that retains the moved checkpoint as well.
        self.disk[c.FILTER_STATE_FILE]['verified_checkpoints'] = {internal['uid']: row(checked=True)}
        self.incoming = []
        self.assertEqual(c.main(), 0)
        self.assertEqual(self.public(), set())
        self.assertEqual(self.records()[internal['uid']]['repair_original'], original)

    def test_fold_repair_original_conflict_uses_destination_then_missing_fields(self):
        public, internal = row('g123'), row()
        records = {public['uid']: pending(public, repair_original={'tags': ['source'], 'thumbnail': 'source'}),
                   internal['uid']: pending(internal, repair_original={'tags': ['destination']})}
        ids.normalize_incoming(dict(internal, public_gallery_id='123'), {}, {'records': records},
                               ids.empty_id_map(), STAMP, c.merge_item, ids.migration_stats())
        self.assertEqual(records[internal['uid']]['repair_original'],
                         {'tags': ['destination'], 'thumbnail': 'source'})

    def test_partial_save_restart_matches_clean_run_and_preserves_split_original(self):
        owner = row(checked=True)
        excess = dict(row('3000124', checked=True), thumbnail='wrong.webp', tags=['language:japanese', 'wrong'])
        hitomi = [dict(uid=f'hitomi:{n}', source='hitomi', source_id=str(n), title='Sample',
                       language='japanese', tags=['sample']) for n in range(1000, 1025)]
        items = [owner, excess] + hitomi
        def fetch(session, number, base):
            return dict(hitomi[number - 1000], filter_metadata_checked=c.hitomi.FILTER_METADATA_VERSION)
        with patch.object(c, 'HITOMI_FILTER_AUDIT_LIMIT', 25), patch.object(c.hitomi, 'fetch_filter_metadata', side_effect=fetch):
            self.seed(items)
            self.assertEqual(c.main(), 0)
            expected_public = self.public()
            expected_pending = {uid for uid, rec in self.records().items() if rec['status'] == 'pending'}
            expected_record = copy.deepcopy(self.records()[excess['uid']])
            self.seed(items)
            self.writes.clear()
            self.status = 'error'
            self.assertEqual(c.main(), 1)
            self.assertIn(c.FILTER_STATE_FILE, self.writes)
            self.assertNotIn(c.DATA_FILE, self.writes)
            partial = copy.deepcopy(self.records()[excess['uid']])
            self.assertEqual(partial['repair_original'], expected_record['repair_original'])
            self.assertNotIn('source_url', partial['item'])
            self.status = 'ok'
            self.assertEqual(c.main(), 0)
            self.assertEqual(self.public(), expected_public)
            self.assertEqual({uid for uid, rec in self.records().items() if rec['status'] == 'pending'}, expected_pending)
            resumed = self.records()[excess['uid']]
            self.assertEqual(resumed['repair_original'], expected_record['repair_original'])
            self.assertEqual(resumed['hold_source_url'], partial['hold_source_url'])
            # The later hold loop refreshes item from the old catalog. Its URL
            # and tags can return, but neither the split hold nor originals may.
            self.assertEqual(resumed['item']['source_url'], excess['source_url'])
            self.assertEqual(resumed['item']['tags'], excess['tags'])
            self.assertFalse(c.hentai3_can_restore(row('3000124', checked=True), resumed))
            self.assertEqual(c.main(), 0)
            self.assertEqual(self.public(), expected_public)
            self.assertEqual({uid for uid, rec in self.records().items() if rec['status'] == 'pending'}, expected_pending)
            self.assertEqual(self.records()[excess['uid']]['repair_original'], expected_record['repair_original'])

    def test_migrated_url_conflict_all_routes(self):
        for mode in ('startup', 'reverse', 'fold', 'fold_reverse'):
            for route in ('checkpoint', 'audit', 'collector'):
                with self.subTest(mode=mode, route=route):
                    reverse = 'reverse' in mode
                    moving = 'g123' if 'fold' in mode else '123'
                    catalog = row(moving if reverse else '3000456',
                                  url=DIRECT if reverse else 'https://3hentai.net/d/456', checked=True)
                    held = row('3000456' if reverse else moving,
                               url='https://3hentai.net/d/456' if reverse else DIRECT)
                    saved = row('3000456', url=held['source_url'], checked=True)
                    mapping = ids.empty_id_map()
                    if 'fold' not in mode:
                        mapping['public_to_internal']['123'] = saved['uid']
                    self.incoming = ([dict(saved, public_gallery_id='123')] if 'fold' in mode
                                     else [saved] if route == 'collector' else [])
                    self.seed([catalog], records={held['uid']: pending(held)}, mapping=mapping,
                              checkpoints={saved['uid']: saved} if route == 'checkpoint' and 'fold' not in mode else {})
                    def audit(items, **kw):
                        for item in items:
                            item.update(saved)
                        return {}
                    with patch.object(h, 'enrich_existing_for_filter',
                                      side_effect=audit if route == 'audit' and 'fold' not in mode else lambda *a, **kw: {}):
                        self.assertEqual(c.main(), 0)
                    self.assertEqual(self.public(), set())
                    rec = self.records()[saved['uid']]
                    self.assertEqual(rec['status'], 'pending')
                    self.assertEqual(rec['hold_detail'], '3hentai_url_conflict')

    def test_fold_records_direct_conflict_either_winner(self):
        for newer in ('g123', '3000456'):
            with self.subTest(newer=newer):
                records = {item['uid']: pending(dict(item, last_seen='2027' if item['source_id'] == newer else '2020'))
                           for item in (row('g123'), row('3000456', url='https://3hentai.net/d/456'))}
                ids.normalize_incoming(dict(row('3000456'), public_gallery_id='123'), {}, {'records': records},
                                       ids.empty_id_map(), STAMP, c.merge_item, ids.migration_stats())
                rec = records['3hentai:3000456']
                self.assertEqual(rec['hold_detail'], '3hentai_url_conflict')
                self.assertFalse(c.hentai3_can_restore(row('3000456', checked=True), rec))

    def test_hold_gallery_identity_all_restore_routes(self):
        for route in ('checkpoint', 'audit', 'collector'):
            for gallery in ('123', '999'):
                with self.subTest(route=route, gallery=gallery):
                    held, saved = row(), row(url='https://3hentai.net/d/' + gallery, checked=True)
                    self.seed([], records={held['uid']: pending(held)},
                              checkpoints={saved['uid']: saved} if route == 'checkpoint' else {})
                    self.incoming = [saved] if route == 'collector' else []
                    def audit(items, **kw):
                        for item in items:
                            item.update(saved)
                        return {}
                    with patch.object(h, 'enrich_existing_for_filter',
                                      side_effect=audit if route == 'audit' else lambda *a, **kw: {}):
                        self.assertEqual(c.main(), 0)
                    self.assertEqual(self.public(), {saved['uid']} if gallery == '123' else set())
                    if gallery != '123':
                        self.assertEqual(self.records()[held['uid']]['status'], 'pending')

    def test_input_pair_conflicts_same_uid_migration_and_fold(self):
        for pair in ('catalog_checkpoint', 'catalog_collector', 'checkpoint_collector'):
            for mode in ('same', 'migration', 'fold'):
                for url in ('', 'https://3hentai.net/search?q=Sample', DIRECT):
                    with self.subTest(pair=pair, mode=mode, url=url):
                        original = row('123' if mode == 'migration' and url == DIRECT else
                                       'g123' if mode == 'fold' else '3000456', url=url, checked=True)
                        saved = row('3000456', url='https://3hentai.net/d/999', checked=True)
                        mapping = ids.empty_id_map()
                        if mode == 'migration':
                            mapping['public_to_internal']['123'] = saved['uid']
                        catalog = [original] if pair.startswith('catalog') else []
                        checkpoints = ({saved['uid']: saved} if pair == 'catalog_checkpoint' else
                                       {original['uid']: original} if pair == 'checkpoint_collector' else {})
                        self.seed(catalog, checkpoints=checkpoints, mapping=mapping)
                        self.incoming = ([dict(saved, public_gallery_id='123')] if mode == 'fold' or pair.endswith('collector') else [])
                        self.assertEqual(c.main(), 0)
                        if pair.endswith('collector') and url == DIRECT:
                            # A confirmed row with no hold survives; the new URL must not replace it.
                            self.assertEqual(self.public(), {saved['uid']})
                            self.assertEqual(self.disk[c.DATA_FILE]['items'][0]['source_url'], DIRECT)
                        else:
                            self.assertEqual(self.public(), set())

    def test_reconcile_record_is_idempotent(self):
        for url in ('', 'https://3hentai.net/search?q=Sample', 'https://3hentai.net/d/456'):
            for detail in ('3hentai_unaudited', '3hentai_mislinked_split'):
                with self.subTest(url=url, detail=detail):
                    rec = pending(row(), hold_detail=detail, repair_original={'tags': ['original']})
                    ids.reconcile_record(row(url=url), rec)
                    once = copy.deepcopy(rec)
                    ids.reconcile_record(row(url=url), rec)
                    self.assertEqual(rec, once)
