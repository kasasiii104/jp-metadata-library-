import os
import re
import time
import unicodedata
from typing import Any
from urllib.parse import quote_plus, urljoin

import requests
from bs4 import BeautifulSoup

from config import (
    HENTAI3_BACKFILL_PAGES,
    HENTAI3_DETAIL_SLEEP_SEC,
    HENTAI3_GET_DEBUG_DELAY_SEC,
    HENTAI3_GET_DEBUG_LIMIT,
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



def _debug_search_object(raw: dict) -> dict:
    """Return a compact, non-destructive view of one Jandapress search item.

    This is written to source_status.json only for a few samples so we can
    see where 3Hentai keeps its public gallery URL / thumbnail fields without
    dumping the full upstream payload.
    """
    urls: list[str] = []
    image_like_values: list[str] = []
    all_keys: list[str] = []
    seen_keys: set[str] = set()

    image_words = ("thumb", "thumbnail", "cover", "image", "img", "preview", "poster", "picture", "media", "page", "file")
    image_exts = (".jpg", ".jpeg", ".png", ".webp", ".avif", ".gif")

    def add_unique(target: list[str], value: str, limit: int) -> None:
        value = _clean(value)
        if value and value not in target and len(target) < limit:
            target.append(value[:1000])

    def walk(v: Any, path: str = "") -> None:
        if isinstance(v, dict):
            for k, vv in v.items():
                key_path = f"{path}.{k}" if path else str(k)
                if key_path not in seen_keys and len(all_keys) < 100:
                    seen_keys.add(key_path)
                    all_keys.append(key_path)
                if isinstance(vv, str):
                    sv = _clean(vv)
                    if sv.startswith(("http://", "https://", "//", "/")):
                        add_unique(urls, f"{key_path}={sv}", 30)
                    low = sv.lower().split("?", 1)[0]
                    if any(word in str(k).lower() for word in image_words) or low.endswith(image_exts):
                        add_unique(image_like_values, f"{key_path}={sv}", 30)
                if isinstance(vv, (dict, list, tuple)):
                    walk(vv, key_path)
        elif isinstance(v, (list, tuple)):
            for i, vv in enumerate(v[:30]):
                walk(vv, f"{path}[{i}]")

    walk(raw)
    normalized = _normalize(raw, assumed_japanese=True) or {}
    return {
        "id_guess": _clean(_first(raw, "gallery_id", "galleryId", "book", "source_id", "gid", "id") or ""),
        "title_guess": _clean(_first(raw, "title", "name", "pretty", "english", "japanese") or "")[:300],
        "top_level_keys": list(raw.keys())[:60],
        "all_keys": all_keys[:100],
        "raw_urls": urls[:30],
        "image_like_values": image_like_values[:30],
        "real_gallery_url": _find_real_gallery_url(raw),
        "best_thumbnail_url": _best_thumbnail_url(raw),
        "normalized_source_id": normalized.get("source_id", ""),
        "normalized_source_url_kind": normalized.get("source_url_kind", ""),
        "normalized_source_url": normalized.get("source_url", ""),
    }


def _title_key(value: Any) -> str:
    """Normalize a title for conservative exact matching across HTML/API views."""
    text = unicodedata.normalize("NFKC", _clean(value)).casefold()
    return re.sub(r"[\W_]+", "", text, flags=re.UNICODE)


def _direct_search_cards(session: requests.Session, key: str, page: int, sort: str = "recent") -> tuple[list[dict], dict]:
    """Read public 3Hentai search cards and extract only public metadata.

    Jandapress currently returns only title/id for search results and its detail
    endpoint can fail because those numeric IDs are not the public /d/<id> IDs.
    The public search HTML still exposes the actual gallery link and lazy cover
    image.  We use those fields only; no reader pages or full images are fetched.
    """
    url = f"{SOURCE_BASE}/search"
    info = {"page": page, "http_status": 0, "final_url": "", "cards": 0, "error": ""}
    try:
        r = session.get(
            url,
            params={"q": key, "page": page, "sort": sort},
            timeout=TIMEOUT,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; Japanese-Metadata-Library/2.0; metadata-only)",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "ja,en;q=0.8",
            },
        )
        info["http_status"] = r.status_code
        info["final_url"] = r.url
        if r.status_code in {403, 429}:
            info["error"] = f"HTTP {r.status_code}"
            return [], info
        r.raise_for_status()
    except Exception as e:
        info["error"] = str(e)[:500]
        return [], info

    soup = BeautifulSoup(r.text, "html.parser")
    anchors = []
    seen_nodes = set()
    selectors = (
        ".listing-container a.cover",
        ".listing-galleries-container .gallery-wrapper .gallery-thumb",
        "a.cover[href*='/d/']",
        "a.gallery-thumb[href*='/d/']",
    )
    for selector in selectors:
        for node in soup.select(selector):
            ident = id(node)
            if ident not in seen_nodes:
                seen_nodes.add(ident)
                anchors.append(node)
    # Structure-safe fallback: only anchors that actually contain an image.
    if not anchors:
        for node in soup.select("a[href*='/d/']"):
            if node.select_one("img") is not None:
                anchors.append(node)

    cards: list[dict] = []
    seen_gallery_ids: set[str] = set()
    for a in anchors:
        href = _clean(a.get("href") or "")
        direct_url = urljoin(r.url, href)
        m = re.search(r"/d/(\d+)(?:/|$|[?#])", direct_url, re.I)
        if not m:
            continue
        gallery_id = m.group(1)
        if gallery_id in seen_gallery_ids:
            continue
        seen_gallery_ids.add(gallery_id)

        img = a.select_one("img")
        thumb = ""
        title = ""
        if img is not None:
            thumb = _clean(img.get("data-src") or img.get("data-original") or img.get("src") or "")
            title = _clean(img.get("alt") or img.get("title") or "")
        if thumb:
            thumb = urljoin(r.url, thumb)
        if not title:
            title = _clean(a.get("title") or a.get_text(" ", strip=True))
        if not title and getattr(a, "parent", None) is not None:
            parent = a.parent
            caption = parent.select_one(".caption, .related-title, .gallery-title, .title") if hasattr(parent, "select_one") else None
            if caption is not None:
                title = _clean(caption.get_text(" ", strip=True))

        cards.append({
            "gallery_id": gallery_id,
            "title": title,
            "source_url": direct_url,
            "thumbnail": thumb,
        })

    info["cards"] = len(cards)
    return cards, info


def _enrich_from_direct_card(item: dict, card: dict) -> bool:
    """Attach verified public gallery/thumbnail metadata without changing UID."""
    changed = False
    direct_url = _clean(card.get("source_url"))
    if direct_url and re.search(r"/d/\d+(?:/|$|[?#])", direct_url, re.I):
        if item.get("source_url") != direct_url or item.get("source_url_kind") != "direct":
            item["source_url"] = direct_url
            item["source_url_kind"] = "direct"
            changed = True
    thumb = _clean(card.get("thumbnail"))
    if thumb and not item.get("thumbnail"):
        item["thumbnail"] = thumb
        changed = True
    gid = _clean(card.get("gallery_id"))
    if gid and item.get("resolved_gallery_id") != gid:
        item["resolved_gallery_id"] = gid
        changed = True
    return changed


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


def _debug_generic_payload(value: Any) -> dict:
    """Compact diagnostics for a Jandapress detail response.

    Only keys/URLs and short string values are retained.  The full upstream
    response is deliberately not written to source_status.json.
    """
    urls: list[str] = []
    image_like_values: list[str] = []
    all_keys: list[str] = []
    seen_keys: set[str] = set()
    image_words = ("thumb", "thumbnail", "cover", "image", "img", "preview", "poster", "picture", "media", "page", "file")
    image_exts = (".jpg", ".jpeg", ".png", ".webp", ".avif", ".gif")

    def add_unique(target: list[str], value: str, limit: int) -> None:
        value = _clean(value)
        if value and value not in target and len(target) < limit:
            target.append(value[:1000])

    def walk(v: Any, path: str = "") -> None:
        if isinstance(v, dict):
            for k, vv in v.items():
                key_path = f"{path}.{k}" if path else str(k)
                if key_path not in seen_keys and len(all_keys) < 120:
                    seen_keys.add(key_path)
                    all_keys.append(key_path)
                if isinstance(vv, str):
                    sv = _clean(vv)
                    if sv.startswith(("http://", "https://", "//", "/")):
                        add_unique(urls, f"{key_path}={sv}", 40)
                    low = sv.lower().split("?", 1)[0]
                    if any(word in str(k).lower() for word in image_words) or low.endswith(image_exts):
                        add_unique(image_like_values, f"{key_path}={sv}", 40)
                if isinstance(vv, (dict, list, tuple)):
                    walk(vv, key_path)
        elif isinstance(v, (list, tuple)):
            for i, vv in enumerate(v[:40]):
                walk(vv, f"{path}[{i}]")

    walk(value)
    richest = _find_payload_object(value) if isinstance(value, (dict, list)) else {}
    normalized = _normalize(richest, assumed_japanese=True) if richest else None
    return {
        "response_type": type(value).__name__,
        "top_level_keys": list(value.keys())[:80] if isinstance(value, dict) else [],
        "all_keys": all_keys[:120],
        "raw_urls": urls[:40],
        "image_like_values": image_like_values[:40],
        "real_gallery_url": _find_real_gallery_url(value),
        "best_thumbnail_url": _best_thumbnail_url(richest) if richest else "",
        "candidate_objects": len(_find_candidate_dicts(value)) if isinstance(value, (dict, list)) else 0,
        "normalized_source_id": (normalized or {}).get("source_id", ""),
        "normalized_source_url_kind": (normalized or {}).get("source_url_kind", ""),
        "normalized_source_url": (normalized or {}).get("source_url", ""),
    }


def _probe_get_endpoint(session: requests.Session, sid: str) -> tuple[dict, dict | None]:
    """Probe /3hentai/get for a tiny sample without raising on 4xx/5xx.

    This is diagnostic only.  It is intentionally limited by config so a bad
    upstream does not create dozens of failing requests or trigger rate limits.
    """
    url = f"{API_BASE}/3hentai/get"
    try:
        r = session.get(url, params={"book": sid}, timeout=TIMEOUT)
        preview = " ".join((r.text or "").split())[:1200]
        result = {
            "id": sid,
            "http_status": r.status_code,
            "url": r.url,
            "content_type": r.headers.get("content-type", ""),
            "response_preview": preview,
            "response_keys": [],
            "raw_urls": [],
            "image_like_values": [],
            "real_gallery_url": "",
            "best_thumbnail_url": "",
        }
        try:
            payload = r.json()
        except Exception:
            payload = None
        detail_item = None
        if payload is not None:
            summary = _debug_generic_payload(payload)
            result.update(summary)
            result["response_keys"] = summary.get("top_level_keys", [])
            if r.status_code == 200:
                obj = _find_payload_object(payload)
                if obj:
                    detail_item = _normalize(obj, assumed_japanese=True)
        return result, detail_item
    except Exception as e:
        return {
            "id": sid,
            "http_status": 0,
            "url": url,
            "content_type": "",
            "response_preview": f"request error: {e}"[:1200],
            "response_keys": [],
            "raw_urls": [],
            "image_like_values": [],
            "real_gallery_url": "",
            "best_thumbnail_url": "",
        }, None


def _merge_probe_detail(base: dict, detail: dict) -> bool:
    """Use successful sample detail data conservatively without changing UID."""
    changed = False
    if detail.get("source_url_kind") == "direct" and detail.get("source_url"):
        if base.get("source_url") != detail.get("source_url") or base.get("source_url_kind") != "direct":
            base["source_url"] = detail["source_url"]
            base["source_url_kind"] = "direct"
            changed = True
    if detail.get("thumbnail") and not base.get("thumbnail"):
        base["thumbnail"] = detail["thumbnail"]
        changed = True
    for key in ("title_jp", "language", "category", "pages", "rating", "popularity", "posted_at"):
        if detail.get(key) not in (None, "", 0, [], {}) and base.get(key) in (None, "", 0, [], {}):
            base[key] = detail[key]
            changed = True
    for key in ("artists", "groups", "parodies", "characters", "tags"):
        merged = unique_strings(list(base.get(key) or []) + list(detail.get(key) or []))
        if merged != list(base.get(key) or []):
            base[key] = merged
            changed = True
    return changed


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
    debug_raw_objects: list[dict] = []
    direct_html_debug: list[dict] = []
    direct_html_enriched = 0
    direct_html_cards_total = 0

    for page in pages:
        # Public HTML is used only to enrich the Jandapress search rows with
        # the verified /d/<gallery_id> link and public lazy thumbnail.  The
        # Jandapress ID remains the stable local UID for backward compatibility.
        direct_cards, direct_info = _direct_search_cards(session, SEARCH_KEY, page, "recent")
        direct_html_debug.append(direct_info)
        direct_html_cards_total += len(direct_cards)
        direct_by_title = {}
        for card in direct_cards:
            key = _title_key(card.get("title"))
            if key and key not in direct_by_title:
                direct_by_title[key] = card

        try:
            payload, final_url = _get_json(session, "/3hentai/search", {
                "key": SEARCH_KEY,
                "page": page,
                "sort": "recent",
            })
            found = _find_candidate_dicts(payload)
            search_debug.append(f"page={page} candidates={len(found)} url={final_url}")
            for obj in found:
                if len(debug_raw_objects) < 3:
                    debug_raw_objects.append(obj)
                normalized = _normalize(obj, assumed_japanese=True)
                if not normalized:
                    continue
                card = direct_by_title.get(_title_key(normalized.get("title")))
                if card and _enrich_from_direct_card(normalized, card):
                    direct_html_enriched += 1
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
                    if len(debug_raw_objects) < 3:
                        debug_raw_objects.append(obj)
                    normalized = _normalize(obj, assumed_japanese=False)
                    if normalized:
                        candidates[normalized["source_id"]] = normalized
                        if len(candidates) >= HENTAI3_MAX_GALLERIES_PER_RUN:
                            break
                if candidates:
                    break
            except Exception as e:
                errors.append(f"fallback={fallback_key}: {e}")

    # Probe only a tiny sample of the documented Jandapress detail endpoint.
    # We need the actual 400/200 response body to determine why search IDs do
    # not currently resolve to public gallery/image metadata.
    get_debug: list[dict] = []
    get_probe_enriched = 0
    get_probe_rate_limited = False
    probe_ids = list(candidates.keys())[:max(0, HENTAI3_GET_DEBUG_LIMIT)]
    for idx, sid in enumerate(probe_ids):
        dbg, detail_item = _probe_get_endpoint(session, sid)
        get_debug.append(dbg)
        if detail_item and sid in candidates:
            if _merge_probe_detail(candidates[sid], detail_item):
                get_probe_enriched += 1
        if dbg.get("http_status") in {403, 429}:
            get_probe_rate_limited = dbg.get("http_status") == 429
            break
        if idx + 1 < len(probe_ids):
            time.sleep(max(0.0, HENTAI3_GET_DEBUG_DELAY_SEC))

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
        "3hentai_debug_samples": [_debug_search_object(x) for x in debug_raw_objects[:3]],
        "debug_sample_count": min(3, len(debug_raw_objects)),
        "direct_html_mode": "public-search-card-enrichment",
        "direct_html_cards": direct_html_cards_total,
        "direct_html_enriched": direct_html_enriched,
        "direct_html_pages": direct_html_debug[:8],
        "get_probe_mode": "sample-only" if HENTAI3_GET_DEBUG_LIMIT > 0 else "disabled",
        "3hentai_get_debug": get_debug,
        "get_debug_count": len(get_debug),
        "get_debug_successes": sum(1 for x in get_debug if x.get("http_status") == 200),
        "get_debug_http_statuses": [x.get("http_status") for x in get_debug],
        "get_debug_rate_limited": get_probe_rate_limited,
        "get_probe_enriched_items": get_probe_enriched,
        "debug": " | ".join(search_debug[:8]),
        "message": " | ".join((errors + detail_samples)[:8]),
    }
    return items, new_state, status
