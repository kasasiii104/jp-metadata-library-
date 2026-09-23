import json
import re
import time
from typing import Any
from urllib.parse import quote_plus, urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag

from config import (
    HENTAI3_BACKFILL_PAGES,
    HENTAI3_DETAIL_SLEEP_SEC,
    HENTAI3_LATEST_PAGES,
    HENTAI3_MAX_GALLERIES_PER_RUN,
)
from sources.common import safe_get, unique_strings

BASE = "https://3hentai.net"
JP_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")

# 3Hentai is a multi-language source. Current public clients document normal
# search/get support and recent/popular sorting; site routes can change, so the
# collector deliberately keeps several ordinary public URL fallbacks and logs
# which one worked. No anti-bot bypass is attempted.
SEARCH_PATTERNS = [
    ("jp-search-query", "/search?query={query}&sort=recent&page={page}"),
    ("jp-search-q", "/search?q={query}&sort=recent&page={page}"),
    ("jp-search-key", "/search?key={query}&sort=recent&page={page}"),
]
BROWSE_PATTERNS = [
    ("browse-query-page", "/?page={page}"),
    ("browse-page-path", "/page/{page}"),
    ("browse-recent", "/search?sort=recent&page={page}"),
]

GALLERY_PATH_RE = re.compile(
    r"/(?:g|gallery|doujinshi|book|manga|read|view)/(?P<id>\d{3,10})(?:[/?#]|$)", re.I
)
PLAIN_ID_PATH_RE = re.compile(r"^/(?P<id>\d{4,10})(?:[/?#]|$)")
LANG_MAP = {
    "japanese": "japanese",
    "日本語": "japanese",
    "ja": "japanese",
    "english": "english",
    "英語": "english",
    "chinese": "chinese",
    "中文": "chinese",
    "korean": "korean",
    "한국어": "korean",
}


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _norm_lang(value: str) -> str:
    text = _clean(value).lower()
    for key, val in LANG_MAP.items():
        if text == key.lower() or re.search(rf"(?:^|[^a-z]){re.escape(key.lower())}(?:[^a-z]|$)", text):
            return val
    return ""


def _url_for(mode: str, pattern: str, page: int) -> str:
    query = quote_plus("language:japanese")
    return urljoin(BASE, pattern.format(query=query, page=page))


def _extract_gid(href: str) -> str:
    try:
        path = urlparse(href).path
    except Exception:
        path = href
    m = GALLERY_PATH_RE.search(path)
    if m:
        return m.group("id")
    m = PLAIN_ID_PATH_RE.search(path)
    return m.group("id") if m else ""


def _thumbnail_from_node(node: Tag | None) -> str:
    if not isinstance(node, Tag):
        return ""
    img = node.find("img")
    if not isinstance(img, Tag):
        return ""
    for attr in ("data-src", "data-lazy-src", "data-original", "src"):
        raw = str(img.get(attr) or "").strip()
        if raw and not raw.startswith("data:"):
            return urljoin(BASE, raw)
    return ""


def _extract_refs(soup: BeautifulSoup) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = str(a.get("href") or "").strip()
        gid = _extract_gid(href)
        if not gid or gid in seen:
            continue
        # Avoid obvious pagination/account/navigation false positives.
        low = href.lower()
        if any(x in low for x in ("/page/", "login", "register", "random", "favorite")):
            continue
        seen.add(gid)
        card = a
        for parent in a.parents:
            if not isinstance(parent, Tag):
                continue
            classes = " ".join(parent.get("class") or []).lower()
            if parent.name in {"article", "li"} or any(x in classes for x in ("card", "gallery", "item", "book", "thumb")):
                card = parent
                break
        title = _clean(a.get("title") or a.get_text(" ", strip=True))
        if not title and isinstance(card, Tag):
            img = card.find("img")
            title = _clean((img.get("alt") if isinstance(img, Tag) else "") or "")
        refs.append({
            "gid": gid,
            "url": urljoin(BASE, href),
            "title_hint": title,
            "thumb_hint": _thumbnail_from_node(card if isinstance(card, Tag) else a),
        })
    return refs


def _fetch_discovery_page(session: requests.Session, page: int, preferred: str = ""):
    candidates = SEARCH_PATTERNS + BROWSE_PATTERNS
    if preferred:
        candidates.sort(key=lambda x: 0 if x[0] == preferred else 1)
    errors = []
    for mode, pattern in candidates:
        url = _url_for(mode, pattern, page)
        try:
            res = safe_get(session, url)
            soup = BeautifulSoup(res.text, "html.parser")
            refs = _extract_refs(soup)
            title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
            if refs:
                return refs, mode, url, title, errors
            errors.append(f"{mode}: 200 but no gallery refs")
        except Exception as e:
            errors.append(f"{mode}: {e}")
    return [], preferred, "", "", errors


def _all_text_links(soup: BeautifulSoup, fragments: tuple[str, ...]) -> list[str]:
    vals = []
    for a in soup.find_all("a", href=True):
        href = str(a.get("href") or "").lower()
        if any(f in href for f in fragments):
            text = _clean(a.get_text(" ", strip=True))
            if text:
                vals.append(text)
    return unique_strings(vals)


def _detect_language(soup: BeautifulSoup, *, from_jp_search: bool) -> tuple[str, str]:
    # Strong explicit metadata first.
    for node in soup.select("[data-language],[data-lang],[itemprop='inLanguage']"):
        for raw in (node.get("data-language"), node.get("data-lang"), node.get("content"), node.get_text(" ", strip=True)):
            lang = _norm_lang(str(raw or ""))
            if lang:
                return lang, "structured"

    for a in soup.find_all("a", href=True):
        href = str(a.get("href") or "").lower()
        text = _clean(a.get_text(" ", strip=True))
        if any(x in href for x in ("language", "/lang/", "lang=")):
            lang = _norm_lang(text + " " + href)
            if lang:
                return lang, "language-link"

    whole = _clean(soup.get_text(" ", strip=True))
    m = re.search(r"(?:Language|Languages|言語)\s*[:：-]?\s*(Japanese|日本語|English|Chinese|Korean|中文|한국어)", whole, re.I)
    if m:
        lang = _norm_lang(m.group(1))
        if lang:
            return lang, "page-text"

    # If the site accepted an explicit language:japanese search query and did
    # not expose a contradictory language marker, treat it as Japanese.
    if from_jp_search:
        return "japanese", "japanese-search"
    return "", "missing"


def _title(soup: BeautifulSoup, hint: str) -> str:
    for selector in ("h1", ".title", ".gallery-title", "meta[property='og:title']", "meta[name='twitter:title']"):
        node = soup.select_one(selector)
        if not node:
            continue
        value = _clean(node.get("content") if node.name == "meta" else node.get_text(" ", strip=True))
        if value:
            return value
    return _clean(hint)


def _thumbnail(soup: BeautifulSoup, hint: str) -> str:
    for selector in ("meta[property='og:image']", "meta[name='twitter:image']"):
        node = soup.select_one(selector)
        if node and node.get("content"):
            return urljoin(BASE, str(node.get("content")).strip())
    if hint:
        return hint
    for selector in (".cover img", ".gallery img", "main img", "article img"):
        node = soup.select_one(selector)
        if isinstance(node, Tag):
            for attr in ("data-src", "data-lazy-src", "data-original", "src"):
                raw = str(node.get(attr) or "").strip()
                if raw and not raw.startswith("data:"):
                    return urljoin(BASE, raw)
    return ""


def _number(text: str) -> int:
    m = re.search(r"([\d,]+)", str(text or ""))
    return int(m.group(1).replace(",", "")) if m else 0


def _rating(soup: BeautifulSoup) -> float | None:
    for selector in ("[itemprop='ratingValue']", "meta[itemprop='ratingValue']"):
        node = soup.select_one(selector)
        if node:
            raw = str(node.get("content") or node.get_text(" ", strip=True) or "")
            m = re.search(r"([0-5](?:\.\d+)?)", raw)
            if m:
                return float(m.group(1))
    whole = _clean(soup.get_text(" ", strip=True))
    m = re.search(r"(?:Rating|Score|評価)\s*[:：-]?\s*([0-5](?:\.\d+)?)", whole, re.I)
    return float(m.group(1)) if m else None


def _pages(soup: BeautifulSoup) -> int:
    whole = _clean(soup.get_text(" ", strip=True))
    for pat in (r"(?:Pages?|Page count|ページ)\s*[:：-]?\s*([\d,]+)", r"([\d,]+)\s*(?:pages|ページ)\b"):
        m = re.search(pat, whole, re.I)
        if m:
            return int(m.group(1).replace(",", ""))
    return 0


def _posted_at(soup: BeautifulSoup) -> str:
    node = soup.find("time")
    if isinstance(node, Tag):
        raw = str(node.get("datetime") or node.get_text(" ", strip=True) or "").strip()
        if raw:
            return raw
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or script.get_text() or "{}")
            objs = data if isinstance(data, list) else [data]
            for obj in objs:
                if isinstance(obj, dict):
                    for key in ("datePublished", "dateCreated", "uploadDate"):
                        if obj.get(key):
                            return str(obj[key])
        except Exception:
            pass
    return ""


def _normalize_detail(ref: dict[str, str], soup: BeautifulSoup, *, from_jp_search: bool) -> tuple[dict[str, Any], str]:
    title = _title(soup, ref.get("title_hint") or "")
    language, evidence = _detect_language(soup, from_jp_search=from_jp_search)
    title_jp = title if language == "japanese" and JP_RE.search(title) else ""

    artists = _all_text_links(soup, ("/artist/", "/artists/"))
    groups = _all_text_links(soup, ("/group/", "/groups/", "/circle/", "/circles/"))
    parodies = _all_text_links(soup, ("/parody/", "/parodies/", "/series/"))
    characters = _all_text_links(soup, ("/character/", "/characters/"))
    generic_tags = _all_text_links(soup, ("/tag/", "/tags/"))

    tags = list(generic_tags)
    if language:
        tags.append(f"language:{language}")

    category = ""
    cats = _all_text_links(soup, ("/category/", "/categories/", "/type/"))
    if cats:
        category = cats[0]

    popularity = None
    whole = _clean(soup.get_text(" ", strip=True))
    for pat in (
        r"(?:Views?|閲覧数)\s*[:：-]?\s*([\d,]+)",
        r"(?:Favorites?|Favourites?|お気に入り)\s*[:：-]?\s*([\d,]+)",
    ):
        m = re.search(pat, whole, re.I)
        if m:
            popularity = int(m.group(1).replace(",", ""))
            break

    item = {
        "uid": f"3hentai:{ref['gid']}",
        "source": "3hentai",
        "source_id": str(ref["gid"]),
        "source_url": ref["url"],
        "title": title,
        "title_jp": title_jp,
        "language": language,
        "category": category,
        "artists": artists,
        "groups": groups,
        "parodies": parodies,
        "characters": characters,
        "tags": unique_strings(tags),
        "pages": _pages(soup),
        "rating": _rating(soup),
        "popularity": popularity,
        "posted_at": _posted_at(soup),
        "thumbnail": _thumbnail(soup, ref.get("thumb_hint") or ""),
    }
    return item, evidence


def collect(state: dict | None = None) -> tuple[list[dict], dict, dict]:
    state = dict(state or {})
    backfill_page = max(1, int(state.get("backfill_page") or 1))
    preferred_mode = str(state.get("browse_mode") or "")
    session = requests.Session()

    pages = []
    for p in list(range(1, 1 + HENTAI3_LATEST_PAGES)) + list(range(backfill_page, backfill_page + HENTAI3_BACKFILL_PAGES)):
        if p not in pages:
            pages.append(p)

    refs: list[dict[str, str]] = []
    discovery_debug = []
    discovery_errors = []
    mode_used = preferred_mode
    mode_is_jp_search = preferred_mode.startswith("jp-search")

    for page in pages:
        found, mode, url, title, errs = _fetch_discovery_page(session, page, preferred=mode_used)
        if found:
            mode_used = mode
            mode_is_jp_search = mode.startswith("jp-search")
            refs.extend(found)
            discovery_debug.append(f"page={page} mode={mode} found={len(found)} title={title!r}")
        else:
            discovery_errors.extend(errs[-3:])
        if len(refs) >= HENTAI3_MAX_GALLERIES_PER_RUN * 2:
            break
        time.sleep(0.6)

    unique_refs = []
    seen = set()
    for ref in refs:
        gid = ref.get("gid")
        if not gid or gid in seen:
            continue
        seen.add(gid)
        unique_refs.append(ref)
        if len(unique_refs) >= HENTAI3_MAX_GALLERIES_PER_RUN:
            break

    items: list[dict] = []
    detail_errors = 0
    error_samples: list[str] = []
    language_counts: dict[str, int] = {}
    language_evidence: dict[str, int] = {}
    language_samples: list[str] = []
    thumb_found = 0
    thumb_missing = 0

    for ref in unique_refs:
        try:
            res = safe_get(session, ref["url"])
            soup = BeautifulSoup(res.text, "html.parser")
            item, evidence = _normalize_detail(ref, soup, from_jp_search=mode_is_jp_search)
            lang = item.get("language") or "missing"
            language_counts[lang] = language_counts.get(lang, 0) + 1
            language_evidence[evidence] = language_evidence.get(evidence, 0) + 1
            if len(language_samples) < 5:
                language_samples.append(f"{ref['gid']} lang={lang!r} via={evidence!r} title={item.get('title','')[:80]!r}")
            if item.get("thumbnail"):
                thumb_found += 1
            else:
                thumb_missing += 1
            items.append(item)
        except Exception as e:
            detail_errors += 1
            if len(error_samples) < 5:
                error_samples.append(f"{ref.get('gid')}: {e}")
        time.sleep(HENTAI3_DETAIL_SLEEP_SEC)

    new_state = dict(state)
    if mode_used:
        new_state["browse_mode"] = mode_used
    if items or unique_refs:
        new_state["backfill_page"] = backfill_page + HENTAI3_BACKFILL_PAGES

    status = {
        "status": "ok" if items else ("error" if discovery_errors or detail_errors else "empty"),
        "discovered": len(unique_refs),
        "accepted_raw": len(items),
        "browse_mode": mode_used,
        "detail_errors": detail_errors,
        "languages_detected": language_counts,
        "language_evidence": language_evidence,
        "language_samples": language_samples,
        "thumbnails_found": thumb_found,
        "thumbnails_missing": thumb_missing,
        "debug": " | ".join(discovery_debug[:5]),
        "message": " | ".join((discovery_errors + error_samples)[:5]),
    }
    return items, new_state, status
