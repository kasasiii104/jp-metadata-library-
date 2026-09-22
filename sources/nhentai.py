from typing import Any
from urllib.parse import quote

import requests

from config import NH_BACKFILL_PAGES, NH_LATEST_PAGES, NH_MAX_DETAILS_PER_RUN
from sources.common import iso_from_unix, safe_get, unique_strings

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


def _results(payload: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("result", "results", "galleries", "items"):
        value = payload.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
    data = payload.get("data")
    if isinstance(data, dict):
        return _results(data)
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    return []


def _search_page(session: requests.Session, page: int) -> tuple[list[dict], str]:
    q = quote("language:japanese", safe="")
    last_error = None
    for template in SEARCH_ENDPOINTS:
        url = template.format(query=q, page=page)
        try:
            res = safe_get(session, url, headers={"Accept": "application/json"})
            payload = res.json()
            return _results(payload), url
        except Exception as e:
            last_error = e
    raise RuntimeError(f"nHentai search failed: {last_error}")


def _detail(session: requests.Session, gid: str) -> dict[str, Any]:
    last_error = None
    for template in DETAIL_ENDPOINTS:
        try:
            res = safe_get(session, template.format(gid=gid), headers={"Accept": "application/json"})
            payload = res.json()
            if isinstance(payload, dict) and not payload.get("error"):
                return payload
        except Exception as e:
            last_error = e
    raise RuntimeError(f"nHentai detail {gid} failed: {last_error}")


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
    if not gid:
        return None
    titles = raw.get("title") if isinstance(raw.get("title"), dict) else {}
    tagmap = _tag_map(raw.get("tags") or [])
    languages = {x.lower() for x in tagmap.get("language", [])}
    language = "japanese" if "japanese" in languages else ""
    category = (tagmap.get("category") or [""])[0]
    pure_tags = unique_strings(tagmap.get("tag", []))

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
        "pages": int(raw.get("num_pages") or 0),
        "rating": None,
        "popularity": int(raw.get("num_favorites") or 0) if raw.get("num_favorites") is not None else None,
        "posted_at": iso_from_unix(raw.get("upload_date")) if raw.get("upload_date") else "",
        "thumbnail": _thumb_from_detail(raw, discovered_thumb),
    }


def collect(state: dict | None = None) -> tuple[list[dict], dict, dict]:
    state = dict(state or {})
    backfill_page = int(state.get("backfill_page") or (NH_LATEST_PAGES + 1))
    session = requests.Session()
    errors: list[str] = []
    discovered: list[tuple[str, str]] = []
    seen = set()

    pages = list(range(1, NH_LATEST_PAGES + 1)) + list(range(backfill_page, backfill_page + NH_BACKFILL_PAGES))
    for page in pages:
        try:
            rows, _ = _search_page(session, page)
            for row in rows:
                gid = str(row.get("id") or "").strip()
                if not gid or gid in seen:
                    continue
                seen.add(gid)
                discovered.append((gid, _extract_discovery_thumb(row)))
        except Exception as e:
            errors.append(f"page {page}: {e}")

    discovered = discovered[:NH_MAX_DETAILS_PER_RUN]
    items: list[dict] = []
    for gid, thumb in discovered:
        try:
            raw = _detail(session, gid)
            item = _normalize(raw, thumb)
            if item:
                items.append(item)
        except Exception as e:
            errors.append(str(e))

    new_state = dict(state)
    if items or not errors:
        new_state["backfill_page"] = backfill_page + NH_BACKFILL_PAGES

    status = {
        "status": "ok" if items else ("error" if errors else "empty"),
        "discovered": len(discovered),
        "accepted_raw": len(items),
        "message": " | ".join(errors[:3]),
    }
    return items, new_state, status
