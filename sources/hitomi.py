import re
from typing import Any

import requests
from bs4 import BeautifulSoup

from config import HITOMI_BACKFILL_LIMIT, HITOMI_LATEST_LIMIT
from sources.common import absolute_url, parse_json_wrapped_js, safe_get, unique_strings

# Hitomi moved resource delivery from ltn.hitomi.la to
# ltn.gold-usergeneratedcontent.net. Keep the old host as a fallback only.
RESOURCE_BASES = [
    "https://ltn.gold-usergeneratedcontent.net",
    "https://ltn.hitomi.la",
]
FRONT_BASE = "https://hitomi.la"
INDEX_PATH = "/n/index-japanese.nozomi"
GALLERY_JS_PATH = "/galleries/{gid}.js"
GALLERY_BLOCK_URLS = [
    FRONT_BASE + "/galleryblock/{gid}.html",
]
RESOURCE_HEADERS = {
    "Accept": "application/octet-stream,*/*",
    "Accept-Encoding": "identity",
    "Referer": FRONT_BASE + "/",
}
JS_HEADERS = {
    "Accept": "application/javascript,text/javascript,*/*;q=0.8",
    "Referer": FRONT_BASE + "/",
}


def _ids_from_nozomi(data: bytes) -> list[int]:
    if len(data) % 4:
        data = data[: len(data) - (len(data) % 4)]
    return [int.from_bytes(data[i : i + 4], "big") for i in range(0, len(data), 4)]


def _fetch_range_from_base(
    session: requests.Session,
    base: str,
    start_index: int,
    count: int,
) -> tuple[list[int], int | None]:
    if count <= 0:
        return [], None
    start_byte = max(0, start_index) * 4
    end_byte = start_byte + count * 4 - 1
    headers = dict(RESOURCE_HEADERS)
    headers["Range"] = f"bytes={start_byte}-{end_byte}"
    url = base + INDEX_PATH
    res = safe_get(session, url, headers=headers)

    data = res.content
    if res.status_code == 200 and len(data) > count * 4:
        data = data[start_byte : end_byte + 1]

    total_items = None
    content_range = res.headers.get("Content-Range") or ""
    m = re.search(r"/([0-9]+)$", content_range)
    if m:
        try:
            total_items = int(m.group(1)) // 4
        except Exception:
            total_items = None

    return _ids_from_nozomi(data), total_items


def _range_ids(
    session: requests.Session,
    start_index: int,
    count: int,
    preferred_base: str | None = None,
) -> tuple[list[int], int | None, str]:
    bases = list(RESOURCE_BASES)
    if preferred_base and preferred_base in bases:
        bases.remove(preferred_base)
        bases.insert(0, preferred_base)

    errors: list[str] = []
    for base in bases:
        try:
            ids, total = _fetch_range_from_base(session, base, start_index, count)
            return ids, total, base
        except Exception as e:
            errors.append(f"{base}: {e}")
    raise RuntimeError("all Hitomi resource hosts failed: " + " | ".join(errors))


def _list_values(raw: Any, preferred_keys: tuple[str, ...]) -> list[str]:
    values: list[str] = []
    for entry in raw or []:
        if isinstance(entry, str):
            values.append(entry)
            continue
        if not isinstance(entry, dict):
            continue
        value = ""
        for key in preferred_keys:
            if entry.get(key):
                value = str(entry[key])
                break
        if not value:
            for key, candidate in entry.items():
                if key in {"url", "href"}:
                    continue
                if isinstance(candidate, str) and candidate.strip():
                    value = candidate
                    break
        if value:
            values.append(value)
    return unique_strings(values)


def _tag_values(raw: Any) -> list[str]:
    out: list[str] = []
    for entry in raw or []:
        if isinstance(entry, str):
            out.append(entry)
            continue
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("tag") or entry.get("name") or "").strip()
        if not name:
            continue
        if str(entry.get("female") or "") in {"1", "true", "True"}:
            out.append(f"female:{name}")
        elif str(entry.get("male") or "") in {"1", "true", "True"}:
            out.append(f"male:{name}")
        else:
            out.append(name)
    return unique_strings(out)


def _thumbnail_from_raw(raw: dict[str, Any]) -> str:
    for key in ("thumbnail", "thumb", "cover"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return absolute_url(value.strip())
        if isinstance(value, dict):
            for subkey in ("url", "src", "s"):
                candidate = str(value.get(subkey) or "").strip()
                if candidate:
                    return absolute_url(candidate)
    return ""


def _thumbnail(session: requests.Session, gid: int, raw: dict[str, Any]) -> str:
    direct = _thumbnail_from_raw(raw)
    if direct:
        return direct
    for template in GALLERY_BLOCK_URLS:
        try:
            res = safe_get(session, template.format(gid=gid), headers={"Referer": FRONT_BASE + "/"})
            soup = BeautifulSoup(res.text, "html.parser")
            img = soup.select_one("img")
            if not img:
                continue
            for attr in ("data-src", "data-original", "src"):
                url = str(img.get(attr) or "").strip()
                if url:
                    return absolute_url(url)
        except Exception:
            continue
    return ""


def _normalize(gid: int, raw: dict[str, Any], thumbnail: str) -> dict[str, Any] | None:
    language = str(raw.get("language") or raw.get("language_localname") or raw.get("lang") or "").strip()
    language_norm = "japanese" if language.lower() in {"japanese", "ja"} or language == "日本語" else language.lower()
    title = str(raw.get("title") or raw.get("name") or "").strip()
    title_jp = str(raw.get("japanese_title") or raw.get("title_japanese") or "").strip()
    if language_norm == "japanese" and not title_jp:
        title_jp = title

    tags = _tag_values(raw.get("tags"))
    artists = _list_values(raw.get("artists"), ("artist", "name"))
    groups = _list_values(raw.get("groups"), ("group", "name"))
    parodies = _list_values(raw.get("parodys") or raw.get("parodies"), ("parody", "name"))
    characters = _list_values(raw.get("characters"), ("character", "name"))
    files = raw.get("files") or []

    return {
        "uid": f"hitomi:{gid}",
        "source": "hitomi",
        "source_id": str(gid),
        "source_url": f"{FRONT_BASE}/galleries/{gid}.html",
        "title": title,
        "title_jp": title_jp,
        "language": language_norm,
        "category": str(raw.get("type") or raw.get("category") or "").strip(),
        "artists": artists,
        "groups": groups,
        "parodies": parodies,
        "characters": characters,
        "tags": tags,
        "pages": len(files) if isinstance(files, list) else int(raw.get("files") or 0),
        "rating": None,
        "popularity": None,
        "posted_at": str(raw.get("date") or raw.get("published") or "").strip(),
        "thumbnail": thumbnail,
    }


def _fetch_gallery_js(session: requests.Session, gid: int, preferred_base: str | None) -> tuple[dict[str, Any], str]:
    bases = list(RESOURCE_BASES)
    if preferred_base and preferred_base in bases:
        bases.remove(preferred_base)
        bases.insert(0, preferred_base)
    errors: list[str] = []
    for base in bases:
        try:
            res = safe_get(session, base + GALLERY_JS_PATH.format(gid=gid), headers=JS_HEADERS)
            return parse_json_wrapped_js(res.text), base
        except Exception as e:
            errors.append(f"{base}: {e}")
    raise RuntimeError("gallery metadata hosts failed: " + " | ".join(errors))


def _fetch_one(session: requests.Session, gid: int, preferred_base: str | None) -> tuple[dict[str, Any] | None, str | None]:
    raw, used_base = _fetch_gallery_js(session, gid, preferred_base)
    thumb = _thumbnail(session, gid, raw)
    return _normalize(gid, raw, thumb), used_base


def collect(state: dict | None = None) -> tuple[list[dict], dict, dict]:
    state = dict(state or {})
    backfill_offset = int(state.get("backfill_offset") or HITOMI_LATEST_LIMIT)
    preferred_base = str(state.get("resource_base") or "") or None
    session = requests.Session()
    errors: list[str] = []

    try:
        latest_ids, latest_total, latest_base = _range_ids(session, 0, HITOMI_LATEST_LIMIT, preferred_base)
        backfill_ids, backfill_total, backfill_base = _range_ids(
            session,
            backfill_offset,
            HITOMI_BACKFILL_LIMIT,
            latest_base,
        )
        preferred_base = latest_base or backfill_base
        total_index = latest_total if latest_total is not None else backfill_total
    except Exception as e:
        return [], state, {
            "status": "error",
            "discovered": 0,
            "accepted_raw": 0,
            "message": str(e),
            "resource_hosts": RESOURCE_BASES,
            "index_path": INDEX_PATH,
        }

    ids: list[int] = []
    seen = set()
    for gid in latest_ids + backfill_ids:
        if gid and gid not in seen:
            seen.add(gid)
            ids.append(gid)

    items: list[dict] = []
    used_hosts: dict[str, int] = {}
    for gid in ids:
        try:
            item, used_base = _fetch_one(session, gid, preferred_base)
            if used_base:
                preferred_base = used_base
                used_hosts[used_base] = used_hosts.get(used_base, 0) + 1
            if item:
                items.append(item)
        except Exception as e:
            if len(errors) < 6:
                errors.append(f"{gid}: {e}")

    new_state = dict(state)
    if preferred_base:
        new_state["resource_base"] = preferred_base
    if backfill_ids:
        new_state["backfill_offset"] = backfill_offset + len(backfill_ids)
        if total_index is not None:
            new_state["backfill_complete"] = new_state["backfill_offset"] >= total_index

    status = {
        "status": "ok" if items else ("error" if errors else "empty"),
        "discovered": len(ids),
        "accepted_raw": len(items),
        "latest_ids": len(latest_ids),
        "backfill_ids": len(backfill_ids),
        "total_index": total_index,
        "resource_base": preferred_base or "",
        "used_hosts": used_hosts,
        "index_url": (preferred_base or RESOURCE_BASES[0]) + INDEX_PATH,
        "message": " | ".join(errors[:3]),
    }
    return items, new_state, status
