import copy
import unittest
from unittest.mock import Mock, patch

import crawler
from sources import nhentai as n
from tools.probe_nhentai import run_probe


class NhentaiTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(n, 'polite_sleep'))
        self.session = Mock()
        self.session.__enter__ = Mock(return_value=self.session)
        self.session.__exit__ = Mock(return_value=False)
        self.enterContext(patch.object(n.requests, 'Session', return_value=self.session))

    def gallery(self, gid=9000001):
        return {'id': gid, 'title': {'japanese': '中立なサンプル作品'},
                'tags': [{'type': 'language', 'name': 'japanese'},
                         {'type': 'category', 'name': 'sample'},
                         {'type': 'tag', 'name': 'sample'}],
                'num_pages': 12, 'upload_date': 1}

    def response(self, status=200, payload=None, headers=None, body=''):
        response = Mock(status_code=status,
                        headers=headers or {'Content-Type': 'application/json'}, text=body)
        if payload is None:
            response.json.side_effect = ValueError('not JSON')
        else:
            response.json.return_value = payload
        return response

    def test_first_403_stops_without_overwriting_it_with_fallback_404(self):
        self.session.get.side_effect = [self.response(403), self.response(404)]
        with self.assertRaises(n.FetchError) as caught:
            n._detail(self.session, '9000001')
        self.assertIn('403', str(caught.exception))
        self.assertNotIn('404', str(caught.exception))
        self.assertEqual(self.session.get.call_count, 1)

    def test_missing_endpoints_preserve_both_errors(self):
        self.session.get.side_effect = [self.response(404), self.response(410)]
        with self.assertRaises(n.FetchError) as caught:
            n._detail(self.session, '9000001')
        self.assertEqual([x['http_status'] for x in caught.exception.attempts], [404, 410])

    def test_missing_endpoint_can_fall_back_to_valid_nested_json(self):
        self.session.get.side_effect = [self.response(404), self.response(payload={'data': self.gallery()})]
        self.assertEqual(n._detail(self.session, '9000001')['id'], 9000001)
        self.assertEqual(self.session.get.call_count, 2)

    def test_auth_rate_limit_and_challenge_stop_the_whole_probe(self):
        for response in (self.response(401), self.response(429, headers={'Retry-After': '3600'}),
                         self.response(200, headers={'Content-Type': 'text/html', 'cf-mitigated': 'challenge'})):
            with self.subTest(status=response.status_code, headers=response.headers):
                self.session.get.reset_mock()
                self.session.get.return_value = response
                report = run_probe()
                self.assertEqual(self.session.get.call_count, 1)
                self.assertTrue(report['collector']['stopped'])
                self.assertFalse(report['ready_for_integration'])

    def test_generic_html_is_not_mistaken_for_successful_api_or_authentication(self):
        self.session.get.return_value = self.response(headers={'Content-Type': 'text/html'},
            body='<title>Site Unavailable</title><p>Unable to access this site.</p>')
        report = run_probe()
        attempt = report['collector']['attempts'][0]
        self.assertEqual(attempt['reason'], 'site_unavailable')
        self.assertFalse(attempt['challenge'])
        self.assertFalse(attempt['login_redirect'])
        self.assertIn('server', attempt)
        self.assertFalse(attempt['cf_ray_present'])
        self.assertFalse(report['ready_for_integration'])
        self.assertEqual(self.session.get.call_count, 1)

    def test_login_redirect_is_not_followed_or_logged_with_sensitive_parameters(self):
        self.session.get.return_value = self.response(302, headers={'Location': '/login?token=private'})
        report = run_probe()
        self.assertTrue(report['collector']['attempts'][0]['login_redirect'])
        self.assertFalse(self.session.get.call_args.kwargs['allow_redirects'])
        self.assertNotIn('private', str(report))
        self.assertEqual(self.session.get.call_count, 1)

    def test_full_search_metadata_needs_no_detail_or_image_download(self):
        self.session.get.return_value = self.response(payload={'result': [self.gallery()]})
        report = run_probe()
        self.assertEqual(report['collector']['search_metadata_used'], 1)
        self.assertEqual(report['collector']['detail_requests'], 0)
        self.assertEqual(self.session.get.call_count, 1)
        self.assertEqual(report['eligible_after_filters'], 1)
        self.assertTrue(report['ready_for_integration'])
        self.assertNotIn('中立なサンプル作品', str(report))
        self.assertNotIn('nhentai', crawler.SOURCES, 'Probe alone cannot enable publication')

    def test_incomplete_search_uses_matching_detail_metadata(self):
        self.session.get.side_effect = [self.response(payload={'result': [{'id': 9000001}]}),
                                       self.response(payload=self.gallery())]
        report = run_probe()
        self.assertTrue(report['ready_for_integration'])
        self.assertEqual(report['collector']['detail_requests'], 1)
        self.assertEqual(self.session.get.call_count, 2)

    def test_wrong_detail_identity_or_api_error_cannot_pass(self):
        for payload in (self.gallery(9000002), {'error': 'authentication required'}, {'unexpected': []}):
            with self.subTest(payload=payload):
                self.session.get.return_value = self.response(payload=payload)
                with self.assertRaises(n.FetchError):
                    n._detail(self.session, '9000001')

    def test_missing_tags_title_or_language_are_not_verified(self):
        for changes in ({'tags': []}, {'tags': 'sample'}, {'title': {}},
                        {'tags': [{'type': 'tag', 'name': 'sample'}]},
                        {'tags': [{'type': 'language', 'name': 'japanese'}]},
                        {'tags': [{'type': 'tag', 'name': None}]}):
            with self.subTest(changes=changes):
                row = {**self.gallery(), **changes}
                self.assertIsNone(n._normalize(row))

    def test_common_language_and_block_filters_gate_the_probe_result(self):
        blocked = self.gallery()
        blocked['tags'].append({'type': 'tag', 'name': 'blood'})
        other_language = self.gallery(9000002)
        other_language['tags'][0]['name'] = 'english'
        self.session.get.return_value = self.response(payload={'result': [blocked, other_language]})
        report = run_probe()
        self.assertEqual(report['collector']['accepted_raw'], 2)
        self.assertEqual(report['eligible_after_filters'], 0)
        self.assertFalse(report['ready_for_integration'])
        self.assertEqual(sum(report['rejected_reasons'].values()), 2)

    def test_probe_bounds_details_and_does_not_mutate_cursor(self):
        state = {'backfill_page': 10}
        original = copy.deepcopy(state)
        rows = [{'id': 9000000 + i} for i in range(10)]
        invalid = [dict(self.gallery(9000000 + i), tags=[]) for i in range(3)]
        self.session.get.side_effect = [self.response(payload={'result': rows})] + [
            self.response(payload=x) for x in invalid]
        items, new_state, status = n.collect(state, latest_pages=1, backfill_pages=0,
                                            max_details=3, max_items=3)
        self.assertEqual(items, [])
        self.assertEqual(status['detail_requests'], 3)
        self.assertEqual(self.session.get.call_count, 4)
        self.assertEqual(state, original)
        self.assertEqual(new_state, original)

    def test_api_error_is_not_reported_as_empty_success(self):
        self.session.get.return_value = self.response(payload={'error': 'access denied'})
        report = run_probe()
        self.assertEqual(report['collector']['status'], 'error')
        self.assertFalse(report['ready_for_integration'])


if __name__ == '__main__':
    unittest.main()
