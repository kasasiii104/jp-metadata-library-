from typing import Any
from urllib.parse import quote
from datetime import datetime, timezone

import requests

from config import (NH_BACKFILL_PAGES, NH_LATEST_PAGES, NH_MAX_DETAILS_PER_RUN,
                    NH_REQUEST_DELAY_SEC, REQUEST_TIMEOUT)
from sources.common import HEADERS, iso_from_unix, polite_sleep, unique_strings

BASE = "https://nhentai.net"
SEARCH_ENDPOINTS = [
    BASE + "/api/v2/search?query={query}&sort=date&page={page}",
    BASE + "/api/galleries/search?query={query}&sort=date&page={page}",
]
DETAIL_ENDPOINTS = [
    BASE + "/api/gallery/{gid}",
    BASE + "/api/v2/gallery/{gid}",
]

EXTS = {"j": "jpg", "p": "png", "g": "gif", "w": "webp", "a": "avif"}
FILTER_METADATA_VERSION = "nhentai-api-v1"


class FetchError(RuntimeError):
    def __init__(self, attempts: list[dict]):
        self.attempts = [dict(x) for x in attempts]
        super().__init__(" | ".join(
            f"{x['url']}: HTTP {x.get('http_status', '-')} {x['reason']}" for x in attempts
        ))


def _json_from_endpoints(session, urls, select, attempts):
    """Retain each attempt; only a missing endpoint permits an API fallback.

    No redirects, browser impersonation, challenge solving or cookie export.
    Diagnostics never contain response bodies, titles, cookies or credentials.
    """
    start = len(attempts)
    for url in urls:
        if attempts:
            polite_sleep(NH_REQUEST_DELAY_SEC)
        attempt = {"url": url}
        attempts.append(attempt)
        try:
            response = session.get(url, headers={**HEADERS, "Accept": "application/json"},
                                   timeout=REQUEST_TIMEOUT, allow_redirects=False)
        except requests.RequestException as exc:
            attempt.update(reason="transport_error", error_type=type(exc).__name__)
            break
        try:
            code = response.status_code
            mime = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
            challenge = response.headers.get("cf-mitigated", "").lower() == "challenge"
            attempt.update(http_status=code, content_type=mime,
                           challenge=challenge, login_redirect=False)
            if response.headers.get("Retry-After"):
                attempt["retry_after"] = response.headers["Retry-After"][:80]
            if challenge or code in {401, 403, 429}:
                attempt["reason"] = "challenge" if challenge else f"http_{code}"
                break
            if 300 <= code < 400:
                # Record only the classification, never redirect query tokens.
                attempt["login_redirect"] = any(
                    x in response.headers.get("Location", "").lower() for x in ("login", "signin", "auth")
                )
                attempt["reason"] = "redirect"
                break
            if code in {404, 410}:
                attempt["reason"] = f"http_{code}"
                continue
            if not 200 <= code < 300:
                attempt["reason"] = f"http_{code}"
                break
            try:
                payload = response.json()
            except ValueError:
                body = response.text[:4096].lower()
                attempt["reason"] = ("site_unavailable" if "site unavailable" in body
                                     else "non_json_response")
                break
            selected = select(payload)
            if selected is None:
                attempt["reason"] = "invalid_schema_or_identity"
                break
            attempt["reason"] = "ok"
            return selected, url
        finally:
            response.close()
    raise FetchError(attempts[start:])


def _results(payload: Any) -> list[dict[str, Any]] | None:
    if not isinstance(payload, dict) or payload.get("error"):
        return None
    for key in ("result", "results", "galleries", "items"):
        value = payload.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
    data = payload.get("data")
    if isinstance(data, dict):
        return _results(data)
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    return None


def _search_page(session: requests.Session, page: int, attempts=None) -> tuple[list[dict], str]:
    q = quote("language:japanese", safe="")
    return _json_from_endpoints(session,
        [x.format(query=q, page=page) for x in SEARCH_ENDPOINTS], _results,
        attempts if attempts is not None else [])


def _gallery(payload: Any, gid: str) -> dict | None:
    for _ in range(3):
        if not isinstance(payload, dict) or payload.get("error"):
            return None
        if "id" in payload:
            return payload if str(payload["id"]) == gid else None
        payload = payload.get("gallery", payload.get("data"))
    return None


def _detail(session: requests.Session, gid: str, attempts=None) -> dict[str, Any]:
    raw, _ = _json_from_endpoints(session,
        [x.format(gid=gid) for x in DETAIL_ENDPOINTS], lambda x: _gallery(x, gid),
        attempts if attempts is not None else [])
    return raw


def _tag_map(tags: list[dict]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for tag in tags or []:
        if not isinstance(tag, dict):
            continue
        typ = str(tag.get("type") or "tag").strip().lower()
        name = str(tag.get("name") or "").strip()
        if not name:
            continue
        out.setdefault(typ, []).append(name)
    for key in list(out):
        out[key] = unique_strings(out[key])
    return out


def _thumb_from_detail(raw: dict[str, Any], discovered_thumb: str = "") -> str:
    if discovered_thumb:
        return discovered_thumb
    media_id = str(raw.get("media_id") or "").strip()
    if not media_id:
        return ""
    images = raw.get("images") if isinstance(raw.get("images"), dict) else {}
    thumb = images.get("thumbnail") if isinstance(images.get("thumbnail"), dict) else {}
    ext = EXTS.get(str(thumb.get("t") or "j").lower(), "jpg")
    return f"https://t.nhentai.net/galleries/{media_id}/thumb.{ext}"


def _extract_discovery_thumb(raw: dict[str, Any]) -> str:
    thumb = raw.get("thumbnail")
    if isinstance(thumb, dict):
        return str(thumb.get("s") or thumb.get("url") or "")
    if isinstance(thumb, str):
        return thumb
    return ""


def _normalize(raw: dict[str, Any], discovered_thumb: str = "") -> dict[str, Any] | None:
    gid = str(raw.get("id") or "").strip()
    if not gid.isascii() or not gid.isdigit() or len(gid) > 12 or int(gid) <= 0:
        return None
    titles = raw.get("title") if isinstance(raw.get("title"), dict) else {}
    if not any(isinstance(titles.get(k), str) and titles[k].strip()
               for k in ("english", "pretty", "japanese")):
        return None
    tags = raw.get("tags")
    if not isinstance(tags, list) or not all(
        isinstance(x, dict) and isinstance(x.get("name"), str) and x["name"].strip()
        and isinstance(x.get("type"), str) and x["type"].strip() for x in tags
    ):
        return None
    tagmap = _tag_map(raw.get("tags") or [])
    languages = {x.lower() for x in tagmap.get("language", [])}
    language = "japanese" if "japanese" in languages else ""
    category = (tagmap.get("category") or [""])[0]
    pure_tags = unique_strings(tagmap.get("tag", []))
    if not languages or not pure_tags:
        return None
    def count(value):
        try:
            return max(0, int(value or 0))
        except (ValueError, TypeError):
            return 0
    try:
        posted_at = iso_from_unix(raw["upload_date"]) if raw.get("upload_date") else ""
    except (ValueError, TypeError, OverflowError, OSError):
        posted_at = ""

    return {
        "uid": f"nhentai:{gid}",
        "source": "nhentai",
        "source_id": gid,
        "source_url": f"https://nhentai.net/g/{gid}/",
        "title": str(titles.get("english") or titles.get("pretty") or titles.get("japanese") or "").strip(),
        "title_jp": str(titles.get("japanese") or "").strip(),
        "language": language,
        "category": category,
        "artists": unique_strings(tagmap.get("artist", [])),
        "groups": unique_strings(tagmap.get("group", [])),
        "parodies": unique_strings(tagmap.get("parody", [])),
        "characters": unique_strings(tagmap.get("character", [])),
        "tags": pure_tags,
        "pages": count(raw.get("num_pages")),
        "rating": None,
        "popularity": count(raw.get("num_favorites")) if raw.get("num_favorites") is not None else None,
        "posted_at": posted_at,
        "thumbnail": _thumb_from_detail(raw, discovered_thumb),
        "filter_metadata_checked": FILTER_METADATA_VERSION,
        "filter_metadata_checked_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }


def collect(state: dict | None = None, *, latest_pages=None, backfill_pages=None,
            max_details=None, max_items=None) -> tuple[list[dict], dict, dict]:
    state = dict(state or {})
    latest_pages = NH_LATEST_PAGES if latest_pages is None else latest_pages
    backfill_pages = NH_BACKFILL_PAGES if backfill_pages is None else backfill_pages
    max_details = NH_MAX_DETAILS_PER_RUN if max_details is None else max_details
    backfill_page = int(state.get("backfill_page") or (latest_pages + 1))
    errors: list[str] = []
    attempts: list[dict] = []
    discovered: list[dict] = []
    seen = set()
    items: list[dict] = []
    details = unverified = search_metadata_used = 0
    stopped = truncated = False
    pages = list(range(1, latest_pages + 1)) + list(range(backfill_page, backfill_page + backfill_pages))
    with requests.Session() as session:
        for page in pages:
            try:
                rows, _ = _search_page(session, page, attempts)
                for row in rows:
                    gid = str(row.get("id") or "").strip()
                    if not gid.isascii() or not gid.isdigit() or len(gid) > 12 or int(gid) <= 0 or gid in seen:
                        continue
                    seen.add(gid)
                    discovered.append(row)
            except FetchError as exc:
                errors.append(str(exc))
                stopped = True
                break
        if not stopped:
            for row in discovered:
                if max_items is not None and len(items) >= max_items:
                    truncated = True
                    break
                item = _normalize(row, _extract_discovery_thumb(row))
                if item:
                    search_metadata_used += 1
                else:
                    if details >= max_details:
                        unverified += 1
                        continue
                    details += 1
                    try:
                        raw = _detail(session, str(row["id"]), attempts)
                        item = _normalize(raw, _extract_discovery_thumb(row))
                    except FetchError as exc:
                        errors.append(str(exc))
                        stopped = True
                        break
                if item:
                    items.append(item)
                else:
                    unverified += 1

    new_state = dict(state)
    if backfill_pages and not errors and not unverified and not truncated:
        new_state["backfill_page"] = backfill_page + backfill_pages

    status = {
        "status": ("partial" if items else "error") if errors else ("ok" if items else "empty"),
        "discovered": len(discovered),
        "accepted_raw": len(items),
        "search_metadata_used": search_metadata_used,
        "detail_requests": details,
        "metadata_unverified": unverified,
        "stopped": stopped,
        "attempts": attempts,
        "message": " | ".join(errors[:3]),
    }
    return items, new_state, status
