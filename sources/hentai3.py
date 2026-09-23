import os
import time
from typing import Any
from urllib.parse import urljoin

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
        if isinstance(v, str) and v.startswith(("http://", "https://", "/")):
            return urljoin(SOURCE_BASE, v)
        if isinstance(v, dict):
            for kk in ("url", "src", "source", "original", "thumbnail", "cover"):
                vv = v.get(kk)
                if isinstance(vv, str) and vv.startswith(("http://", "https://", "/")):
                    return urljoin(SOURCE_BASE, vv)
    return ""


def _normalize(raw: dict, *, assumed_japanese: bool = False) -> dict | None:
    ident = _first(raw, "id", "gallery_id", "galleryId", "book", "source_id", "gid")
    title = _first(raw, "title", "name", "pretty", "english", "japanese")
    if ident is None or not title:
        return None

    sid = _clean(ident)
    title = _clean(title)
    title_jp = _clean(_first(raw, "title_jp", "title_jpn", "japanese", "jp_title") or "")

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

    source_url = _pick_url(raw, "url", "link", "source_url", "href")
    thumbnail = _pick_url(raw, "thumbnail", "thumb", "cover", "image", "images")

    category = _clean(_first(raw, "category", "type") or "")

    return {
        "uid": f"3hentai:{sid}",
        "source": "3hentai",
        "source_id": sid,
        "source_url": source_url,
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

    for sid, base_item in list(candidates.items())[:HENTAI3_MAX_GALLERIES_PER_RUN]:
        detail, detail_info = _detail(session, sid)
        item = detail or base_item
        if detail is None:
            detail_errors += 1
            if len(detail_samples) < 5:
                detail_samples.append(f"{sid}: {detail_info}")
        lang = item.get("language") or "missing"
        languages[lang] = languages.get(lang, 0) + 1
        if item.get("thumbnail"):
            thumbs_found += 1
        else:
            thumbs_missing += 1
        items.append(item)
        time.sleep(HENTAI3_DETAIL_SLEEP_SEC)

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
        "detail_errors": detail_errors,
        "languages_detected": languages,
        "thumbnails_found": thumbs_found,
        "thumbnails_missing": thumbs_missing,
        "debug": " | ".join(search_debug[:8]),
        "message": " | ".join((errors + detail_samples)[:8]),
    }
    return items, new_state, status
