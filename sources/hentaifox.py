import os
import re
from typing import Any
from urllib.parse import quote_plus

import requests

from config import HENTAIFOX_LATEST_PAGES, HENTAIFOX_MAX_GALLERIES_PER_RUN
from sources.common import unique_strings

API_BASE = os.environ.get("JANDAPRESS_URL", "http://127.0.0.1:3000").rstrip("/")
SEARCH_KEY = os.environ.get("HENTAIFOX_SEARCH_KEY", "language:japanese")
TIMEOUT = 30


def _clean(v: Any) -> str:
    return " ".join(str(v or "").split()).strip()


def _first(d: dict, *keys: str):
    for k in keys:
        if k in d and d[k] not in (None, "", [], {}):
            return d[k]
    return None


def _find(obj: Any) -> list[dict]:
    out = []
    if isinstance(obj, dict):
        ident = _first(obj, "id", "gallery_id", "galleryId", "book", "source_id", "gid")
        title = _first(obj, "title", "name", "pretty", "english", "japanese")
        if ident is not None and title:
            out.append(obj)
        for v in obj.values():
            if isinstance(v, (dict, list)):
                out.extend(_find(v))
    elif isinstance(obj, list):
        for v in obj:
            out.extend(_find(v))
    return out


def _tags(v: Any) -> list[str]:
    out = []
    if isinstance(v, str):
        if _clean(v): out.append(_clean(v))
    elif isinstance(v, dict):
        name = _first(v, "name", "tag", "value", "label", "title")
        ns = _first(v, "namespace", "type", "category")
        if name:
            name = _clean(name)
            out.append(f"{_clean(ns)}:{name}" if ns and ":" not in name else name)
        for x in v.values():
            if isinstance(x, (dict, list)): out.extend(_tags(x))
    elif isinstance(v, list):
        for x in v: out.extend(_tags(x))
    return unique_strings(out)


def _list_names(raw: dict, *keys: str) -> list[str]:
    vals = []
    for key in keys:
        v = raw.get(key)
        if isinstance(v, list):
            for x in v:
                if isinstance(x, str): vals.append(x)
                elif isinstance(x, dict):
                    n = _first(x, "name", "value", "label", "title")
                    if n: vals.append(_clean(n))
        elif isinstance(v, str):
            vals.append(v)
    return unique_strings(vals)


def _normalize(raw: dict) -> dict | None:
    sid = _clean(_first(raw, "id", "gallery_id", "galleryId", "book", "source_id", "gid"))
    title = _clean(_first(raw, "title", "name", "pretty", "english", "japanese"))
    if not sid or not title:
        return None
    tags = _tags(_first(raw, "tags", "tag", "metadata"))
    lang_text = " ".join(tags).lower()
    language = "japanese" if ("japanese" in lang_text or "日本語" in lang_text or "language:japanese" in lang_text) else "japanese"
    pages = _first(raw, "total", "pages", "page_count", "num_pages")
    try: pages = int(pages or 0)
    except Exception: pages = 0
    thumb = ""
    for key in ("thumbnail", "thumb", "cover", "poster", "image"):
        v = raw.get(key)
        if isinstance(v, str) and v.startswith(("http://", "https://")):
            thumb = v; break
        if isinstance(v, dict):
            for kk in ("url", "src", "source"):
                vv = v.get(kk)
                if isinstance(vv, str) and vv.startswith(("http://", "https://")):
                    thumb = vv; break
            if thumb: break
    return {
        "uid": f"hentaifox:{sid}",
        "source": "hentaifox",
        "source_id": sid,
        "source_url": f"https://hentaifox.com/gallery/{sid}/",
        "source_url_kind": "direct",
        "title": title,
        "title_jp": _clean(_first(raw, "japanese", "title_jp")),
        "language": language,
        "category": _clean(_first(raw, "category", "type")),
        "artists": _list_names(raw, "artists", "artist"),
        "groups": _list_names(raw, "groups", "group", "circles", "circle"),
        "parodies": _list_names(raw, "parodies", "parody", "series"),
        "works": _list_names(raw, "parodies", "parody", "series"),
        "characters": _list_names(raw, "characters", "character"),
        "tags": tags,
        "pages": pages,
        "rating": None,
        "popularity": None,
        "posted_at": "",
        "thumbnail": thumb,
        "fallback_search_url": f"https://hentaifox.com/?q={quote_plus(title)}",
    }


def collect(state: dict | None = None):
    state = dict(state or {})
    session = requests.Session()
    try:
        root = session.get(f"{API_BASE}/", timeout=10)
        root.raise_for_status()
    except Exception as e:
        return [], state, {"status":"error","discovered":0,"accepted_raw":0,"message":f"Jandapress unavailable: {e}"}

    found = {}
    errors = []
    pages_fetched = 0
    for page in range(1, HENTAIFOX_LATEST_PAGES + 1):
        try:
            r = session.get(f"{API_BASE}/hentaifox/search", params={"key":SEARCH_KEY,"page":page,"sort":"latest"}, timeout=TIMEOUT)
            r.raise_for_status()
            payload = r.json()
            rows = _find(payload)
            if not rows and SEARCH_KEY != "japanese":
                r = session.get(f"{API_BASE}/hentaifox/search", params={"key":"japanese","page":page,"sort":"latest"}, timeout=TIMEOUT)
                r.raise_for_status(); rows = _find(r.json())
            pages_fetched += 1
            for raw in rows:
                item = _normalize(raw)
                if item: found[item["source_id"]] = item
                if len(found) >= HENTAIFOX_MAX_GALLERIES_PER_RUN: break
        except Exception as e:
            errors.append(f"page={page}: {str(e)[:300]}")
        if len(found) >= HENTAIFOX_MAX_GALLERIES_PER_RUN: break

    items = list(found.values())[:HENTAIFOX_MAX_GALLERIES_PER_RUN]
    return items, state, {
        "status": "ok" if items else "error",
        "discovered": len(found),
        "accepted_raw": len(items),
        "pages_fetched": pages_fetched,
        "search_key": SEARCH_KEY,
        "tagged_items": sum(1 for x in items if x.get("tags")),
        "artists_found": sum(1 for x in items if x.get("artists")),
        "groups_found": sum(1 for x in items if x.get("groups")),
        "works_found": sum(1 for x in items if x.get("works")),
        "characters_found": sum(1 for x in items if x.get("characters")),
        "message": " | ".join(errors[:4]),
    }
