import json
import re
import time
from collections import Counter
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Tag

from config import (
    PURURIN_BACKFILL_PAGES,
    PURURIN_DETAIL_SLEEP_SEC,
    PURURIN_LATEST_PAGES,
    PURURIN_MAX_GALLERIES_PER_RUN,
)
from sources.common import safe_get, unique_strings

BASE = "https://pururin.me"
BROWSE_URL = BASE + "/browse"
GALLERY_RE = re.compile(r"/gallery/(\d+)(?:/[^?#\"']*)?", re.I)
JP_RE = re.compile(r"[ぁ-んァ-ン一-龯々〆ヶ]")
LANG_NAMES = {
    "japanese": "japanese",
    "日本語": "japanese",
    "ja": "japanese",
    "english": "english",
    "英語": "english",
    "en": "english",
    "chinese": "chinese",
    "中文": "chinese",
    "zh": "chinese",
    "korean": "korean",
    "한국어": "korean",
    "ko": "korean",
}


def _nearest_card(anchor: Tag) -> Tag:
    node: Tag | None = anchor
    best = anchor
    for _ in range(6):
        parent = node.parent if isinstance(node, Tag) else None
        if not isinstance(parent, Tag):
            break
        best = parent
        if parent.find("img") and len(parent.find_all("a", href=GALLERY_RE)) <= 3:
            return parent
        node = parent
    return best


def _img_url(node: Tag) -> str:
    img = node.find("img")
    if not img:
        return ""
    for attr in ("data-src", "data-original", "data-lazy-src", "src"):
        value = str(img.get(attr) or "").strip()
        if value and not value.startswith("data:"):
            return urljoin(BASE, value)
    srcset = str(img.get("srcset") or "").strip()
    if srcset:
        candidate = srcset.split(",")[0].strip().split(" ")[0]
        if candidate:
            return urljoin(BASE, candidate)
    return ""


def _discover_page(session: requests.Session, page: int) -> tuple[list[dict[str, Any]], str]:
    params: dict[str, Any] = {"sort": "newest"}
    if page > 1:
        params["page"] = page
    res = safe_get(session, BROWSE_URL, params=params)
    soup = BeautifulSoup(res.text, "html.parser")
    title = soup.title.get_text(" ", strip=True)[:160] if soup.title else ""

    out: list[dict[str, Any]] = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = str(a.get("href") or "")
        m = GALLERY_RE.search(href)
        if not m:
            continue
        gid = m.group(1)
        if gid in seen:
            continue
        seen.add(gid)
        card = _nearest_card(a)
        raw_title = str(a.get("title") or a.get_text(" ", strip=True) or "").strip()
        if not raw_title:
            img = card.find("img")
            raw_title = str((img.get("alt") if img else "") or "").strip()
        out.append(
            {
                "gid": gid,
                "url": urljoin(BASE, href),
                "title_hint": raw_title,
                "thumb_hint": _img_url(card),
            }
        )
    return out, title


def _clean_label(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().strip(":：").lower()


def _text_after_label(soup: BeautifulSoup, labels: tuple[str, ...]) -> str:
    labels_l = tuple(_clean_label(x) for x in labels)
    for node in soup.find_all(["th", "dt", "strong", "b", "span", "div", "li"]):
        text_raw = node.get_text(" ", strip=True)
        text = _clean_label(text_raw)
        if not any(text == label or text.startswith(label + " ") for label in labels_l):
            continue
        if node.name == "th" and node.find_next_sibling("td"):
            return node.find_next_sibling("td").get_text(" ", strip=True)
        if node.name == "dt" and node.find_next_sibling("dd"):
            return node.find_next_sibling("dd").get_text(" ", strip=True)
        sib = node.find_next_sibling()
        if isinstance(sib, Tag):
            value = sib.get_text(" ", strip=True)
            if value:
                return value
        for label in labels:
            m = re.match(rf"^\s*{re.escape(label)}\s*[:：-]?\s*(.+)$", text_raw, re.I)
            if m and m.group(1).strip():
                return m.group(1).strip()
        parent = node.parent if isinstance(node.parent, Tag) else None
        if parent:
            whole = parent.get_text(" ", strip=True)
            for label in labels:
                m = re.search(rf"{re.escape(label)}\s*[:：-]?\s*([^|•·]+)", whole, re.I)
                if m and m.group(1).strip():
                    return m.group(1).strip()
    return ""


def _links_by_path(soup: BeautifulSoup, fragments: tuple[str, ...]) -> list[str]:
    values: list[str] = []
    for a in soup.find_all("a", href=True):
        href = str(a.get("href") or "").lower()
        if any(fragment in href for fragment in fragments):
            text = a.get_text(" ", strip=True)
            if text:
                values.append(text)
    return unique_strings(values)


def _norm_lang(value: str) -> str:
    text = _clean_label(value)
    if text in LANG_NAMES:
        return LANG_NAMES[text]
    for key, normalized in LANG_NAMES.items():
        if re.search(rf"(^|[^a-z]){re.escape(key)}([^a-z]|$)", text, re.I):
            return normalized
    return ""


def _language_from_structured_data(soup: BeautifulSoup) -> tuple[str, str]:
    for node in soup.select("[data-language], [data-lang], [itemprop='inLanguage']"):
        for attr in ("data-language", "data-lang", "content"):
            value = str(node.get(attr) or "").strip()
            lang = _norm_lang(value)
            if lang:
                return lang, f"attribute:{attr}"
        lang = _norm_lang(node.get_text(" ", strip=True))
        if lang:
            return lang, "itemprop-text"

    for meta in soup.find_all("meta"):
        name = str(meta.get("name") or meta.get("property") or "").lower()
        if "language" in name or name.endswith(":locale"):
            value = str(meta.get("content") or "")
            lang = _norm_lang(value)
            if lang:
                return lang, f"meta:{name}"

    for script in soup.find_all("script"):
        typ = str(script.get("type") or "").lower()
        text = script.string or script.get_text(" ", strip=True) or ""
        if not text:
            continue
        if "ld+json" in typ:
            try:
                payload = json.loads(text)
                stack = payload if isinstance(payload, list) else [payload]
                for obj in stack:
                    if isinstance(obj, dict):
                        value = obj.get("inLanguage") or obj.get("language")
                        lang = _norm_lang(str(value or ""))
                        if lang:
                            return lang, "jsonld"
            except Exception:
                pass
        for pat in (
            r'["\'](?:language|lang|inLanguage)["\']\s*:\s*["\']([^"\']+)["\']',
            r'language\s*[=:]\s*["\']([^"\']+)["\']',
        ):
            m = re.search(pat, text, re.I)
            if m:
                lang = _norm_lang(m.group(1))
                if lang:
                    return lang, "script"
    return "", ""


def _detect_language(soup: BeautifulSoup, title: str) -> tuple[str, str]:
    lang, evidence = _language_from_structured_data(soup)
    if lang:
        return lang, evidence

    # Explicit language links or exact-value anchors.
    for a in soup.find_all("a", href=True):
        href = str(a.get("href") or "").lower()
        text = a.get_text(" ", strip=True)
        if any(x in href for x in ("/language/", "/languages/", "/lang/", "language=")):
            lang = _norm_lang(href + " " + text)
            if lang:
                return lang, "language-link"
        if _clean_label(text) in LANG_NAMES:
            # Exact language anchors are strong evidence on gallery metadata pages.
            return LANG_NAMES[_clean_label(text)], "exact-anchor"

    value = _text_after_label(soup, ("Language", "Languages", "言語"))
    lang = _norm_lang(value)
    if lang:
        return lang, "label"

    # Some layouts expose language as a tag/category.
    for text in _links_by_path(soup, ("/tag/", "/tags/", "/category/", "/categories/")):
        if _clean_label(text) in LANG_NAMES:
            return LANG_NAMES[_clean_label(text)], "tag"

    whole = soup.get_text(" ", strip=True)
    m = re.search(r"(?:Language|Languages|言語)\s*[:：-]?\s*(Japanese|日本語|English|Chinese|中文|Korean|한국어)\b", whole, re.I)
    if m:
        lang = _norm_lang(m.group(1))
        if lang:
            return lang, "page-text"

    # Conservative fallback: a clearly Japanese-script title can be treated as Japanese.
    if title and JP_RE.search(title):
        return "japanese", "title-script"

    return "", "missing"


def _title(soup: BeautifulSoup, hint: str) -> str:
    for selector in ("h1", ".gallery-title", ".title", "meta[property='og:title']"):
        node = soup.select_one(selector)
        if not node:
            continue
        value = str(node.get("content") if node.name == "meta" else node.get_text(" ", strip=True) or "").strip()
        if value:
            return value
    return hint.strip()


def _thumbnail(soup: BeautifulSoup, hint: str) -> str:
    for selector in ("meta[property='og:image']", "meta[name='twitter:image']"):
        node = soup.select_one(selector)
        if node and node.get("content"):
            return urljoin(BASE, str(node.get("content")).strip())
    if hint:
        return hint
    for selector in (".gallery-cover img", ".cover img", "main img"):
        node = soup.select_one(selector)
        if isinstance(node, Tag):
            for attr in ("data-src", "data-original", "src"):
                value = str(node.get(attr) or "").strip()
                if value and not value.startswith("data:"):
                    return urljoin(BASE, value)
    return ""


def _number_from_label(soup: BeautifulSoup, labels: tuple[str, ...]) -> int:
    value = _text_after_label(soup, labels)
    m = re.search(r"([\d,]+)", value)
    return int(m.group(1).replace(",", "")) if m else 0


def _rating(soup: BeautifulSoup) -> float | None:
    for selector in ("[itemprop='ratingValue']", "meta[itemprop='ratingValue']"):
        node = soup.select_one(selector)
        if node:
            value = str(node.get("content") or node.get_text(" ", strip=True) or "")
            m = re.search(r"([0-5](?:\.\d+)?)", value)
            if m:
                return float(m.group(1))
    value = _text_after_label(soup, ("Rating", "Score", "評価"))
    m = re.search(r"([0-5](?:\.\d+)?)", value)
    return float(m.group(1)) if m else None


def _posted_at(soup: BeautifulSoup) -> str:
    time_node = soup.find("time")
    if isinstance(time_node, Tag):
        raw = str(time_node.get("datetime") or time_node.get_text(" ", strip=True) or "").strip()
        if raw:
            return raw
    return _text_after_label(soup, ("Uploaded", "Posted", "Published", "Added", "Date", "投稿日"))


def _normalize_detail(ref: dict[str, Any], soup: BeautifulSoup) -> dict[str, Any]:
    gid = ref["gid"]
    title = _title(soup, ref.get("title_hint") or "")
    language, language_evidence = _detect_language(soup, title)
    title_jp = title if language == "japanese" and JP_RE.search(title) else ""

    artists = _links_by_path(soup, ("/artist/", "/artists/"))
    groups = _links_by_path(soup, ("/group/", "/groups/", "/circle/", "/circles/"))
    parodies = _links_by_path(soup, ("/parody/", "/parodies/", "/series/"))
    characters = _links_by_path(soup, ("/character/", "/characters/"))
    generic_tags = _links_by_path(soup, ("/tag/", "/tags/"))
    category_values = _links_by_path(soup, ("/category/", "/categories/", "/type/"))

    tags = list(generic_tags)
    if language:
        tags.append(f"language:{language}")
    category = category_values[0] if category_values else _text_after_label(soup, ("Category", "Type", "カテゴリ"))

    pages = _number_from_label(soup, ("Pages", "Page count", "Length", "ページ"))
    if not pages:
        whole = soup.get_text(" ", strip=True)
        m = re.search(r"(?:Pages?|Length)\s*:?\s*(\d{1,5})", whole, re.I)
        if m:
            pages = int(m.group(1))

    popularity = None
    for labels in (("Views", "View count", "閲覧数"), ("Favorites", "Favourites", "お気に入り")):
        n = _number_from_label(soup, labels)
        if n:
            popularity = n
            break

    return {
        "uid": f"pururin:{gid}",
        "source": "pururin",
        "source_id": str(gid),
        "source_url": ref["url"],
        "title": title,
        "title_jp": title_jp,
        "language": language,
        "language_evidence": language_evidence,
        "category": category,
        "artists": artists,
        "groups": groups,
        "parodies": parodies,
        "characters": characters,
        "tags": unique_strings(tags),
        "pages": pages,
        "rating": _rating(soup),
        "popularity": popularity,
        "posted_at": _posted_at(soup),
        "thumbnail": _thumbnail(soup, ref.get("thumb_hint") or ""),
    }


def _fetch_detail(session: requests.Session, ref: dict[str, Any]) -> dict[str, Any]:
    res = safe_get(session, ref["url"])
    soup = BeautifulSoup(res.text, "html.parser")
    return _normalize_detail(ref, soup)


def collect(state: dict | None = None) -> tuple[list[dict], dict, dict]:
    state = dict(state or {})
    backfill_page = int(state.get("backfill_page") or (PURURIN_LATEST_PAGES + 1))
    session = requests.Session()
    errors: list[str] = []
    page_debug: list[str] = []

    pages: list[int] = []
    for page in list(range(1, PURURIN_LATEST_PAGES + 1)) + list(
        range(backfill_page, backfill_page + PURURIN_BACKFILL_PAGES)
    ):
        if page not in pages:
            pages.append(page)

    refs: list[dict[str, Any]] = []
    for page in pages:
        try:
            found, title = _discover_page(session, page)
            page_debug.append(f"page={page} found={len(found)} title={title!r}")
            refs.extend(found)
        except Exception as e:
            errors.append(f"browse page={page}: {e}")

    unique_refs: list[dict[str, Any]] = []
    seen = set()
    for ref in refs:
        gid = ref["gid"]
        if gid in seen:
            continue
        seen.add(gid)
        unique_refs.append(ref)
        if len(unique_refs) >= PURURIN_MAX_GALLERIES_PER_RUN:
            break

    items: list[dict[str, Any]] = []
    detail_errors = 0
    evidence_counter: Counter[str] = Counter()
    language_counter: Counter[str] = Counter()
    sample_debug: list[str] = []

    for index, ref in enumerate(unique_refs):
        try:
            item = _fetch_detail(session, ref)
            if item:
                items.append(item)
                language = str(item.get("language") or "missing")
                evidence = str(item.get("language_evidence") or "missing")
                language_counter[language] += 1
                evidence_counter[evidence] += 1
                if len(sample_debug) < 5:
                    sample_debug.append(
                        f"{item.get('source_id')} lang={language!r} via={evidence!r} title={str(item.get('title') or '')[:70]!r}"
                    )
        except Exception as e:
            detail_errors += 1
            if len(errors) < 6:
                errors.append(f"gallery {ref['gid']}: {e}")
        if index < len(unique_refs) - 1 and PURURIN_DETAIL_SLEEP_SEC > 0:
            time.sleep(PURURIN_DETAIL_SLEEP_SEC)

    new_state = dict(state)
    historical_ok = any(f"page={backfill_page} found=" in d and "found=0" not in d for d in page_debug)
    if historical_ok:
        new_state["backfill_page"] = backfill_page + PURURIN_BACKFILL_PAGES

    status = {
        "status": "ok" if items else ("error" if errors else "empty"),
        "discovered": len(unique_refs),
        "accepted_raw": len(items),
        "browse_url": BROWSE_URL,
        "detail_errors": detail_errors,
        "languages_detected": dict(language_counter),
        "language_evidence": dict(evidence_counter),
        "language_samples": sample_debug,
        "debug": " | ".join(page_debug[:3]),
        "message": " | ".join(errors[:3]),
    }
    return items, new_state, status
