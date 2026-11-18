import json
import socket
import unittest
from unittest.mock import Mock, patch

from requests import Response, Session
from requests.structures import CaseInsensitiveDict

from net_test_helpers import recording_session
from sources import pururin
from sources.common import safe_get
from sources.net_policy import (
    URLPolicyError,
    _PinnedAdapter,
    _PinnedHTTPConnection,
    _PinnedHTTPSConnection,
    guarded_request,
    validate_url,
)


def response(status=200, location=None):
    result = Mock(spec=Response, status_code=status, raw=None)
    result.headers = CaseInsensitiveDict()
    if location is not None:
        result.headers["location"] = location
    return result


ASM_IMAGE_URL = "https://images.asmhentai.com/018/636218/1t.jpg"


class URLPolicyTest(unittest.TestCase):
    def test_allowlisted_urls(self):
        cases = {
            "ehentai_list": ["https://e-hentai.org/"],
            "ehentai_api": ["https://api.e-hentai.org/"],
            "hitomi_resource": ["https://ltn.gold-usergeneratedcontent.net/", "https://ltn.hitomi.la/"],
            "hitomi_html": ["https://hitomi.la/"],
            "hentai3_page": ["https://3hentai.net/"],
            "hentai3_image": [f"https://s{i}.3hentai.xyz/" for i in (1, 4, 9)],
            "asmhentai_image": [ASM_IMAGE_URL],
            "nhentai_api": ["https://nhentai.net/api/v2/search?query=language%3Ajapanese&sort=date&page=1", "https://nhentai.net/api/v2/galleries/42"],
            "nhentai_thumb": ["https://t.nhentai.net/galleries/42/thumb.Jp2"],
            "local_api": [
                f"http://127.0.0.1:3000{path}" for path in
                ("/", "/3hentai/search", "/3hentai/get", "/asmhentai/search", "/asmhentai/get")
            ],
        }
        for purpose, urls in cases.items():
            for url in urls:
                with self.subTest(purpose=purpose, url=url):
                    self.assertEqual(validate_url(url, purpose=purpose), url)
            with self.subTest(purpose=purpose, url="https://example.org/"), self.assertRaises(URLPolicyError):
                validate_url("https://example.org/", purpose=purpose)

    def test_host_normalization(self):
        self.assertEqual(
            validate_url("HTTPS://Ｅ-HENTAI。ORG.:443/?q=one#ignored", purpose="ehentai_list"),
            "https://e-hentai.org/?q=one",
        )

    def test_rejected_targets_never_send(self):
        cases = [
            ("hentai3_image", "https://s2.3hentai.xyz/"),
            ("nhentai_thumb", "https://i.nhentai.net/galleries/42/thumb.jpg"),
            ("hitomi_resource", "https://tn.hitomi.la/"),
            ("ehentai_list", "https://ehgt.org/"),
            ("asmhentai_image", "https://asmhentai.com/"),
            ("asmhentai_image", "https://s2.3hentai.xyz/"),
            ("asmhentai_image", "https://i.nhentai.net/"),
            ("asmhentai_image", "https://sub.images.asmhentai.com/"),
            ("asmhentai_image", "https://images.asmhentai.com.example.org/"),
            ("asmhentai_image", "http://images.asmhentai.com/"),
            ("asmhentai_image", "https://images.asmhentai.com:444/"),
            ("asmhentai_image", "https://user:password@images.asmhentai.com/"),
            ("hitomi_node", "https://hitomi.la/"),
            ("pururin", "https://pururin.me/"),
            ("nharchive", "https://nharchive.org/"),
            ("nhentai_api", "https://nhentai.net/api/v2/gallery/42"),
            ("nhentai_api", "https://nhentai.net/api/v2/galleries/%34%32"),
            ("nhentai_thumb", "https://t.nhentai.net/galleries/42/thumb.jpg/more"),
            ("ehentai_list", "https://sub.e-hentai.org/"),
            ("ehentai_list", "https://e-hentai.org.example/"),
            ("ehentai_list", "https://user:password@e-hentai.org/"),
            ("ehentai_list", "https://e-hentai.org:444/"),
            ("ehentai_list", "https://e-hentai.org:/"),
            ("ehentai_list", "https://8.8.8.8/"),
            ("ehentai_list", "https://[2606:4700::1111]/"),
            ("ehentai_list", "https:///e-hentai.org"),
            ("ehentai_list", "https://e-hentai.org/\npage"),
            ("ehentai_list", "https://e-hentai.org/%xx"),
            ("ehentai_list", "https://e-hentai.org\\@example.org/"),
            ("ehentai_list", "http://e-hentai.org/"),
        ]
        for purpose, url in cases:
            with self.subTest(purpose=purpose, url=url):
                session, transport = recording_session()
                with patch.object(session, "mount", wraps=session.mount) as mount:
                    with self.assertRaises(URLPolicyError):
                        guarded_request(session, "GET", url, purpose=purpose)
                transport.assert_not_called()
                mount.assert_not_called()

    def test_local_authority_and_path_boundaries(self):
        urls = [
            f"http://{host}:3000/" for host in (
                "localhost", "127.0.0.2", "[::1]", "2130706433", "0x7f000001",
                "0177.0.0.1", "127.1", "127.0.0.1.", "192.168.1.1", "user@127.0.0.1",
            )
        ] + [
            "https://127.0.0.1:3000/", "http://127.0.0.1/", "http://127.0.0.1:03000/",
            "http://127.0.0.1:3001/", "http://127.0.0.1:3000/3hentai/search/",
            "http://127.0.0.1:3000/3hentai/%67et",
        ]
        for url in urls:
            with self.subTest(url=url), self.assertRaises(URLPolicyError):
                validate_url(url, purpose="local_api")

    def test_retired_source_helpers_fail_before_transport(self):
        session, transport = recording_session()
        for operation, arg in ((pururin._discover_page, 1), (pururin._fetch_detail, {"url": "https://pururin.me/"})):
            with self.subTest(operation=operation.__name__), self.assertRaises(URLPolicyError):
                operation(session, arg)
        transport.assert_not_called()
        with patch.object(pururin.requests, "Session") as make_session, self.assertRaises(URLPolicyError):
            pururin.collect()
        make_session.assert_not_called()

    def test_common_http_error_keeps_status_and_final_url(self):
        session, transport = recording_session()
        result = Response()
        result.status_code = 429
        result.url = "https://e-hentai.org/limit"
        result._content = b"<title>Rate limited</title>"
        result.headers["Content-Type"] = "text/html"
        transport.return_value = result
        with self.assertRaisesRegex(
            RuntimeError, r"HTTP 429 url=https://e-hentai\.org/ final=https://e-hentai\.org/limit size=27 title='Rate limited'",
        ):
            safe_get(session, "https://e-hentai.org/", purpose="ehentai_list")


class PreparedRequestPolicyTest(unittest.TestCase):
    search = "https://nhentai.net/api/v2/search"
    detail = "https://nhentai.net/api/v2/galleries/42"
    thumb = "https://t.nhentai.net/galleries/42/thumb.jpg"
    search_query = "query=language%3Ajapanese&sort=date&page=1"

    def test_non_session_is_rejected_before_mount_or_request(self):
        for session in (Mock(), Mock(spec=Session)):
            with self.subTest(session=session), patch.object(_PinnedAdapter, "send") as send:
                with self.assertRaisesRegex(URLPolicyError, "requests.Session"):
                    guarded_request(session, "GET", self.detail, purpose="nhentai_api")
                session.mount.assert_not_called()
                session.request.assert_not_called()
                send.assert_not_called()

    def test_search_accepts_decoded_values_from_url_and_params(self):
        session, send = recording_session()
        send.return_value = response()
        urls_and_params = (
            (self.search + "?page=002&sort=date&query=language%3ajapanese", None),
            (self.search + "?query=language:japanese", {"sort": "date", "page": 2}),
        )
        for url, params in urls_and_params:
            with self.subTest(url=url, params=params):
                send.reset_mock()
                guarded_request(session, "get", url, purpose="nhentai_api", params=params)
                send.assert_called_once()
                self.assertEqual(send.call_args.args[0].method, "GET")

    def test_search_rejects_invalid_or_repeated_query_before_send(self):
        session, send = recording_session()
        cases = [
            ("", None),
            ("?query=language:japanese&sort=date", None),
            ("?query=language:english&sort=date&page=1", None),
            ("?query=language:japanese&sort=popular&page=1", None),
            ("?" + self.search_query + "&extra=1", None),
            ("?" + self.search_query + "&extra", None),
            ("?" + self.search_query, {"page": 2}),
            ("?" + self.search_query, {"query": "language:japanese"}),
        ] + [
            ("?query=language:japanese&sort=date&page=" + page, None)
            for page in ("", "0", "000", "-1", "%2B1", "1.5", "%EF%BC%91")
        ]
        for query, params in cases:
            with self.subTest(query=query, params=params), self.assertRaises(URLPolicyError):
                guarded_request(session, "GET", self.search + query, purpose="nhentai_api", params=params)
        send.assert_not_called()

    def test_detail_and_thumbnail_reject_query_in_url_or_params(self):
        for purpose, url in (("nhentai_api", self.detail), ("nhentai_thumb", self.thumb)):
            session, send = recording_session()
            for target, params in ((url + "?x=1", None), (url, {"x": ""})):
                with self.subTest(purpose=purpose, target=target, params=params):
                    with self.assertRaises(URLPolicyError):
                        guarded_request(session, "GET", target, purpose=purpose, params=params)
            send.assert_not_called()

    def test_nhentai_purposes_reject_non_get_methods(self):
        session, send = recording_session()
        for purpose, url in (
            ("nhentai_api", self.search + "?" + self.search_query),
            ("nhentai_api", self.detail),
            ("nhentai_thumb", self.thumb),
        ):
            for method in ("HEAD", "POST", "PUT"):
                with self.subTest(purpose=purpose, url=url, method=method):
                    with self.assertRaisesRegex(URLPolicyError, "Only GET"):
                        guarded_request(session, method, url, purpose=purpose)
        send.assert_not_called()


class RedirectTest(unittest.TestCase):
    def setUp(self):
        self.session, self.transport = recording_session()
        self.base = "https://e-hentai.org/dir/start"

    def send(self, method="GET", **kwargs):
        return guarded_request(self.session, method, self.base, purpose="ehentai_list", **kwargs)

    def test_asmhentai_image_redirect_within_allowed_host(self):
        target = "https://images.asmhentai.com/018/636218/2t.jpg"
        first = response(302, target)
        last = response()
        self.transport.side_effect = [first, last]

        result = guarded_request(self.session, "GET", ASM_IMAGE_URL, purpose="asmhentai_image")

        self.assertIs(result, last)
        self.assertEqual(
            [call.args[0].url for call in self.transport.call_args_list],
            [ASM_IMAGE_URL, target],
        )
        first.close.assert_called_once_with()

    def test_asmhentai_image_redirect_outside_allowed_host(self):
        first = response(302, "https://example.com/x.jpg")
        self.transport.return_value = first

        with self.assertRaises(URLPolicyError):
            guarded_request(self.session, "GET", ASM_IMAGE_URL, purpose="asmhentai_image")

        self.transport.assert_called_once()
        self.assertEqual(self.transport.call_args.args[0].url, ASM_IMAGE_URL)
        first.close.assert_called_once_with()

    def test_relative_redirect_preserves_head_and_sends_params_once(self):
        first = response(302, "next?new=2")
        last = response()
        self.transport.side_effect = [first, last]
        self.assertIs(self.send("HEAD", params={"old": 1}, stream=True), last)
        initial, followup = self.transport.call_args_list
        self.assertEqual((initial.args[0].method, initial.args[0].url), ("HEAD", self.base + "?old=1"))
        self.assertEqual((followup.args[0].method, followup.args[0].url),
                         ("HEAD", "https://e-hentai.org/dir/next?new=2"))
        first.close.assert_called_once_with()
        last.close.assert_not_called()

    def test_redirect_does_not_reapply_session_params(self):
        self.session.params = {"page": "1"}
        self.transport.side_effect = [response(302, "?page=2"), response()]
        self.send(stream=True)
        self.assertEqual(
            [call.args[0].url for call in self.transport.call_args_list],
            [self.base + "?page=1", self.base + "?page=2"],
        )
        self.assertEqual(self.session.params, {"page": "1"})

    def test_five_redirects_allowed_and_sixth_rejected(self):
        for overflow in (False, True):
            with self.subTest(overflow=overflow):
                redirects = [response(301, f"/hop/{i}") for i in range(5 + overflow)]
                last = response()
                self.transport.reset_mock()
                self.transport.side_effect = redirects + [last]
                if overflow:
                    with self.assertRaisesRegex(URLPolicyError, "limit"):
                        self.send(max_redirects=99)
                else:
                    self.assertIs(self.send(), last)
                self.assertEqual(self.transport.call_count, 6)
                for redirect in redirects:
                    redirect.close.assert_called_once_with()

    def test_caller_can_reduce_redirect_limit(self):
        redirect = response(302, "/next")
        self.transport.return_value = redirect
        with self.assertRaisesRegex(URLPolicyError, "limit"):
            self.send(max_redirects=0)
        self.transport.assert_called_once()
        redirect.close.assert_called_once_with()

    def test_cycle_downgrade_and_disallowed_hosts(self):
        for location in (
            "https://E-HENTAI.ORG.:443/dir/start#fragment", "http://e-hentai.org/next",
            "https://api.e-hentai.org/next", "//127.0.0.1:3000/", "https:next",
        ):
            with self.subTest(location=location):
                self.transport.reset_mock()
                redirect = response(302, location)
                self.transport.return_value = redirect
                with self.assertRaises(URLPolicyError):
                    self.send()
                self.transport.assert_called_once()
                redirect.close.assert_called_once_with()

    def test_no_redirect_purposes(self):
        cases = [
            ("local_api", "http://127.0.0.1:3000/", "/3hentai/get"),
            ("local_api", "http://127.0.0.1:3000/", "https://e-hentai.org/"),
            ("nhentai_api", "https://nhentai.net/api/v2/search?query=language%3Ajapanese&sort=date&page=1", "/api/v2/galleries/1"),
            ("nhentai_thumb", "https://t.nhentai.net/galleries/1/thumb.jpg", "/galleries/2/thumb.jpg"),
        ]
        for purpose, url, location in cases:
            with self.subTest(purpose=purpose, location=location):
                self.transport.reset_mock()
                first = response(302, location)
                self.transport.return_value = first
                with self.assertRaises(URLPolicyError):
                    guarded_request(self.session, "GET", url, purpose=purpose, max_redirects=5)
                self.transport.assert_called_once()
                first.close.assert_called_once_with()

    def test_get_can_follow_another_host_in_same_purpose(self):
        self.transport.side_effect = [response(308, "https://ltn.hitomi.la/next"), response()]
        guarded_request(self.session, "GET", "https://ltn.gold-usergeneratedcontent.net/start", purpose="hitomi_resource")
        prepared = self.transport.call_args.args[0]
        self.assertEqual((prepared.method, prepared.url), ("GET", "https://ltn.hitomi.la/next"))

    def test_post_307_and_308_keep_body_and_method(self):
        first, second, last = response(307, "/next"), response(308, "/final"), response()
        self.transport.side_effect = [first, second, last]
        payload = {"method": "gdata"}
        self.assertIs(self.send("POST", json=payload), last)
        self.assertEqual([call.args[0].method for call in self.transport.call_args_list], ["POST"] * 3)
        for call in self.transport.call_args_list:
            self.assertEqual(json.loads(call.args[0].body), payload)
        first.close.assert_called_once_with()
        second.close.assert_called_once_with()

    def test_post_rejects_method_switch_and_cross_origin(self):
        for status, location in [(301, "/next"), (302, "/next"), (303, "/next"), (307, "https://ltn.hitomi.la/")]:
            with self.subTest(status=status, location=location):
                self.transport.reset_mock()
                first = response(status, location)
                self.transport.return_value = first
                with self.assertRaisesRegex(URLPolicyError, "POST"):
                    guarded_request(
                        self.session, "POST", "https://ltn.gold-usergeneratedcontent.net/",
                        purpose="hitomi_resource", json={"value": 1},
                    )
                self.transport.assert_called_once()
                first.close.assert_called_once_with()


def address_record(address, port=443):
    if ":" in address:
        return (socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, port, 0, 0))
    return (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, port))


class TransportTest(unittest.TestCase):
    def test_mixed_dns_answers_rejected_before_socket_creation(self):
        public = address_record("93.184.216.34")
        for ip in ("127.0.0.1", "10.0.0.1", "169.254.1.1", "224.0.0.1", "240.0.0.1", "0.0.0.0",
                   "100.64.0.1", "::1", "fe80::1", "::ffff:8.8.8.8", "ff02::1"):
            with self.subTest(ip=ip), patch(
                "socket.getaddrinfo", return_value=[public, address_record(ip)],
            ) as lookup, patch("socket.socket") as make_socket:
                with self.assertRaisesRegex(URLPolicyError, "Non-public DNS"):
                    _PinnedHTTPSConnection("e-hentai.org", timeout=1)._new_conn()
                lookup.assert_called_once_with("e-hentai.org", 443, socket.AF_UNSPEC, socket.SOCK_STREAM)
                make_socket.assert_not_called()

    def test_real_session_rejects_non_public_dns_before_connecting(self):
        with Session() as session, patch(
            "socket.getaddrinfo", return_value=[address_record("127.0.0.1")],
        ) as lookup, patch("socket.socket") as make_socket:
            with self.assertRaisesRegex(URLPolicyError, "Non-public DNS"):
                guarded_request(session, "GET", "https://e-hentai.org/", purpose="ehentai_list", timeout=1)
        lookup.assert_called_once()
        make_socket.assert_not_called()

    def test_pool_connection_uses_validated_sockaddr_and_keeps_tls_hostname(self):
        adapter = _PinnedAdapter()
        connection = adapter.poolmanager.connection_from_url("https://e-hentai.org/")._new_conn()
        record = address_record("2606:4700::1111")
        with patch("socket.getaddrinfo", return_value=[record]) as lookup, patch("socket.socket") as make_socket:
            self.assertIs(connection._new_conn(), make_socket.return_value)
        lookup.assert_called_once_with("e-hentai.org", 443, socket.AF_UNSPEC, socket.SOCK_STREAM)
        make_socket.assert_called_once_with(*record[:3])
        make_socket.return_value.connect.assert_called_once_with(record[4])
        self.assertEqual(connection.host, "e-hentai.org")

    def test_local_connection_pins_ipv4_without_lookup(self):
        with patch("socket.getaddrinfo") as lookup, patch("socket.socket") as make_socket:
            _PinnedHTTPConnection("127.0.0.1", port=3000, timeout=1)._new_conn()
            with self.assertRaises(URLPolicyError):
                _PinnedHTTPConnection("127.0.0.2", port=3000, timeout=1)._new_conn()
        lookup.assert_not_called()
        make_socket.assert_called_once()
        make_socket.return_value.connect.assert_called_once_with(("127.0.0.1", 3000))

    def test_real_session_uses_mounted_adapter_without_proxy_or_host_override(self):
        first = response(302, "/next")
        last = response()
        first.raw = last.raw = Mock()
        first.raw._original_response = last.raw._original_response = None
        with patch.dict("os.environ", {"HTTPS_PROXY": "http://proxy.invalid:8080"}), Session() as session, patch.object(
            _PinnedAdapter, "send", side_effect=[first, last],
        ) as send:
            session.proxies["https"] = "http://proxy.invalid:8080"
            session.verify = False
            self.assertIs(guarded_request(
                session, "GET", "https://e-hentai.org/", purpose="ehentai_list",
                headers={"Host": "invalid.example"}, stream=True,
            ), last)
            self.assertIsInstance(session.get_adapter("https://e-hentai.org/"), _PinnedAdapter)
            self.assertFalse(session.trust_env)
        self.assertEqual([call.args[0].url for call in send.call_args_list], [
            "https://e-hentai.org/", "https://e-hentai.org/next",
        ])
        for call in send.call_args_list:
            self.assertEqual(call.kwargs["proxies"], {})
            self.assertTrue(call.kwargs["verify"])
            self.assertNotIn("Host", call.args[0].headers)
        first.close.assert_called_once_with()
