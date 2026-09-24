import os
import re
import time
from typing import Any
from urllib.parse import quote_plus, urljoin

import requests

from config import (
    HENTAI3_BACKFILL_PAGES,
    HENTAI3_DETAIL_SLEEP_SEC,
    HENTAI3_LATEST_PAGES,
    HENTAI3_MAX_GALLERIES_PER_RUN,
)
from sources.common import unique_strings

API_BASE = os.environ.get("JANDAPRESS_URL", "http://127.0.0.1:3000").rstrip("/")
SOURCE_BASE = "https://3hentai.net"
SEARCH_KEY = os.environ.get("HENTAI3_SEARCH_KEY", "language:japanese")
TIMEOUT = 30


def _clean(v: Any) -> str:
    return " ".join(str(v or "").split()).strip()


def _num(v: Any):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return v
    s = _clean(v).replace(",", "")
    try:
        return float(s) if "." in s else int(s)
    except Exception:
        return None


def _first(d: dict, *keys: str):
    for k in keys:
        if k in d and d[k] not in (None, "", [], {}):
            return d[k]
    return None


def _flatten_tags(value: Any) -> list[str]:
    out: list[str] = []
    if value is None:
        return out
    if isinstance(value, str):
        s = _clean(value)
        if s:
            out.append(s)
        return out
    if isinstance(value, dict):
        name = _first(value, "name", "tag", "value", "label", "title")
        namespace = _first(value, "namespace", "type", "category")
        if name:
            name = _clean(name)
            if namespace and ":" not in name:
                out.append(f"{_clean(namespace)}:{name}")
            else:
                out.append(name)
        for k, v in value.items():
            if k in {"name", "tag", "value", "label", "title", "namespace", "type", "category"}:
                continue
            if isinstance(v, (list, tuple, set, dict)):
                out.extend(_flatten_tags(v))
        return unique_strings(out)
    if isinstance(value, (list, tuple, set)):
        for x in value:
            out.extend(_flatten_tags(x))
    return unique_strings(out)


def _find_candidate_dicts(obj: Any) -> list[dict]:
    """Recursively collect objects that look like gallery/search results."""
    found: list[dict] = []
    if isinstance(obj, dict):
        ident = _first(obj, "id", "gallery_id", "galleryId", "book", "source_id", "gid")
        title = _first(obj, "title", "name", "pretty", "english", "japanese")
        if ident is not None and title:
            found.append(obj)
        for v in obj.values():
            if isinstance(v, (dict, list)):
                found.extend(_find_candidate_dicts(v))
    elif isinstance(obj, list):
        for v in obj:
            found.extend(_find_candidate_dicts(v))
    return found


def _find_payload_object(obj: Any) -> dict:
    """Find the richest single gallery object in a get response."""
    candidates = _find_candidate_dicts(obj)
    if not candidates:
        if isinstance(obj, dict):
            data = obj.get("data")
            if isinstance(data, dict):
                return data
            return obj
        return {}
    return max(candidates, key=lambda x: len(x))


def _pick_url(obj: dict, *keys: str) -> str:
    for k in keys:
        v = obj.get(k)
        if isinstance(v, str) and v.startswith(("http://", "https://", "//", "/")):
            return urljoin(SOURCE_BASE, v)
        if isinstance(v, dict):
            for kk in ("url", "src", "source", "original", "thumbnail", "cover"):
                vv = v.get(kk)
                if isinstance(vv, str) and vv.startswith(("http://", "https://", "//", "/")):
                    return urljoin(SOURCE_BASE, vv)
    return ""


def _image_candidates(value: Any, path: str = "") -> list[tuple[int, str]]:
    """Collect likely representative image URLs from nested Jandapress payloads.

    3Hentai responses have changed shape over time and image data may live in
    arrays such as images/pages/files rather than a top-level thumbnail field.
    This deliberately prefers thumbnail/cover-like fields and only then falls
    back to a representative page image.
    """
    out: list[tuple[int, str]] = []
    image_ext = (".jpg", ".jpeg", ".png", ".webp", ".avif", ".gif")

    def add(raw: str, context: str):
        raw = _clean(raw)
        if not raw or not raw.startswith(("http://", "https://", "//", "/")):
            return
        url = urljoin(SOURCE_BASE, raw)
        low_url = url.lower().split("?", 1)[0]
        ctx = context.lower()
        score = 0
        if "thumbnail" in ctx or "thumb" in ctx:
            score += 120
        if "cover" in ctx:
            score += 110
        if "preview" in ctx:
            score += 90
        if any(k in ctx for k in ("image", "images", "picture", "poster")):
            score += 75
        if any(k in ctx for k in ("page", "pages", "file", "files", "media")):
            score += 35
        if low_url.endswith(image_ext):
            score += 50
        if "3hentai" in low_url:
            score += 5
        # Page URLs are not useful as thumbnails unless the context explicitly
        # identifies them as an image.
        if score >= 50:
            out.append((score, url))

    def walk(v: Any, context: str):
        if isinstance(v, str):
            add(v, context)
        elif isinstance(v, dict):
            for k, vv in v.items():
                walk(vv, f"{context}.{k}" if context else str(k))
        elif isinstance(v, (list, tuple, set)):
            for i, vv in enumerate(v):
                walk(vv, f"{context}[{i}]")

    walk(value, path)
    # Stable order, highest confidence first, de-duplicate URLs.
    seen = set()
    result = []
    for score, url in sorted(out, key=lambda x: x[0], reverse=True):
        if url in seen:
            continue
        seen.add(url)
        result.append((score, url))
    return result


def _best_thumbnail_url(raw: dict) -> str:
    direct = _pick_url(raw, "thumbnail", "thumb", "cover", "poster", "image")
    if direct:
        return direct
    candidates = _image_candidates(raw)
    return candidates[0][1] if candidates else ""




def _find_real_gallery_url(value: Any) -> str:
    """Return an actual 3Hentai gallery URL found in the API payload.

    Do not synthesize /d/<generic id>: Jandapress search payloads may contain
    numeric IDs that are not the public 3Hentai gallery id.
    """
    found: list[str] = []

    def scan(v: Any):
        if isinstance(v, str):
            text = v.strip()
            # Absolute, protocol-relative and relative gallery links.
            m = re.search(r"(?:(?:https?:)?//(?:[a-z0-9-]+\.)?3hentai\.net)?(/d/\d+)(?:[/#?]|$)", text, re.I)
            if m:
                if text.startswith("//"):
                    found.append("https:" + text)
                elif text.startswith(("http://", "https://")):
                    found.append(text)
                else:
                    found.append(urljoin(SOURCE_BASE, m.group(1)))
        elif isinstance(v, dict):
            # Link-ish fields first, then recurse through the rest.
            for k in ("url", "link", "href", "source_url", "path", "permalink"):
                if k in v:
                    scan(v[k])
            for k, vv in v.items():
                if k not in {"url", "link", "href", "source_url", "path", "permalink"}:
                    scan(vv)
        elif isinstance(v, (list, tuple, set)):
            for vv in v:
                scan(vv)

    scan(value)
    return found[0] if found else ""

def _normalize(raw: dict, *, assumed_japanese: bool = False) -> dict | None:
    # Jandapress search payloads may expose internal/nested numeric IDs.
    # Only a real /d/<id> link found in the payload is trusted as a direct
    # 3Hentai gallery URL. Never fabricate /d/<generic-id>.
    source_url = _find_real_gallery_url(raw)
    url_match = re.search(r"/d/(\d+)(?:/|$|[?#])", source_url or "", re.I)

    ident = url_match.group(1) if url_match else _first(
        raw, "gallery_id", "galleryId", "book", "source_id", "gid", "id"
    )
    title = _first(raw, "title", "name", "pretty", "english", "japanese")
    if ident is None or not title:
        return None

    sid = _clean(ident)
    title = _clean(title)
    title_jp = _clean(_first(raw, "title_jp", "title_jpn", "japanese", "jp_title") or "")

    # When the API gives no real gallery link, send the user to a 3Hentai
    # title search instead of a likely-dead /d/<internal-id> URL.
    source_url_kind = "direct" if source_url else "search"
    if not source_url:
        source_url = f"{SOURCE_BASE}/search?q={quote_plus(title)}"

    tags = []
    for k in ("tags", "tag", "artists", "artist", "groups", "group", "parodies", "parody", "characters", "character", "languages", "language"):
        if k in raw:
            tags.extend(_flatten_tags(raw.get(k)))
    tags = unique_strings(tags)

    lang = ""
    for t in tags:
        low = t.lower()
        if low in {"japanese", "language:japanese", "lang:japanese", "日本語"} or low.endswith(":japanese"):
            lang = "japanese"
            break
        if low in {"english", "language:english", "chinese", "language:chinese", "korean", "language:korean"}:
            lang = low.split(":")[-1]
            break
    raw_lang = _clean(_first(raw, "language", "lang") or "").lower()
    if raw_lang in {"japanese", "ja", "日本語"}:
        lang = "japanese"
    elif raw_lang and not lang:
        lang = raw_lang
    if not lang and assumed_japanese:
        lang = "japanese"
        tags.append("language:japanese")

    artists = unique_strings(_flatten_tags(_first(raw, "artists", "artist") or []))
    groups = unique_strings(_flatten_tags(_first(raw, "groups", "group", "circles", "circle") or []))
    parodies = unique_strings(_flatten_tags(_first(raw, "parodies", "parody", "series") or []))
    characters = unique_strings(_flatten_tags(_first(raw, "characters", "character") or []))

    pages = _num(_first(raw, "pages", "page_count", "pageCount", "total", "num_pages")) or 0
    rating = _num(_first(raw, "rating", "score"))
    popularity = _num(_first(raw, "views", "favorites", "favourites", "popularity", "likes"))
    posted = _clean(_first(raw, "posted_at", "posted", "date", "uploaded_at", "created_at") or "")

    thumbnail = _best_thumbnail_url(raw)
    category = _clean(_first(raw, "category", "type") or "")

    return {
        "uid": f"3hentai:{sid}",
        "source": "3hentai",
        "source_id": sid,
        "source_url": source_url,
        "source_url_kind": source_url_kind,
        "title": title,
        "title_jp": title_jp or (title if lang == "japanese" else ""),
        "language": lang,
        "category": category,
        "artists": artists,
        "groups": groups,
        "parodies": parodies,
        "characters": characters,
        "tags": unique_strings(tags),
        "pages": int(pages) if isinstance(pages, (int, float)) else 0,
        "rating": float(rating) if isinstance(rating, (int, float)) else None,
        "popularity": int(popularity) if isinstance(popularity, (int, float)) else None,
        "posted_at": posted,
        "thumbnail": thumbnail,
    }

def _get_json(session: requests.Session, path: str, params: dict | None = None):
    url = f"{API_BASE}{path}"
    r = session.get(url, params=params or {}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json(), r.url


def _detail(session: requests.Session, sid: str) -> tuple[dict | None, str]:
    try:
        payload, url = _get_json(session, "/3hentai/get", {"book": sid})
        obj = _find_payload_object(payload)
        return _normalize(obj, assumed_japanese=True), url
    except Exception as e:
        return None, str(e)


def collect(state: dict | None = None) -> tuple[list[dict], dict, dict]:
    state = dict(state or {})
    backfill_page = max(1, int(state.get("backfill_page") or 1))
    session = requests.Session()

    # First verify the locally self-hosted Jandapress service.
    try:
        root = session.get(f"{API_BASE}/", timeout=10)
        service_status = root.status_code
    except Exception as e:
        return [], state, {
            "status": "error",
            "discovered": 0,
            "accepted_raw": 0,
            "api_base": API_BASE,
            "message": f"Jandapress unavailable: {e}",
        }

    pages: list[int] = []
    for p in list(range(1, HENTAI3_LATEST_PAGES + 1)) + list(range(backfill_page, backfill_page + HENTAI3_BACKFILL_PAGES)):
        if p not in pages:
            pages.append(p)

    candidates: dict[str, dict] = {}
    search_debug: list[str] = []
    errors: list[str] = []

    for page in pages:
        try:
            payload, final_url = _get_json(session, "/3hentai/search", {
                "key": SEARCH_KEY,
                "page": page,
                "sort": "recent",
            })
            found = _find_candidate_dicts(payload)
            search_debug.append(f"page={page} candidates={len(found)} url={final_url}")
            for obj in found:
                normalized = _normalize(obj, assumed_japanese=True)
                if not normalized:
                    continue
                candidates[normalized["source_id"]] = normalized
                if len(candidates) >= HENTAI3_MAX_GALLERIES_PER_RUN:
                    break
        except Exception as e:
            errors.append(f"page={page}: {e}")
        if len(candidates) >= HENTAI3_MAX_GALLERIES_PER_RUN:
            break
        time.sleep(0.4)

    # If tag-query search unexpectedly yields no results, try a plain Japanese
    # keyword as a normal public search fallback. This does not bypass any access control.
    if not candidates:
        for fallback_key in ("japanese", "日本語"):
            try:
                payload, final_url = _get_json(session, "/3hentai/search", {
                    "key": fallback_key,
                    "page": 1,
                    "sort": "recent",
                })
                found = _find_candidate_dicts(payload)
                search_debug.append(f"fallback={fallback_key!r} candidates={len(found)} url={final_url}")
                for obj in found:
                    normalized = _normalize(obj, assumed_japanese=False)
                    if normalized:
                        candidates[normalized["source_id"]] = normalized
                        if len(candidates) >= HENTAI3_MAX_GALLERIES_PER_RUN:
                            break
                if candidates:
                    break
            except Exception as e:
                errors.append(f"fallback={fallback_key}: {e}")

    items: list[dict] = []
    detail_errors = 0
    detail_samples: list[str] = []
    languages: dict[str, int] = {}
    thumbs_found = 0
    thumbs_missing = 0

    # Do not call /3hentai/get for every search result.  On current 3Hentai
    # data that endpoint can return 400 for fresh galleries, and dozens of
    # sequential get calls also trigger 429 upstream.  Search is sufficient
    # for discovery; thumbnails are resolved separately and slowly by the
    # local cache step.
    for sid, item in list(candidates.items())[:HENTAI3_MAX_GALLERIES_PER_RUN]:
        lang = item.get("language") or "missing"
        languages[lang] = languages.get(lang, 0) + 1
        if item.get("thumbnail"):
            thumbs_found += 1
        else:
            thumbs_missing += 1
        items.append(item)

    new_state = dict(state)
    if items or candidates:
        new_state["backfill_page"] = backfill_page + HENTAI3_BACKFILL_PAGES

    status = {
        "status": "ok" if items else "error",
        "discovered": len(candidates),
        "accepted_raw": len(items),
        "api_base": API_BASE,
        "jandapress_http": service_status,
        "search_key": SEARCH_KEY,
        "detail_mode": "search-only",
        "detail_errors": 0,
        "source_urls_found": sum(1 for x in items if x.get("source_url")),
        "direct_source_urls": sum(1 for x in items if x.get("source_url_kind") == "direct"),
        "search_fallback_urls": sum(1 for x in items if x.get("source_url_kind") == "search"),
        "source_url_samples": [f"{x.get('source_id')} [{x.get('source_url_kind')}]: {x.get('source_url')}" for x in items if x.get("source_url")][:5],
        "languages_detected": languages,
        "thumbnails_found": thumbs_found,
        "thumbnails_missing": thumbs_missing,
        "debug": " | ".join(search_debug[:8]),
        "message": " | ".join((errors + detail_samples)[:8]),
    }
    return items, new_state, status
