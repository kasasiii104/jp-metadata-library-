import re
import time
from typing import Any

import requests

from config import EH_BACKFILL_PAGES, EH_LATEST_PAGES, EH_MAX_GALLERIES_PER_RUN
from sources.common import absolute_url, iso_from_unix, safe_get, safe_post_json, unique_strings

BASE = "https://e-hentai.org/"
API = "https://api.e-hentai.org/api.php"
GALLERY_RE = re.compile(r"/g/(\d+)/([0-9a-fA-F]{10})/?")


def _discover(session: requests.Session, page: int) -> list[tuple[int, str]]:
    # E-Hentai search requests are rate-limited; keep this to at most a few requests/run.
    res = safe_get(session, BASE, params={"f_search": "language:japanese$", "page": page})
    found: list[tuple[int, str]] = []
    seen = set()
    for match in GALLERY_RE.finditer(res.text):
        key = (int(match.group(1)), match.group(2).lower())
        if key not in seen:
            seen.add(key)
            found.append(key)
    return found


def _split_namespaced(tags: list[str], namespace: str) -> list[str]:
    prefix = namespace + ":"
    return unique_strings(t[len(prefix):] for t in tags if t.lower().startswith(prefix))


def _normalize(meta: dict[str, Any]) -> dict[str, Any] | None:
    if meta.get("error") or meta.get("expunged"):
        return None
    gid = str(meta.get("gid") or "")
    token = str(meta.get("token") or meta.get("current_key") or "")
    tags = unique_strings(meta.get("tags") or [])
    low = {t.lower() for t in tags}
    language = "japanese" if "language:japanese" in low else ""
    if not gid or not token:
        return None
    return {
        "uid": f"ehentai:{gid}",
        "source": "ehentai",
        "source_id": gid,
        "source_token": token,
        "source_url": f"https://e-hentai.org/g/{gid}/{token}/",
        "title": str(meta.get("title") or "").strip(),
        "title_jp": str(meta.get("title_jpn") or "").strip(),
        "language": language,
        "category": str(meta.get("category") or "").strip(),
        "artists": _split_namespaced(tags, "artist"),
        "groups": _split_namespaced(tags, "group"),
        "parodies": _split_namespaced(tags, "parody"),
        "characters": _split_namespaced(tags, "character"),
        "tags": tags,
        "pages": int(meta.get("filecount") or 0),
        "rating": float(meta.get("rating") or 0) if str(meta.get("rating") or "").strip() else None,
        "popularity": None,
        "posted_at": iso_from_unix(meta.get("posted")) if meta.get("posted") else "",
        "thumbnail": absolute_url(str(meta.get("thumb") or "")),
    }


def _metadata(session: requests.Session, refs: list[tuple[int, str]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for idx in range(0, len(refs), 25):
        batch = refs[idx : idx + 25]
        payload = {
            "method": "gdata",
            "gidlist": [[gid, token] for gid, token in batch],
            "namespace": 1,
        }
        data = safe_post_json(session, API, payload)
        token_by_gid = {str(gid): token for gid, token in batch}
        for raw in data.get("gmetadata") or []:
            if isinstance(raw, dict) and not raw.get("token"):
                raw = dict(raw)
                raw["token"] = token_by_gid.get(str(raw.get("gid") or ""), "")
            item = _normalize(raw)
            if item:
                out.append(item)
        # Official docs recommend a pause after several sequential API calls.
        if idx and idx % 100 == 0:
            time.sleep(5.0)
    return out


def collect(state: dict | None = None) -> tuple[list[dict], dict, dict]:
    state = dict(state or {})
    backfill_page = int(state.get("backfill_page") or 1)
    session = requests.Session()
    refs: list[tuple[int, str]] = []
    errors: list[str] = []

    latest_pages = list(range(0, EH_LATEST_PAGES))
    historical_pages = list(range(backfill_page, backfill_page + EH_BACKFILL_PAGES))
    pages = []
    for p in latest_pages + historical_pages:
        if p not in pages:
            pages.append(p)

    for i, page in enumerate(pages):
        try:
            refs.extend(_discover(session, page))
        except Exception as e:
            errors.append(f"page {page}: {e}")
        if i < len(pages) - 1:
            time.sleep(3.2)

    unique_refs = []
    seen = set()
    for ref in refs:
        if ref in seen:
            continue
        seen.add(ref)
        unique_refs.append(ref)
        if len(unique_refs) >= EH_MAX_GALLERIES_PER_RUN:
            break

    items: list[dict] = []
    if unique_refs:
        try:
            items = _metadata(session, unique_refs)
        except Exception as e:
            errors.append(f"gdata: {e}")

    new_state = dict(state)
    if not errors or items:
        new_state["backfill_page"] = backfill_page + EH_BACKFILL_PAGES

    status = {
        "status": "ok" if items else ("error" if errors else "empty"),
        "discovered": len(unique_refs),
        "accepted_raw": len(items),
        "artists_found": sum(1 for x in items if x.get("artists")),
        "groups_found": sum(1 for x in items if x.get("groups")),
        "works_found": sum(1 for x in items if x.get("parodies")),
        "characters_found": sum(1 for x in items if x.get("characters")),
        "tagged_items": sum(1 for x in items if x.get("tags")),
        "message": " | ".join(errors[:3]),
    }
    return items, new_state, status
