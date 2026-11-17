import copy
import json
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import crawler
from sources import nhentai as nh
from sources.common import iso_from_unix

FIXTURES = Path(__file__).parent / "fixtures" / "nhentai"


def fixture(name):
    return json.loads((FIXTURES / f"{name}.json").read_text())


def rows(*ids):
    return [{"id": gid, "thumbnail": "galleries/123/thumb.webp"} for gid in ids]


def detail(gid):
    raw = fixture("detail-697843")
    raw["id"] = int(gid)
    return raw


class NhentaiTests(unittest.TestCase):
    def run_collect(self, pages, state=None, details=None, **settings):
        original = copy.deepcopy(state)
        defaults = dict(NH_LATEST_PAGES=3, NH_MAX_DETAILS_PER_RUN=45,
                        NH_INITIAL_BACKFILL_ITEMS=0, NH_BACKFILL_SEEK_PAGES=6)
        defaults.update(settings)
        def search(session, page):
            value = pages.get(page, [])
            if isinstance(value, Exception):
                raise value
            return value
        def fetch(session, gid):
            value = (details or {}).get(int(gid), detail(gid))
            if isinstance(value, Exception):
                raise value
            return value
        with patch.multiple(nh, **defaults), patch.object(nh, '_search_page', side_effect=search) as search_mock, \
             patch.object(nh, '_detail', side_effect=fetch) as detail_mock, patch.object(nh, 'polite_sleep'), patch.object(nh.time, 'monotonic', return_value=0):
            result = nh.collect(state)
        self.assertEqual(state, original)
        self.assertEqual(len(detail_mock.call_args_list), len({c.args[1] for c in detail_mock.call_args_list}))
        return result, search_mock, detail_mock

    def test_deadline_contiguous_and_scan(self):
        for ticks, pages, expected in [([0, 0, 421], {1: rows(12,11,10)}, 11),
                                        ([0, 421], {1: rows(12,11)}, 10),
                                        ([0, 421], {1: rows(11,10)}, 10)]:
            with patch.object(nh.time, 'monotonic', side_effect=ticks), \
                 patch.object(nh, '_search_page', side_effect=lambda session,p: pages[p]) as search, \
                 patch.object(nh, '_detail', side_effect=lambda session,g: detail(g)) as fetch, \
                 patch.object(nh, 'polite_sleep') as sleep:
                items, state, status = nh.collect({'latest_id':10})
            self.assertEqual(state['latest_id'], expected)
            self.assertTrue(status['deadline_reached'])
            self.assertTrue(status['partial'])
            self.assertEqual(status['status'], 'ok')
            self.assertEqual(status['detail_errors'], 0)
            self.assertEqual(search.call_count, 1)
            self.assertEqual(fetch.call_count, expected - 10)
            sleep.assert_not_called()

    def test_state_normalization_and_unordered_latest(self):
        original = {'latest_id':'10'}
        (_, state, status), _, _ = self.run_collect({1:rows(12,11),2:nh.HTTPError(500)}, original)
        self.assertEqual(state, original)
        self.assertEqual(status['status'], 'error')
        (_, state, _), search, _ = self.run_collect({1:rows(10)}, original)
        self.assertTrue(state['backfill_complete'])
        self.assertEqual(state['backfill_remaining'], 0)
        self.assertEqual(search.call_count, 1)
        (items,state,_), _, _ = self.run_collect({1:rows(11,6,9,8,7)}, {'latest_id':'8'})
        self.assertEqual([item['source_id'] for item in items], ['9','11'])
        self.assertEqual(state['latest_id'], 11)
        for failures in [[], {'1':'2','2':'bad'}]:
            (_,state,_), _, _ = self.run_collect({1:rows(10)},
                {'latest_id':'bad','backfill_before_id':-1,'backfill_page':'bad',
                 'backfill_remaining':-2,'detail_failures':failures})
            self.assertEqual(state['latest_id'], 10)
            self.assertEqual(state['detail_failures'], {'1':2} if failures else {})

    def test_single_latest_failure_confirmation(self):
        state = {'latest_id':10}
        for attempt in range(4):
            (_,state,status), _, calls = self.run_collect({1:rows(11,10)}, state, {11:nh.HTTPError(500)})
            self.assertEqual(status['status'], 'ok')
            if attempt < 3:
                self.assertTrue(status['partial'])
                self.assertEqual(state['latest_id'],10)
                self.assertEqual(state['detail_failures'], {'11':attempt+1})
                self.assertEqual([c.args[1] for c in calls.call_args_list], ['11','10'])
            else:
                self.assertEqual(state['latest_id'],11)

    def test_backfill_rechecks_processed_page(self):
        base = dict(latest_id=20, backfill_before_id=10, backfill_page=2, backfill_remaining=3)
        for shifted in (True, False):
            pages = [rows(20), rows(10,9), rows(7,6), rows(9,8) if shifted else rows(10,9), rows(7,6)]
            with patch.object(nh, '_search_page', side_effect=pages) as search, \
                 patch.object(nh, '_detail', side_effect=lambda session,g: detail(g)) as fetch, \
                 patch.object(nh, 'polite_sleep'), patch.object(nh.time, 'monotonic', return_value=0):
                _,state,_ = nh.collect({**base, "backfill_remaining": 2 if shifted else 3})
            self.assertEqual([c.args[1] for c in search.call_args_list], [1,2,3,2] if shifted else [1,2,3,2,3])
            self.assertEqual([c.args[1] for c in fetch.call_args_list], ['9','8'] if shifted else ['9','7','6'])
            self.assertEqual(state['backfill_remaining'], 0)

    def test_mapping_and_title_priorities(self):
        raw = fixture('detail-697843')
        item = nh._normalize(raw)
        gid = str(raw['id'])
        self.assertEqual(item['uid'], 'nhentai:' + gid)
        self.assertEqual(item['source_id'], gid)
        self.assertEqual(item['source_url'], f'https://nhentai.net/g/{gid}/')
        self.assertEqual(item['title_jp'], raw['title']['japanese'])
        self.assertEqual(item['pages'], raw['num_pages'])
        self.assertEqual(item['posted_at'], iso_from_unix(raw['upload_date']))
        self.assertIsNone(item['rating'])
        self.assertEqual(item['popularity'], raw['num_favorites'])
        self.assertEqual(item['thumbnail'], 'https://t.nhentai.net/' + raw['thumbnail']['path'])
        self.assertEqual(item['language'], 'japanese')
        for field, typ in [('tags','tag'),('artists','artist'),('groups','group'),('parodies','parody'),('characters','character')]:
            self.assertEqual(item[field], list(dict.fromkeys(t['name'] for t in raw['tags'] if t['type'] == typ)))
        for titles, expected in [({'japanese':'jp','pretty':'pretty','english':'en'},'jp'),
                                 ({'pretty':'pretty','english':'en'},'pretty'), ({'english':'en'},'en')]:
            with self.subTest(titles=titles):
                self.assertEqual(nh._normalize(dict(raw,title=titles))['title'],expected)
        self.assertEqual(nh._normalize(dict(raw,thumbnail={}), 'galleries/12/thumb.webp')['thumbnail'],
                         'https://t.nhentai.net/galleries/12/thumb.webp')

    def test_search_fixtures_and_all_tag_types(self):
        for name in ['search-p1', 'search-p2']:
            session = Mock()
            session.get.return_value.status_code = 200
            session.get.return_value.json.return_value = fixture(name)
            self.assertEqual(nh._search_page(session, 1), fixture(name)['result'])
        raw = detail(1)
        raw['tags'] = [{'type': typ, 'name': typ + '-value'} for typ in
                       ['tag', 'artist', 'group', 'parody', 'character', 'category']]
        item = nh._normalize(raw)
        for field, typ in [('tags', 'tag'), ('artists', 'artist'), ('groups', 'group'),
                           ('parodies', 'parody'), ('characters', 'character')]:
            self.assertEqual(item[field], [typ + '-value'])
        self.assertEqual(item['category'], 'category-value')

    def test_strict_language(self):
        raw = fixture('detail-697883')
        self.assertEqual(nh._normalize(raw)['language'], '')
        result, _, _ = self.run_collect({1: rows(697883)}, details={697883:raw})
        self.assertEqual(result[0], [])
        self.assertEqual(result[2]['skipped_non_japanese'], 1)
        raw['tags'] = [{'type':'language', 'name':'japanese | definition revised please read the wiki'}]
        self.assertEqual(nh._normalize(raw)['language'], '')

    def test_thumb_validation(self):
        for path in ['', '../galleries/1/thumb.jpg','galleries/../thumb.webp','https://evil.test/a',
                     '/galleries/1/thumb.jpg','galleries/x/thumb.jpg','galleries/1/thumb.jpg?x=1']:
            with self.subTest(path=path):
                self.assertEqual(nh._thumb_url(path),'')
        self.assertEqual(nh._thumb_url('galleries/12/thumb.a.webp'),'https://t.nhentai.net/galleries/12/thumb.a.webp')

    def test_transport(self):
        session = Mock()
        session.get.return_value.status_code = 200
        session.get.return_value.json.return_value = fixture('search-p1')
        self.assertTrue(nh._search_page(session,1))
        self.assertEqual(session.get.call_args.args[0], nh.SEARCH_URL.format(page=1))
        self.assertFalse(session.get.call_args.kwargs['allow_redirects'])
        self.assertEqual(session.get.call_args.kwargs['timeout'], nh.NH_REQUEST_TIMEOUT)
        session.get.return_value.json.return_value = fixture('detail-697843')
        nh._detail(session,'697843')
        self.assertEqual(session.get.call_args.args[0], 'https://nhentai.net/api/v2/galleries/697843')
        for gid in ['../1','https://evil.test', 'abc', '１２']:
            with self.assertRaises(ValueError):
                nh._detail(session,gid)
        for code in [301,302,404,429,500]:
            session.get.return_value.status_code=code
            with self.assertRaises(nh.HTTPError) as error:
                nh._detail(session,'1')
            self.assertEqual(error.exception.status_code, code)
        session.get.return_value.status_code=200
        session.get.return_value.json.return_value=[]
        with self.assertRaises(ValueError):
            nh._get_json(session,nh.SEARCH_URL.format(page=1))

    def test_initialization(self):
        (items,state,status), _, calls = self.run_collect({1:rows(10,9,8)}, NH_INITIAL_BACKFILL_ITEMS=5,
                                                        NH_MAX_DETAILS_PER_RUN=3)
        self.assertEqual([c.args[1] for c in calls.call_args_list],['8','9','10'])
        self.assertEqual(state['latest_id'],10)
        self.assertEqual(state['backfill_before_id'],8)
        self.assertEqual(state['backfill_page'],2)
        self.assertEqual(state['backfill_remaining'],5)
        self.assertEqual(status['status'],'ok')
        self.assertEqual(len(items),3)

    def test_latest_difference_and_zero(self):
        (items,state,status), search, _ = self.run_collect({1:rows(12,11,10,9)}, {'latest_id':10})
        self.assertEqual([i['source_id'] for i in items],['11','12'])
        self.assertEqual(search.call_count,1)
        (items,state,status), _, _ = self.run_collect({1:rows(10,9)}, {'latest_id':10})
        self.assertEqual(items,[])
        self.assertEqual(state.get('latest_id'),10)
        self.assertEqual(status['status'],'ok')

    def test_budget_contiguous_and_partial_initialization(self):
        (_,state,status), _, _ = self.run_collect({1:rows(13,12,11,10)}, {'latest_id':10},
                                                 NH_MAX_DETAILS_PER_RUN=1)
        self.assertEqual(state['latest_id'],11)
        (_,state,_), _, _ = self.run_collect({1:rows(13,12,11)}, NH_MAX_DETAILS_PER_RUN=1,
                                            NH_INITIAL_BACKFILL_ITEMS=5)
        self.assertEqual(state['latest_id'],11)
        self.assertEqual(state['backfill_before_id'],11)
        self.assertEqual(state['backfill_remaining'],5)

    def test_failure_stops_latest(self):
        (_,state,status), _, calls = self.run_collect({1:rows(13,12,11,10)}, {'latest_id':10},
                                                       {12:RuntimeError('bad')})
        self.assertEqual(state['latest_id'],11)
        self.assertEqual(state['detail_failures'], {'12':1})
        self.assertEqual(calls.call_count,2)
        self.assertTrue(status['partial'])
        self.assertIn('bad',status['message'])
        self.assertEqual(status['status'],'ok')

    def test_404_and_429(self):
        (_,state,status), _, _ = self.run_collect({1:rows(12,11,10)}, {'latest_id':10},
                                                   {11:nh.HTTPError(404)})
        self.assertEqual(state['latest_id'],12)
        self.assertFalse(state['detail_failures'])
        (_,state,status), _, calls = self.run_collect({1:rows(13,12,11,10)}, {'latest_id':10},
                                                       {12:nh.HTTPError(429)})
        self.assertEqual(state['latest_id'],11)
        self.assertTrue(status['rate_limited'])
        self.assertEqual(calls.call_count,2)

    def test_page_one_failure_and_no_progress(self):
        original={'latest_id':10,'detail_failures':{'11':1}}
        for pages, details in [({1:RuntimeError('search')},None),
                               ({1:rows(11)},{11:RuntimeError('detail')})]:
            (items,state,status), _, _ = self.run_collect(pages,original,details)
            self.assertEqual(state,original)
            self.assertEqual(status['status'],'error')

    def test_backfill_alignment(self):
        base={'latest_id':20,'backfill_before_id':10,'backfill_page':3,'backfill_remaining':2}
        scenarios=[({3:rows(12,11,10),4:rows(9,8)},[3,4]),
                   ({3:rows(11,10,9,8)},[3]),
                   ({3:rows(7,6),2:rows(11,10,9,8)},[3,2])]
        for pages, expected in scenarios:
            with self.subTest(pages=pages):
                (items,state,status), search, _ = self.run_collect({1:rows(20),**pages},base)
                self.assertEqual([c.args[1] for c in search.call_args_list],[1]+expected)
                self.assertEqual(state['backfill_before_id'],8)
                self.assertEqual(state['backfill_remaining'],0)
                self.assertTrue(state['backfill_complete'])
        (_,state,_), search, calls = self.run_collect({1:rows(20),3:[],2:rows(10,9,8)},base)
        self.assertEqual([c.args[1] for c in search.call_args_list], [1,3,2])
        self.assertEqual([c.args[1] for c in calls.call_args_list], ['9','8'])
        self.assertTrue(state['backfill_complete'])

    def test_backfill_stops_on_failure_budget_and_seek_limit(self):
        base={'latest_id':20,'backfill_before_id':10,'backfill_page':2,'backfill_remaining':5}
        for details, settings in [({8:RuntimeError('bad')},{}), ({},{'NH_MAX_DETAILS_PER_RUN':1})]:
            (_,state,_), _, _ = self.run_collect({1:rows(20),2:rows(10,9,8,7)},base,details,**settings)
            self.assertEqual(state['backfill_before_id'],9)
            self.assertEqual(state['backfill_remaining'],4)
        (_,state,_), _, _ = self.run_collect({1:rows(20),2:rows(12,11),3:rows(11,10)},base,
                                            NH_BACKFILL_SEEK_PAGES=2)
        self.assertEqual(state['backfill_before_id'],10)

    def test_failure_threshold_cleanup_and_cap(self):
        failures={str(i):1 for i in range(100,160)}
        failures.update({'11':3,'12':1})
        (_,state,status), _, calls = self.run_collect({1:rows(12,11,10)},
                    {'latest_id':10,'detail_failures':failures})
        self.assertEqual(state['latest_id'],12)
        self.assertEqual(status['skipped_failed'],1)
        self.assertNotIn('11',state['detail_failures'])
        self.assertNotIn('12',state['detail_failures'])
        self.assertEqual(len(state['detail_failures']),50)
        self.assertEqual(calls.call_count,1)

    def test_sleep_and_deduplication(self):
        with patch.object(nh, '_search_page', side_effect=[rows(12,11),rows(11,10)]), \
             patch.object(nh, '_detail', side_effect=lambda session,gid:detail(gid)), \
             patch.object(nh,'polite_sleep') as sleep:
            items, _, _ = nh.collect({'latest_id':10})
        self.assertEqual(len(items),2)
        self.assertEqual([c.args[0] for c in sleep.call_args_list],
                         [nh.NH_SEARCH_DELAY_SEC,nh.NH_DETAIL_DELAY_SEC])

    def test_latest_gap_and_second_page_boundary(self):
        for pages, gap, expected in [
            ({1: rows(16, 15), 2: rows(14, 13), 3: rows(12, 11)}, True, list(range(11, 17))),
            ({1: rows(14, 13), 2: rows(12, 11, 10)}, False, list(range(11, 15))),
        ]:
            with self.subTest(gap=gap):
                (items, state, status), search, _ = self.run_collect(
                    pages, {'latest_id': 10}, NH_LATEST_PAGES=3)
                self.assertEqual(status['latest_gap'], gap)
                self.assertEqual([int(i['source_id']) for i in items], expected)
                self.assertEqual(search.call_count, 3 if gap else 2)
                self.assertEqual(state['latest_id'], expected[-1])

    def test_persistent_failure_recovers_after_four_runs(self):
        state = {'latest_id': 10}
        for attempt in range(1, 5):
            (items, state, status), _, calls = self.run_collect(
                {1: rows(13, 12, 11, 10)}, state, {11: nh.HTTPError(500)},
                NH_DETAIL_MAX_FAILURES=3)
            self.assertEqual(status['status'], 'ok')
            if attempt <= 3:
                self.assertTrue(status['partial'])
                self.assertEqual(state['latest_id'], 10)
                self.assertEqual(state['detail_failures'], {'11': attempt})
                self.assertEqual([c.args[1] for c in calls.call_args_list], ['11', '12'])
                self.assertEqual(status['detail_successes'], 1)
                self.assertEqual([i['source_id'] for i in items], ['12'])
            else:
                self.assertEqual(state['latest_id'], 13)
                self.assertEqual(state['detail_failures'], {})
                self.assertEqual(status['skipped_failed'], 1)

    def test_total_detail_outage_and_confirmation_budget(self):
        original = {'latest_id': 10, 'detail_failures': {'11': 1}}
        for budget, expected_calls in [(45, 2), (1, 1)]:
            (items, state, status), _, calls = self.run_collect(
                {1: rows(14, 13, 12, 11, 10)}, original,
                {gid: nh.HTTPError(500) for gid in range(11, 15)},
                NH_MAX_DETAILS_PER_RUN=budget)
            self.assertEqual(items, [])
            self.assertEqual(state, original)
            self.assertEqual(status['status'], 'error')
            self.assertEqual(status['detail_successes'], 0)
            self.assertEqual(calls.call_count, expected_calls)

    def test_backfill_confirmation_preserves_cursor(self):
        original = {'latest_id': 20, 'backfill_before_id': 10,
                    'backfill_page': 2, 'backfill_remaining': 3}
        (_, state, status), _, calls = self.run_collect(
            {1: rows(20), 2: rows(10, 9, 8, 7)}, original,
            {9: nh.HTTPError(500)})
        self.assertEqual(state['backfill_before_id'], 10)
        self.assertEqual(state['backfill_remaining'], 3)
        self.assertEqual(state['detail_failures'], {'9': 1})
        self.assertEqual(status['status'], 'ok')
        self.assertEqual([c.args[1] for c in calls.call_args_list], ['9', '8'])

    def test_non_japanese_confirmation_is_success(self):
        raw = detail(12)
        raw['tags'] = [{'type': 'language', 'name': 'japanese | definition'}]
        (items, state, status), _, _ = self.run_collect(
            {1: rows(12, 11, 10)}, {'latest_id': 10},
            {11: nh.HTTPError(500), 12: raw})
        self.assertEqual(items, [])
        self.assertEqual(status['detail_successes'], 1)
        self.assertEqual(status['status'], 'ok')
        self.assertEqual(state['latest_id'], 10)
        self.assertEqual(state['detail_failures'], {'11': 1})

    def test_initial_backfill_does_not_return_to_page_one(self):
        (_, state, status), search, _ = self.run_collect(
            {1: rows(10, 9), 2: rows(8, 7), 3: rows(6)},
            NH_INITIAL_BACKFILL_ITEMS=3)
        self.assertEqual([c.args[1] for c in search.call_args_list], [1, 2, 3, 2, 3])
        self.assertFalse(status['latest_gap'])
        self.assertEqual(state['backfill_before_id'], 6)

    def test_language_whitespace_and_popularity_types(self):
        raw = detail(11)
        raw['tags'] = [{'type': 'language', 'name': ' Japanese '}]
        self.assertEqual(nh._normalize(raw)['language'], 'japanese')
        for value in [True, False, '42', 4.2, None, 42]:
            with self.subTest(value=value):
                raw['num_favorites'] = value
                self.assertEqual(nh._normalize(raw)['popularity'],
                                 42 if type(value) is int else None)

    def test_wiring_and_no_block_import(self):
        self.assertIn('nhentai',crawler.SOURCES)
        self.assertNotIn('nhentai',crawler.RETIRED_SOURCES)
        self.assertNotIn('BLOCK_',Path(nh.__file__).read_text())

    @unittest.skipUnless(os.environ.get('NHENTAI_LIVE_TEST') == '1', 'live API opt-in only')
    def test_live_shape(self):
        with nh.requests.Session() as session:
            search=nh._search_page(session,1)
            self.assertIsInstance(search,list)
            self.assertTrue(search)
            raw=nh._detail(session,str(search[0]['id']))
            self.assertIsInstance(raw['title'],dict)
            self.assertIsInstance(raw['tags'],list)
            self.assertIsInstance(raw['thumbnail']['path'],str)
