import re
import time
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from config import EH_BACKFILL_PAGES, EH_LATEST_PAGES, EH_MAX_GALLERIES_PER_RUN
from sources.common import absolute_url, iso_from_unix, safe_get, safe_post_json, unique_strings

BASE = "https://e-hentai.org/"
API = "https://api.e-hentai.org/api.php"
SEARCH_QUERY = "language:japanese$"
GALLERY_RE = re.compile(r"/g/(\d+)/([0-9a-fA-F]{10})/?")


def _refs_from_html(html: str) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    seen = set()
    for match in GALLERY_RE.finditer(html or ""):
        key = (int(match.group(1)), match.group(2).lower())
        if key not in seen:
            seen.add(key)
            found.append(key)
    return found


def _cursor_from_href(href: str) -> str:
    if not href:
        return ""
    try:
        q = parse_qs(urlparse(urljoin(BASE, href)).query)
        vals = q.get("next") or []
        if vals and str(vals[0]).isdigit():
            return str(vals[0])
    except Exception:
        pass
    m = re.search(r"(?:[?&]|&amp;)next=(\d+)", href)
    return m.group(1) if m else ""


def _extract_next_cursor(html: str) -> str:
    """Extract E-Hentai's current keyset/cursor pagination token.

    Modern E-Hentai list/search pagination uses a `next=<gallery id>` cursor.
    Prefer the explicit Next controls and only then fall back to a text-labelled
    next link. Do not use the old numeric `page=N` assumption for backfill.
    """
    soup = BeautifulSoup(html or "", "html.parser")

    # Known E-Hentai pager ids / shapes. Different layouts can expose either.
    for selector in ("#dnext[href]", "#unext[href]", "a#dnext[href]", "a#unext[href]"):
        el = soup.select_one(selector)
        if el:
            cur = _cursor_from_href(el.get("href") or "")
            if cur:
                return cur

    # Some layouts wrap the anchor in a cell/div with the id.
    for selector in ("#dnext a[href]", "#unext a[href]"):
        el = soup.select_one(selector)
        if el:
            cur = _cursor_from_href(el.get("href") or "")
            if cur:
                return cur

    # Conservative fallback: require next-like label/id/class so a `prev=` or
    # unrelated cursor link is never mistaken for forward pagination.
    for a in soup.find_all("a", href=True):
        href = a.get("href") or ""
        if "next=" not in href and "next%3D" not in href.lower():
            continue
        marker = " ".join(
            [
                a.get_text(" ", strip=True),
                str(a.get("id") or ""),
                " ".join(a.get("class") or []),
                str(a.get("title") or ""),
            ]
        ).lower()
        if any(x in marker for x in ("next", "older", ">", "›", "»")):
            cur = _cursor_from_href(href)
            if cur:
                return cur

    return ""


def _discover(session: requests.Session, cursor: str = "", stage: str = "") -> tuple[list[tuple[int, str]], str, dict]:
    params = {"f_search": SEARCH_QUERY}
    if cursor:
        params["next"] = cursor

    res = safe_get(session, BASE, params=params)
    found = _refs_from_html(res.text)
    next_cursor = _extract_next_cursor(res.text)
    debug = {
        "stage": stage,
        "requested_cursor": cursor or "<latest>",
        "final_url": str(getattr(res, "url", "") or ""),
        "found_count": len(found),
        "first_gid": str(found[0][0]) if found else "",
        "last_gid": str(found[-1][0]) if found else "",
        "next_cursor": next_cursor,
    }
    return found, next_cursor, debug


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
        # Avoid hammering the metadata API during a larger backfill.
        if idx and idx % 100 == 0:
            time.sleep(5.0)
    return out


def _append_unique_refs(target: list[tuple[int, str]], page_refs: list[tuple[int, str]], seen: set, limit: int) -> int:
    added = 0
    for ref in page_refs:
        if ref in seen:
            continue
        if len(target) >= limit:
            break
        seen.add(ref)
        target.append(ref)
        added += 1
    return added


def collect(state: dict | None = None) -> tuple[list[dict], dict, dict]:
    state = dict(state or {})
    session = requests.Session()
    refs: list[tuple[int, str]] = []
    seen_refs: set[tuple[int, str]] = set()
    errors: list[str] = []
    page_debug: list[dict] = []
    page_signatures: set[tuple[int, ...]] = set()
    repeated_page_detected = False

    # Old revisions stored a numeric page counter. E-Hentai now paginates list
    # searches by `next=<gid>` cursor, so that integer cannot be safely mapped to
    # a current cursor. We restart cursor backfill safely; UID merge prevents
    # duplicates in data.json and, unlike the old logic, we do not skip pages.
    legacy_backfill_page = state.get("backfill_page")

    # 1) Latest chain: always start from the live first page and follow the
    # site's own `next` cursor for EH_LATEST_PAGES pages.
    latest_cursor = ""
    latest_pages_fetched = 0
    latest_tail_cursor = ""

    for i in range(max(0, EH_LATEST_PAGES)):
        try:
            page_refs, next_cursor, dbg = _discover(session, latest_cursor, f"latest:{i + 1}")
            sig = tuple(gid for gid, _ in page_refs)
            dbg["unique_new_count"] = 0
            if sig and sig in page_signatures:
                dbg["repeated_page"] = True
                repeated_page_detected = True
                page_debug.append(dbg)
                break
            if sig:
                page_signatures.add(sig)
            added = _append_unique_refs(refs, page_refs, seen_refs, EH_MAX_GALLERIES_PER_RUN)
            dbg["unique_new_count"] = added
            dbg["repeated_page"] = False
            page_debug.append(dbg)
            latest_pages_fetched += 1
            latest_tail_cursor = next_cursor
            if not next_cursor or next_cursor == latest_cursor or len(refs) >= EH_MAX_GALLERIES_PER_RUN:
                break
            latest_cursor = next_cursor
        except Exception as e:
            errors.append(f"latest {i + 1}: {e}")
            break
        if i < EH_LATEST_PAGES - 1:
            time.sleep(3.2)

    # 2) Historical cursor. On the first run after migration, start immediately
    # after the latest chain. On later runs continue exactly where the previous
    # successful backfill stopped.
    saved_backfill_cursor = str(state.get("backfill_next") or "")
    backfill_cursor = saved_backfill_cursor or latest_tail_cursor
    backfill_cursor_before = backfill_cursor
    backfill_pages_fetched = 0
    backfill_advance_cursor = saved_backfill_cursor
    backfill_failed = False

    for i in range(max(0, EH_BACKFILL_PAGES)):
        if not backfill_cursor or len(refs) >= EH_MAX_GALLERIES_PER_RUN:
            break
        try:
            page_refs, next_cursor, dbg = _discover(session, backfill_cursor, f"backfill:{i + 1}")
            sig = tuple(gid for gid, _ in page_refs)
            dbg["unique_new_count"] = 0
            if sig and sig in page_signatures:
                dbg["repeated_page"] = True
                repeated_page_detected = True
                page_debug.append(dbg)
                backfill_failed = True
                break
            if sig:
                page_signatures.add(sig)

            # Never advance past a page we did not actually include because of
            # the per-run cap. This avoids silently skipping historical items.
            remaining = max(0, EH_MAX_GALLERIES_PER_RUN - len(refs))
            unique_page_refs = [r for r in page_refs if r not in seen_refs]
            if unique_page_refs and len(unique_page_refs) > remaining:
                dbg["cap_blocked_page"] = True
                dbg["repeated_page"] = False
                page_debug.append(dbg)
                break

            added = _append_unique_refs(refs, page_refs, seen_refs, EH_MAX_GALLERIES_PER_RUN)
            dbg["unique_new_count"] = added
            dbg["repeated_page"] = False
            dbg["cap_blocked_page"] = False
            page_debug.append(dbg)

            if not page_refs:
                backfill_failed = True
                break

            backfill_pages_fetched += 1
            if next_cursor and next_cursor != backfill_cursor:
                backfill_advance_cursor = next_cursor
                backfill_cursor = next_cursor
            else:
                # End of chain or malformed pager. Do not keep inventing pages.
                backfill_advance_cursor = ""
                backfill_cursor = ""
                break
        except Exception as e:
            errors.append(f"backfill {i + 1}: {e}")
            backfill_failed = True
            break
        if i < EH_BACKFILL_PAGES - 1:
            time.sleep(3.2)

    items: list[dict] = []
    metadata_ok = True
    if refs:
        try:
            items = _metadata(session, refs)
        except Exception as e:
            errors.append(f"gdata: {e}")
            metadata_ok = False

    new_state = dict(state)
    # Retire the unsafe numeric paging state only after this revision ran.
    new_state.pop("backfill_page", None)
    if metadata_ok and backfill_pages_fetched > 0 and not backfill_failed:
        if backfill_advance_cursor:
            new_state["backfill_next"] = backfill_advance_cursor
        else:
            new_state.pop("backfill_next", None)
            new_state["backfill_exhausted"] = True
    elif "backfill_next" not in new_state and saved_backfill_cursor:
        new_state["backfill_next"] = saved_backfill_cursor

    status = {
        "status": "ok" if items else ("error" if errors else "empty"),
        "pagination_mode": "next-cursor",
        "discovered": len(refs),
        "accepted_raw": len(items),
        "latest_pages_fetched": latest_pages_fetched,
        "backfill_pages_requested": EH_BACKFILL_PAGES,
        "backfill_pages_fetched": backfill_pages_fetched,
        "backfill_cursor_before": backfill_cursor_before,
        "backfill_cursor_after": str(new_state.get("backfill_next") or ""),
        "legacy_backfill_page_ignored": legacy_backfill_page if legacy_backfill_page is not None else "",
        "repeated_page_detected": repeated_page_detected,
        "page_debug": page_debug[:12],
        "artists_found": sum(1 for x in items if x.get("artists")),
        "groups_found": sum(1 for x in items if x.get("groups")),
        "works_found": sum(1 for x in items if x.get("parodies")),
        "characters_found": sum(1 for x in items if x.get("characters")),
        "tagged_items": sum(1 for x in items if x.get("tags")),
        "message": " | ".join(errors[:4]),
    }
    return items, new_state, status
