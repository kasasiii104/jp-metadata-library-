"""Outbound URL policy and DNS-pinned transport for server-side fetches."""

import ipaddress
import re
import socket
from dataclasses import dataclass
from urllib.parse import parse_qsl, urljoin, urlsplit, urlunsplit

import idna
from requests import Request, Session
from requests.adapters import HTTPAdapter
from requests.cookies import extract_cookies_to_jar
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
from urllib3.exceptions import ConnectTimeoutError, NameResolutionError, NewConnectionError


class URLPolicyError(RuntimeError):
    """The requested URL, redirect, or connection is outside the policy."""


_MAX_REDIRECTS = 5
_LOCAL_HOST = "127.0.0.1"
_LOCAL_AUTHORITY = f"{_LOCAL_HOST}:3000"
_LOCAL_PATHS = {"/", "/3hentai/search", "/3hentai/get", "/asmhentai/search", "/asmhentai/get"}


@dataclass(frozen=True)
class _Policy:
    hosts: frozenset[str]
    path: str | None = None
    redirects: int = _MAX_REDIRECTS


_POLICIES = {
    "ehentai_list": _Policy(frozenset({"e-hentai.org"})),
    "ehentai_api": _Policy(frozenset({"api.e-hentai.org"})),
    "hitomi_resource": _Policy(frozenset({"ltn.gold-usergeneratedcontent.net", "ltn.hitomi.la"})),
    "hitomi_html": _Policy(frozenset({"hitomi.la"})),
    "hentai3_page": _Policy(frozenset({"3hentai.net"})),
    "hentai3_image": _Policy(frozenset({"s1.3hentai.xyz", "s4.3hentai.xyz", "s9.3hentai.xyz"})),
    "asmhentai_image": _Policy(frozenset({"images.asmhentai.com"})),
    "nhentai_api": _Policy(frozenset({"nhentai.net"}), r"/api/v2/(?:search|galleries/[0-9]+)", 0),
    "nhentai_thumb": _Policy(frozenset({"t.nhentai.net"}), r"/galleries/[0-9]+/thumb\.[A-Za-z0-9.]+", 0),
    "local_api": _Policy(frozenset({_LOCAL_HOST}), redirects=0),
}


def _split_url(url):
    # Check before urlsplit can strip control characters.
    target = url.partition("#")[0]
    if re.search(r"[\x00-\x20\x7f\\]|%(?![0-9a-fA-F]{2})", target):
        raise URLPolicyError("Malformed URL")
    try:
        return urlsplit(target)
    except ValueError as exc:
        raise URLPolicyError("Malformed URL") from exc


def validate_url(url: str, *, purpose: str) -> str:
    """Return the canonical URL to send, without a fragment."""
    policy = _POLICIES.get(purpose)
    if policy is None:
        raise URLPolicyError(f"Unknown outbound purpose: {purpose}")
    parts = _split_url(url)
    path = parts.path or "/"
    if purpose == "local_api":
        if parts.scheme != "http" or parts.netloc != _LOCAL_AUTHORITY or path not in _LOCAL_PATHS:
            raise URLPolicyError("URL outside local_api policy")
        return urlunsplit(("http", _LOCAL_AUTHORITY, path, parts.query, ""))

    if parts.scheme != "https" or not parts.hostname or any(c in parts.netloc for c in "@[]"):
        raise URLPolicyError(f"URL outside {purpose} policy")
    try:
        hostname, separator, port = parts.netloc.partition(":")
        if separator and port != "443":
            raise URLPolicyError("Only HTTPS port 443 is allowed")
        hostname = idna.encode(hostname, uts46=True, std3_rules=True).decode("ascii").removesuffix(".")
    except (UnicodeError, ValueError) as exc:
        raise URLPolicyError("Invalid HTTPS hostname") from exc
    if hostname not in policy.hosts or (policy.path and not re.fullmatch(policy.path, path)):
        raise URLPolicyError(f"URL outside {purpose} policy")
    canonical = urlunsplit(("https", hostname, path, parts.query, ""))
    if purpose == "nhentai_thumb" and (parts.query or url != canonical):
        raise URLPolicyError("URL outside nhentai_thumb policy")
    return canonical


def local_api_url(base: str, path: str) -> str:
    if base != f"http://{_LOCAL_AUTHORITY}":
        raise URLPolicyError("URL outside local_api policy")
    return base + path


def _public_address(address):
    ip = ipaddress.ip_address(address)
    return (
        ip.is_global
        and not (ip.is_multicast or ip.is_reserved or ip.is_link_local or ip.is_unspecified or ip.is_loopback)
        and not (isinstance(ip, ipaddress.IPv6Address) and (ip.ipv4_mapped or ip.is_site_local))
    )


class _PinnedConnection:
    def _new_conn(self):
        try:
            if self._local_only:
                if (self.host, self.port) != (_LOCAL_HOST, 3000):
                    raise URLPolicyError("HTTP connection outside local_api policy")
                addresses = [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (_LOCAL_HOST, 3000))]
            else:
                addresses = socket.getaddrinfo(self.host, self.port, socket.AF_UNSPEC, socket.SOCK_STREAM)
                # Reject mixed answers before opening any socket.
                if any(not _public_address(address[0]) for _, _, _, _, address in addresses):
                    raise URLPolicyError(f"Non-public DNS answer for {self.host}")
            error = OSError("No addresses returned")
            for family, kind, protocol, _, address in addresses:
                sock = None
                try:
                    sock = socket.socket(family, kind, protocol)
                    sock.settimeout(self.timeout)
                    for option in self.socket_options or ():
                        sock.setsockopt(*option)
                    if self.source_address:
                        sock.bind(self.source_address)
                    sock.connect(address)
                    return sock
                except OSError as exc:
                    if sock is not None:
                        sock.close()
                    error = exc
                except BaseException:
                    if sock is not None:
                        sock.close()
                    raise
            raise error
        except socket.gaierror as exc:
            raise NameResolutionError(self.host, self, exc) from exc
        except TimeoutError as exc:
            raise ConnectTimeoutError(self, f"Connection to {self.host} timed out") from exc
        except OSError as exc:
            raise NewConnectionError(self, f"Failed to establish a connection: {exc}") from exc


class _PinnedHTTPConnection(_PinnedConnection, HTTPConnection):
    _local_only = True


class _PinnedHTTPSConnection(_PinnedConnection, HTTPSConnection):
    _local_only = False


class _PinnedHTTPPool(HTTPConnectionPool):
    ConnectionCls = _PinnedHTTPConnection


class _PinnedHTTPSPool(HTTPSConnectionPool):
    ConnectionCls = _PinnedHTTPSConnection


class _PinnedAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        super().init_poolmanager(*args, **kwargs)
        self.poolmanager.pool_classes_by_scheme = {
            "http": _PinnedHTTPPool,
            "https": _PinnedHTTPSPool,
        }

    def proxy_manager_for(self, proxy, **proxy_kwargs):
        raise URLPolicyError("Proxies are disabled for pinned connections")


def _mount_adapter(session, url):
    prefix = urlsplit(url).scheme + "://"
    adapter = session.adapters.get(prefix)
    if type(adapter) is not _PinnedAdapter:
        adapter = _PinnedAdapter()
        session.mount(prefix, adapter)
    return adapter


def _validate_prepared_request(prepared, purpose):
    if purpose not in {"nhentai_api", "nhentai_thumb"}:
        return
    if prepared.method != "GET":
        raise URLPolicyError(f"Only GET is allowed for {purpose}")
    parts = urlsplit(prepared.url)
    if purpose == "nhentai_api" and parts.path == "/api/v2/search":
        try:
            pairs = parse_qsl(parts.query, keep_blank_values=True, strict_parsing=True, errors="strict")
        except ValueError as exc:
            raise URLPolicyError("Invalid nhentai search query") from exc
        values = dict(pairs)
        if (
            len(pairs) == 3
            and values.keys() == {"query", "sort", "page"}
            and values["query"] == "language:japanese"
            and values["sort"] == "date"
            and re.fullmatch(r"0*[1-9][0-9]*", values["page"])
        ):
            return
        raise URLPolicyError("Invalid nhentai search query")
    if parts.query:
        raise URLPolicyError(f"Query forbidden for {purpose}")


def guarded_request(
    session, method, url, *, purpose, params=None, headers=None, json=None,
    timeout=None, stream=False, max_redirects=None, follow_redirects=True,
):
    """Send a request and validate each redirect under the original purpose.

    With follow_redirects=False the caller receives and owns the 3xx response.
    """
    if not issubclass(type(session), Session):
        raise URLPolicyError("A requests.Session is required")
    url = validate_url(url, purpose=purpose)
    limit = _POLICIES[purpose].redirects
    if max_redirects is not None:
        if not isinstance(max_redirects, int) or max_redirects < 0:
            raise ValueError("max_redirects must be a nonnegative integer")
        limit = min(limit, max_redirects)
    method = method.upper()
    adapter = _mount_adapter(session, url)
    session.trust_env = False
    initial_origin = urlsplit(url).netloc
    visited = set()

    for hop in range(limit + 1):
        # A string bypasses Requests' mapping merge with session.params.
        request = Request(method, url, params=params if hop == 0 else "", headers=headers, json=json)
        prepared = session.prepare_request(request)
        prepared.url = validate_url(prepared.url, purpose=purpose)
        _validate_prepared_request(prepared, purpose)
        if prepared.url in visited:
            raise URLPolicyError("Redirect cycle")
        visited.add(prepared.url)

        prepared.headers.pop("Host", None)
        if urlsplit(prepared.url).netloc != initial_origin:
            prepared.headers.pop("Authorization", None)
            prepared.headers.pop("Cookie", None)
        # Session.send prepares a redirect even with allow_redirects=False,
        # consuming its body. The adapter sends exactly one hop.
        response = adapter.send(
            prepared, timeout=timeout, stream=True, verify=session.verify or True,
            cert=session.cert, proxies={},
        )
        extract_cookies_to_jar(session.cookies, prepared, response.raw)

        status = response.status_code
        if not 300 <= status < 400:
            if not stream:
                try:
                    response.content
                except BaseException:
                    response.close()
                    raise
            return response

        if not follow_redirects:
            return response
        location = response.headers.get("Location")
        response.close()
        if hop == limit:
            raise URLPolicyError("Redirect limit exceeded")
        if not location or status not in {301, 302, 303, 307, 308}:
            raise URLPolicyError("Unsupported redirect")
        location_parts = _split_url(location)
        if (location_parts.scheme and not location_parts.netloc) or location.startswith("///"):
            raise URLPolicyError("Malformed redirect location")
        next_url = validate_url(urljoin(prepared.url, location), purpose=purpose)
        if method not in {"GET", "HEAD"} and not (
            method == "POST" and json is not None and status in {307, 308}
            and urlsplit(next_url).netloc == urlsplit(prepared.url).netloc
        ):
            raise URLPolicyError(f"Redirect forbidden for {method}")
        url = next_url

    raise AssertionError("Redirect loop must return or raise")
