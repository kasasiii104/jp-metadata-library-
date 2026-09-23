import re
import time
from datetime import datetime, timezone
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


def _nearest_card(anchor: Tag) -> Tag:
    node: Tag | None = anchor
    best = anchor
    for _ in range(6):
        parent = node.parent if isinstance(node, Tag) else None
        if not isinstance(parent, Tag):
            break
        best = parent
        # A compact parent containing an image and only a few gallery links is usually the card.
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
    params = {"sort": "newest"}
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


def _text_after_label(soup: BeautifulSoup, labels: tuple[str, ...]) -> str:
    labels_l = tuple(x.lower() for x in labels)
    for node in soup.find_all(["th", "dt", "strong", "b", "span", "div"]):
        text = node.get_text(" ", strip=True).strip().rstrip(":").lower()
        if text not in labels_l:
            continue
        # table cell
        if node.name == "th" and node.find_next_sibling("td"):
            return node.find_next_sibling("td").get_text(" ", strip=True)
        # definition list
        if node.name == "dt" and node.find_next_sibling("dd"):
            return node.find_next_sibling("dd").get_text(" ", strip=True)
        # generic sibling
        sib = node.find_next_sibling()
        if isinstance(sib, Tag):
            value = sib.get_text(" ", strip=True)
            if value:
                return value
        parent = node.parent if isinstance(node.parent, Tag) else None
        if parent:
            whole = parent.get_text(" ", strip=True)
            for label in labels:
                whole = re.sub(rf"^\s*{re.escape(label)}\s*:?[\s-]*", "", whole, flags=re.I)
            if whole and whole.lower() != text:
                return whole.strip()
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


def _detect_language(soup: BeautifulSoup) -> str:
    # Prefer explicit language links/metadata instead of page UI language.
    for a in soup.find_all("a", href=True):
        href = str(a.get("href") or "").lower()
        text = a.get_text(" ", strip=True).lower()
        if "language" not in href and "/lang/" not in href:
            continue
        if "japanese" in href or text in {"japanese", "日本語", "ja"}:
            return "japanese"
        if "english" in href or text == "english":
            return "english"
        if "chinese" in href or text in {"chinese", "中文"}:
            return "chinese"

    value = _text_after_label(soup, ("Language", "Languages", "言語"))
    low = value.lower()
    if "japanese" in low or "日本語" in value:
        return "japanese"
    if "english" in low:
        return "english"
    if "chinese" in low or "中文" in value:
        return "chinese"

    # Some Pururin pages expose language as an ordinary tag.
    for text in _links_by_path(soup, ("/tag/", "/tags/")):
        low = text.lower()
        if low == "japanese" or text == "日本語":
            return "japanese"
        if low == "english":
            return "english"
        if low == "chinese":
            return "chinese"
    return ""


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
    # Structured microdata first.
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
    raw = _text_after_label(soup, ("Uploaded", "Posted", "Published", "Added", "Date", "投稿日"))
    return raw


def _normalize_detail(ref: dict[str, Any], soup: BeautifulSoup) -> dict[str, Any]:
    gid = ref["gid"]
    language = _detect_language(soup)
    title = _title(soup, ref.get("title_hint") or "")
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
        m = re.search(r"(?:Pages?|Length)\s*:?[\s-]*(\d{1,5})", whole, re.I)
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
    for index, ref in enumerate(unique_refs):
        try:
            item = _fetch_detail(session, ref)
            if item:
                items.append(item)
        except Exception as e:
            detail_errors += 1
            if len(errors) < 6:
                errors.append(f"gallery {ref['gid']}: {e}")
        if index < len(unique_refs) - 1 and PURURIN_DETAIL_SLEEP_SEC > 0:
            time.sleep(PURURIN_DETAIL_SLEEP_SEC)

    new_state = dict(state)
    # Advance historical cursor only when its page was successfully discoverable.
    historical_ok = any(f"page={backfill_page} found=" in d and "found=0" not in d for d in page_debug)
    if historical_ok:
        new_state["backfill_page"] = backfill_page + PURURIN_BACKFILL_PAGES

    status = {
        "status": "ok" if items else ("error" if errors else "empty"),
        "discovered": len(unique_refs),
        "accepted_raw": len(items),
        "browse_url": BROWSE_URL,
        "detail_errors": detail_errors,
        "debug": " | ".join(page_debug[:3]),
        "message": " | ".join(errors[:3]),
    }
    return items, new_state, status
