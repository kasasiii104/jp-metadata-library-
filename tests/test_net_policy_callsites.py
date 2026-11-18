import io
import runpy
import socket
import sys
import unittest
from contextlib import ExitStack, redirect_stdout
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from PIL import Image
from requests import ConnectionError, Session

import crawler

from net_test_helpers import recording_session
from sources import asmhentai, ehentai, hentai3, hitomi, net_policy, nhentai, pururin
from tools import cache_3hentai_thumbs as cache_3hentai
from tools import cache_asmhentai_thumbs as cache_asmhentai
from tools import cache_nhentai_thumbs as cache_nhentai


PAGE_URL = "https://3hentai.net/d/42"
IMAGE_URL = "https://s1.3hentai.xyz/d42/thumb.jpg"
NH_THUMB_URL = "https://t.nhentai.net/galleries/42/thumb.jpg"
LOCAL_BASE = "http://127.0.0.1:3000"


class NetPolicyCallsites(unittest.TestCase):
    def setUp(self):
        self.session, self.transport = recording_session()
        self.direct_get = self.enterContext(patch.object(self.session, "get", wraps=self.session.get))
        self.transport.side_effect = ConnectionError("test transport")
        for source in (asmhentai, hentai3):
            self.enterContext(patch.object(source, "API_BASE", LOCAL_BASE))

    def local_calls(self):
        return (
            ("asmhentai search", "/asmhentai/search", lambda: asmhentai._search(self.session, "japanese", 1)),
            ("asmhentai get", "/asmhentai/get", lambda: asmhentai._get(self.session, "42")),
            ("3hentai repair", "/3hentai/get", lambda: hentai3._RepairGetBudget(self.session).get("42")),
            ("3hentai JSON", "/3hentai/search", lambda: hentai3._get_json(self.session, "/3hentai/search")),
            ("3hentai probe", "/3hentai/get", lambda: hentai3._probe_get_endpoint(self.session, "42")),
            ("3hentai root", "/", hentai3.collect),
        )

    def test_each_active_callsite_validates_its_purpose(self):
        calls = [
            (name, "local_api", LOCAL_BASE + path, run) for name, path, run in self.local_calls()
        ] + [
            ("source search", "hentai3_page", "https://3hentai.net/search",
             lambda: hentai3._direct_search_cards(self.session, "japanese", 1)),
            ("source gallery", "hentai3_page", PAGE_URL,
             lambda: hentai3._fetch_gallery_metadata(self.session, PAGE_URL)),
            ("source exact search", "hentai3_page", PAGE_URL,
             lambda: hentai3._resolve_public_search_exact(self.session, PAGE_URL, "title")),
            ("cache search", "hentai3_page", PAGE_URL,
             lambda: cache_3hentai.resolve_search_fallback(self.session, PAGE_URL, "title")),
            ("cache gallery", "hentai3_page", PAGE_URL,
             lambda: cache_3hentai.fetch_page_candidates(self.session, PAGE_URL)),
            ("cache image", "hentai3_image", IMAGE_URL,
             lambda: cache_3hentai.download_thumbnail(self.session, [IMAGE_URL])),
            ("nhentai search", "nhentai_api", nhentai.SEARCH_URL.format(page=1),
             lambda: nhentai._search_page(self.session, 1)),
            ("nhentai detail", "nhentai_api", nhentai.DETAIL_URL.format(gid=42),
             lambda: nhentai._detail(self.session, "42")),
            ("nhentai cache", "nhentai_thumb", NH_THUMB_URL,
             lambda: cache_nhentai.download(self.session, NH_THUMB_URL)),
        ]
        for name, purpose, url, run in calls:
            with self.subTest(name=name), patch.object(hentai3.requests, "Session", return_value=self.session), \
                 patch.object(net_policy, "validate_url", wraps=net_policy.validate_url) as validate:
                self.transport.reset_mock()
                try:
                    run()
                except ConnectionError:
                    pass
                validate.assert_any_call(url, purpose=purpose)
                self.transport.assert_called_once()
                prepared = self.transport.call_args.args[0]
                self.assertEqual(prepared.method, "GET")
                params = {
                    "asmhentai search": {"key": ["japanese"], "page": ["1"]},
                    "asmhentai get": {"book": ["42"]},
                    "3hentai repair": {"book": ["42"]},
                    "3hentai probe": {"book": ["42"]},
                    "source search": {"q": ["japanese"], "page": ["1"], "sort": ["recent"]},
                }.get(name)
                if params is None:
                    self.assertEqual(prepared.url, url)
                else:
                    target = urlsplit(prepared.url)
                    self.assertEqual(target._replace(query="").geturl(), url)
                    self.assertEqual(parse_qs(target.query), params)

    def test_asmhentai_download_converts_allowed_image_to_webp(self):
        url = "https://images.asmhentai.com/018/636218/1t.jpg"
        jpeg = io.BytesIO()
        with Image.new("RGB", (2, 2), "red") as image:
            image.save(jpeg, "JPEG")
        result = self.transport.return_value
        result.status_code = 200
        result.raw = Mock(_original_response=None)
        result.raw.read.return_value = jpeg.getvalue()
        self.transport.side_effect = None

        data = cache_asmhentai.download(self.session, url)

        self.assertIsNotNone(data)
        with Image.open(io.BytesIO(data)) as image:
            self.assertEqual(image.format, "WEBP")
            self.assertEqual(image.size, (2, 2))
            image.load()
        self.transport.assert_called_once()
        prepared = self.transport.call_args.args[0]
        self.assertEqual((prepared.method, prepared.url), ("GET", url))
        self.assertEqual(prepared.headers["Referer"], "https://asmhentai.com/")

    def test_disallowed_hosts_never_reach_transport(self):
        for host in ("s2.3hentai.xyz", "i.nhentai.net", "tn.hitomi.la", "ehgt.org",
                     "asmhentai.com", "pururin.me", "example.com"):
            with self.subTest(host=host):
                page = f"https://{host}/d/42"
                image = f"https://{host}/galleries/42/thumb.jpg"
                with patch.object(hentai3, "SOURCE_BASE", f"https://{host}"):
                    self.assertEqual(hentai3._direct_search_cards(self.session, "japanese", 1)[0], [])
                self.assertIsNone(hentai3._fetch_gallery_metadata(self.session, page)[0])
                self.assertIsNone(hentai3._resolve_public_search_exact(self.session, page, "title")[0])
                self.assertIsNone(cache_3hentai.resolve_search_fallback(self.session, page, "title")[0])
                self.assertEqual(cache_3hentai.fetch_page_candidates(self.session, page)[0], [])
                self.assertIsNone(cache_3hentai.download_thumbnail(self.session, [image])[0])
                self.assertIsNone(cache_nhentai.download(self.session, image))
                with redirect_stdout(io.StringIO()) as output, patch.object(
                    net_policy, "validate_url", wraps=net_policy.validate_url,
                ) as validate:
                    self.assertIsNone(cache_asmhentai.download(self.session, image))
                validate.assert_called_once_with(image, purpose="asmhentai_image")
                self.assertIn("許可ホスト外のため拒否", output.getvalue())
                self.transport.assert_not_called()
                self.direct_get.assert_not_called()

    def test_thumbnail_extraction_preserves_urls_then_download_rejects_them(self):
        url = "https://example.com/cover.jpg"
        api_url = hentai3._best_thumbnail_url({"images": [{"thumbnail": url}]})
        html_urls = cache_3hentai.page_image_candidates(
            f'<meta property="og:image" content="{url}">', PAGE_URL,
        )
        self.assertEqual(api_url, url)
        self.assertEqual(html_urls, [url])
        for origin, candidates in (("API", [api_url]), ("HTML", html_urls), ("stored row", [url])):
            with self.subTest(origin=origin):
                self.assertIsNone(cache_3hentai.download_thumbnail(self.session, candidates)[0])
                self.transport.assert_not_called()
                self.direct_get.assert_not_called()

    def test_invalid_api_bases_are_rejected_at_every_local_callsite(self):
        for base in ("http://localhost:3000", "http://127.0.0.1:3001", "https://example.com",
                     LOCAL_BASE + "?", LOCAL_BASE + "#"):
            with patch.object(asmhentai, "API_BASE", base), patch.object(hentai3, "API_BASE", base), \
                 patch.object(hentai3.requests, "Session", return_value=self.session):
                for name, _, run in self.local_calls():
                    with self.subTest(base=base, name=name):
                        if name in {"3hentai root"}:
                            run()
                        else:
                            with self.assertRaises(net_policy.URLPolicyError):
                                run()
                        self.transport.assert_not_called()
                        self.direct_get.assert_not_called()

    def test_collectors_preserve_state_on_policy_denial(self):
        denied = net_policy.URLPolicyError("DNS denied")
        cases = (
            (asmhentai, {"detail_cursor": 0}, [
                ("_search", {"return_value": [{"id": 42}]}),
                ("_get", {"side_effect": denied}),
            ]),
            (ehentai, {"retry_refs": {"42": {"token": "token", "failures": 2}}}, [
                ("EH_LATEST_PAGES", {"new": 0}),
                ("EH_BACKFILL_PAGES", {"new": 0}),
                ("_metadata", {"side_effect": denied}),
            ]),
            (hitomi, {"backfill_offset": 100, "retry_ids": {"42": {"failures": 2}}}, [
                ("_range_ids", {"side_effect": [([], 150, None), ([42], 150, None)]}),
                ("_fetch_one", {"side_effect": denied}),
            ]),
            (hentai3, {"backfill_page": 3}, [
                ("guarded_request", {"return_value": self.transport.return_value}),
                ("_direct_search_cards", {"return_value": ([], {
                    "policy_denied": True, "error": str(denied),
                })}),
            ]),
        )
        for source, state, patches in cases:
            with self.subTest(source=source.__name__), ExitStack() as stack:
                for name, kwargs in patches:
                    stack.enter_context(patch.object(source, name, **kwargs))
                items, result_state, result = source.collect(state)
                self.assertEqual(items, [])
                self.assertEqual(result_state, state)
                self.assertEqual(result["status"], "error")
                self.assertTrue(result["partial"])
                self.assertIn(str(denied), result["message"])

    def collect_3hentai_pages(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        for name, value in {
            "HENTAI3_LATEST_PAGES": 0, "HENTAI3_BACKFILL_PAGES": 1,
            "HENTAI3_GET_DEBUG_LIMIT": 0, "HENTAI3_GALLERY_METADATA_LIMIT": 2,
        }.items():
            stack.enter_context(patch.object(hentai3, name, value))
        stack.enter_context(patch.object(hentai3.time, "sleep"))
        rows = [{"id": n, "title": f"Work {n}", "url": url} for n, url in (
            (1, "https://example.org/d/1"), (2, "https://3hentai.net/d/2"),
        )]
        stack.enter_context(patch.object(hentai3, "_get_json", return_value=(rows, "mock")))
        stack.enter_context(patch.object(hentai3, "_direct_search_cards", return_value=([], {})))
        return stack

    def test_collect_skips_data_url_and_advances_on_consecutive_runs(self):
        stack = self.collect_3hentai_pages()
        stack.enter_context(patch.object(hentai3.requests, "Session", return_value=self.session))
        self.transport.side_effect = None
        self.transport.return_value.status_code = 200
        self.transport.return_value.text = "<html></html>"
        self.transport.return_value.url = "https://3hentai.net/d/2"
        state = {"backfill_page": 3}
        for page in (4, 5):
            self.transport.reset_mock()
            items, state, status = hentai3.collect(state)
            self.assertEqual([item["source_url"] for item in items], ["https://3hentai.net/d/2"])
            self.assertEqual(state["backfill_page"], page)
            self.assertEqual(status["status"], "ok")
            self.assertEqual(status["gallery_metadata_url_policy_rejected"], 1)
            self.assertEqual([call.args[0].url for call in self.transport.call_args_list],
                             [LOCAL_BASE + "/", "https://3hentai.net/d/2"])

    def test_collect_fixed_endpoint_denial_still_preserves_state(self):
        state = {"backfill_page": 3}
        with patch.object(hentai3, "API_BASE", "https://example.org"), patch.object(
            hentai3.requests, "Session", return_value=self.session,
        ):
            items, after, status = hentai3.collect(state)
        self.assertEqual((items, after, status["status"]), ([], state, "error"))
        self.assertIn("local_api policy", status["message"])
        self.transport.assert_not_called()

    def test_collect_dns_denial_still_preserves_state(self):
        self.collect_3hentai_pages()
        state = {"backfill_page": 3}
        with Session() as session, patch.object(
            hentai3.requests, "Session", return_value=session,
        ), patch.object(
            hentai3, "guarded_request", wraps=net_policy.guarded_request,
        ) as guarded, patch("socket.getaddrinfo", return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", 443)),
        ]) as lookup, patch("socket.socket") as make_socket:
            def send(session, method, url, **kwargs):
                if kwargs["purpose"] == "local_api":
                    return self.transport.return_value
                return net_policy.guarded_request(session, method, url, **kwargs)

            guarded.side_effect = send
            self.transport.return_value.status_code = 200
            items, after, status = hentai3.collect(state)

        self.assertEqual((items, after, status["status"]), ([], state, "error"))
        self.assertIn("Non-public DNS", status["message"])
        lookup.assert_called_once_with("3hentai.net", 443, socket.AF_UNSPEC, socket.SOCK_STREAM)
        make_socket.assert_not_called()

    def test_hitomi_audit_counts_policy_denial_as_failed(self):
        uid = "hitomi:42"
        with patch.object(crawler, "HITOMI_FILTER_AUDIT_LIMIT", 2), patch.object(
            hitomi, "fetch_filter_metadata", side_effect=net_policy.URLPolicyError("DNS denied"),
        ) as fetch:
            stats = crawler.audit_existing_hitomi(
                {uid: {"uid": uid, "source": "hitomi", "source_id": "42"}},
                {}, "2026-11-18T00:00:00Z",
            )
        fetch.assert_called_once()
        self.assertEqual(stats["attempted"], 1)
        self.assertEqual(stats["failed"], 1)
        self.assertEqual(stats["failed_reasons"], {"url_policy": 1})
        self.assertTrue(stats["stopped"])

    def test_nhentai_redirect_is_an_http_error_without_followup(self):
        self.transport.side_effect = None
        response = self.transport.return_value
        response.status_code = 302
        response.headers = {"Location": "/api/v2/galleries/42"}
        with self.assertRaises(nhentai.HTTPError) as caught:
            nhentai._search_page(self.session, 1)
        self.assertEqual(caught.exception.status_code, 302)
        self.transport.assert_called_once()
        response.close.assert_called_once_with()
        response.json.assert_not_called()

    def test_nhentai_detail_redirect_to_disallowed_host_is_still_http_error(self):
        self.transport.side_effect = None
        response = self.transport.return_value
        response.status_code = 307
        response.headers = {"Location": "https://example.com/api/v2/galleries/42"}
        with self.assertRaises(nhentai.HTTPError) as caught:
            nhentai._detail(self.session, "42")
        self.assertEqual(caught.exception.status_code, 307)
        self.transport.assert_called_once()
        response.close.assert_called_once_with()

    def test_nhentai_thumbnail_filter_uses_shared_policy(self):
        for url in (
            NH_THUMB_URL, "https://T.NHENTAI.NET:443/galleries/42/thumb.Jp2",
            "https://t.nhentai.net/galleries/42/thumb.jpg.exe",
            "https://t.nhentai.net/galleries/id/thumb.jpg",
            "https://t.nhentai.net/galleries/42/1.jpg",
            NH_THUMB_URL + "/extra", NH_THUMB_URL + "?x=1", NH_THUMB_URL + "#cover", NH_THUMB_URL.replace("https:", "http:"),
            NH_THUMB_URL.replace("t.nhentai.net", "i.nhentai.net"),
        ):
            with self.subTest(url=url):
                try:
                    net_policy.validate_url(url, purpose="nhentai_thumb")
                except net_policy.URLPolicyError:
                    self.assertFalse(cache_nhentai.valid_url(url))
                else:
                    self.assertTrue(cache_nhentai.valid_url(url))

    def test_retired_pururin_reuse_rejects_even_an_otherwise_allowed_url(self):
        with self.assertRaises(net_policy.URLPolicyError):
            pururin._fetch_detail(self.session, {"url": PAGE_URL})
        self.transport.assert_not_called()
        self.direct_get.assert_not_called()


    def test_retired_pururin_script_fails_before_creating_session(self):
        with patch.object(sys, "path", sys.path[:]), patch.object(pururin.requests, "Session") as make_session:
            with self.assertRaisesRegex(net_policy.URLPolicyError, "Unknown outbound purpose: pururin"):
                runpy.run_path(pururin.__file__, run_name="__main__")
        make_session.assert_not_called()
