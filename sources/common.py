import json
import re
import time
import unicodedata
from datetime import datetime, timezone
from typing import Any, Iterable

import requests
from bs4 import BeautifulSoup

from config import REQUEST_TIMEOUT, USER_AGENT

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Language": "ja,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
}


def iso_from_unix(value: Any) -> str:
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).isoformat()
    except Exception:
        return ""


def normalize_tag(value: str) -> str:
    value = unicodedata.normalize("NFKC", str(value or "")).strip().lower()
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


def safe_get(session: requests.Session, url: str, *, params=None, headers=None, timeout=None) -> requests.Response:
    merged = dict(HEADERS)
    if headers:
        merged.update(headers)
    res = session.get(url, params=params, headers=merged, timeout=timeout or REQUEST_TIMEOUT)
    res.raise_for_status()
    return res


def safe_post_json(session: requests.Session, url: str, payload: dict, *, headers=None, timeout=None) -> dict:
    merged = dict(HEADERS)
    merged["Content-Type"] = "application/json"
    if headers:
        merged.update(headers)
    res = session.post(url, json=payload, headers=merged, timeout=timeout or REQUEST_TIMEOUT)
    res.raise_for_status()
    return res.json()


def parse_json_wrapped_js(text: str) -> dict:
    """Parse JS files such as `var galleryinfo = {...};` defensively."""
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
