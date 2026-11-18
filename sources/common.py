import json
import logging
import re
import time
import unicodedata
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Iterable

import requests
from bs4 import BeautifulSoup

from config import REQUEST_TIMEOUT, USER_AGENT
from sources.net_policy import URLPolicyError, guarded_request

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Language": "ja,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
}


def retry_window(keys, limit, now=None, period_sec=3600) -> list:
    """Rotate retries despite zero-success runs, without adding saved state."""
    if limit <= 0 or not keys:
        return []
    if len(keys) <= limit:
        return list(keys)
    if now is None:
        now = time.time()
    start = (int(now // period_sec) * limit) % len(keys)
    return [keys[(start + offset) % len(keys)] for offset in range(limit)]


def is_valid_source_id(value: str, *, source: str = "") -> bool:
    """Accept ASCII IDs without path/URL syntax; preserve opaque API IDs."""
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]+", value):
        return True
    logging.getLogger(__name__).warning("[%s] skipping invalid source_id %r", source, value)
    return False


def iso_from_unix(value: Any) -> str:
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).isoformat()
    except Exception:
        return ""


def normalize_tag(value: str) -> str:
    value = unicodedata.normalize("NFKC", str(value or "")).strip().lower()
    value = value.replace("’", "'").replace("‘", "'")
    value = value.replace("_", " ").replace("-", " ")
    value = re.sub(r"\s+", " ", value)
    return value


def normalize_full_tag(value: str) -> str:
    raw = unicodedata.normalize("NFKC", str(value or "")).strip().lower()
    if ":" in raw:
        ns, name = raw.split(":", 1)
        return f"{normalize_tag(ns)}:{normalize_tag(name)}"
    return normalize_tag(raw)


def unique_strings(values: Iterable[Any]) -> list[str]:
    out: list[str] = []
    seen = set()
    for value in values or []:
        s = str(value or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def absolute_url(url: str, scheme: str = "https:") -> str:
    if not url:
        return ""
    if url.startswith("//"):
        return scheme + url
    return url


def _response_title(res: requests.Response) -> str:
    content_type = (res.headers.get("content-type") or "").lower()
    if "html" not in content_type:
        return ""
    try:
        soup = BeautifulSoup(res.text[:200000], "html.parser")
        if soup.title:
            return soup.title.get_text(" ", strip=True)[:160]
    except Exception:
        pass
    return ""


def stop_on_url_policy_error(collect):
    """Preserve the input state when a source cannot complete safely."""
    @wraps(collect)
    def run(state=None):
        try:
            return collect(state)
        except URLPolicyError as exc:
            return [], dict(state or {}), {
                "status": "error", "partial": True, "discovered": 0,
                "accepted_raw": 0, "message": str(exc),
            }

    return run


def safe_get(session: requests.Session, url: str, *, purpose: str, params=None, headers=None, timeout=None) -> requests.Response:
    merged = dict(HEADERS)
    if headers:
        merged.update(headers)
    try:
        res = guarded_request(
            session, "GET", url, purpose=purpose, params=params,
            headers=merged, timeout=timeout or REQUEST_TIMEOUT,
        )
    except URLPolicyError:
        raise
    except Exception as e:
        raise RuntimeError(f"request failed url={url}: {e}") from e
    if not res.ok:
        title = _response_title(res)
        raise RuntimeError(
            f"HTTP {res.status_code} url={url} final={res.url} size={len(res.content)}"
            + (f" title={title!r}" if title else "")
        )
    return res


def safe_post_json(session: requests.Session, url: str, payload: dict, *, purpose: str, headers=None, timeout=None) -> dict:
    merged = dict(HEADERS)
    merged["Content-Type"] = "application/json"
    if headers:
        merged.update(headers)
    try:
        res = guarded_request(
            session, "POST", url, purpose=purpose, json=payload,
            headers=merged, timeout=timeout or REQUEST_TIMEOUT,
        )
    except URLPolicyError:
        raise
    except Exception as e:
        raise RuntimeError(f"request failed url={url}: {e}") from e
    if not res.ok:
        title = _response_title(res)
        raise RuntimeError(
            f"HTTP {res.status_code} url={url} final={res.url} size={len(res.content)}"
            + (f" title={title!r}" if title else "")
        )
    return res.json()


def parse_json_wrapped_js(text: str) -> dict:
    """Parse JavaScript payloads such as `var galleryinfo = {...};`."""
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("JSON object not found in JavaScript payload")
    return json.loads(text[start : end + 1])


def soup_from_response(res: requests.Response) -> BeautifulSoup:
    return BeautifulSoup(res.text, "html.parser")


def polite_sleep(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)
