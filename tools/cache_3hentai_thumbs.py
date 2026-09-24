#!/usr/bin/env python3
"""Cache a small number of 3Hentai cover/page thumbnails per run.

Revision 9 intentionally does not call Jandapress /3hentai/get.  Search already
works in the current environment, while get returns 400 for fresh galleries and
bulk get traffic can cause upstream 429s.  Instead we visit the public gallery
page at a low rate and cache one publicly exposed thumbnail locally.
"""
import io
import json
import os
import time
import re
import unicodedata
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageOps

DATA = Path("docs/data.json")
STATUS = Path("docs/source_status.json")
OUTDIR = Path("docs/thumbs/3hentai")
SOURCE_BASE = "https://3hentai.net"
LIMIT = int(os.environ.get("HENTAI3_THUMB_LIMIT", "12"))
DELAY = float(os.environ.get("HENTAI3_THUMB_DELAY_SEC", "2.0"))
MAX_BYTES = int(os.environ.get("HENTAI3_THUMB_MAX_BYTES", str(15 * 1024 * 1024)))
TIMEOUT = 30


def load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def absolute(base: str, value: str) -> str:
    value = " ".join(str(value or "").split()).strip()
    if not value:
        return ""
    if value.startswith("//"):
        return "https:" + value
    return urljoin(base, value)



def title_key(value: str) -> str:
    text = unicodedata.normalize("NFKC", " ".join(str(value or "").split())).casefold()
    return re.sub(r"[\W_]+", "", text, flags=re.UNICODE)


def search_cards(html: str, page_url: str) -> list[dict]:
    """Extract public gallery links + lazy thumbnails from a search page."""
    soup = BeautifulSoup(html, "html.parser")
    anchors = []
    seen_nodes = set()
    for selector in (
        ".listing-container a.cover",
        ".listing-galleries-container .gallery-wrapper .gallery-thumb",
        "a.cover[href*='/d/']",
        "a.gallery-thumb[href*='/d/']",
    ):
        for node in soup.select(selector):
            ident = id(node)
            if ident not in seen_nodes:
                seen_nodes.add(ident)
                anchors.append(node)
    if not anchors:
        for node in soup.select("a[href*='/d/']"):
            if node.select_one("img") is not None:
                anchors.append(node)

    out = []
    seen = set()
    for a in anchors:
        href = " ".join(str(a.get("href") or "").split())
        direct = absolute(page_url, href)
        m = re.search(r"/d/(\d+)(?:/|$|[?#])", direct, re.I)
        if not m or m.group(1) in seen:
            continue
        seen.add(m.group(1))
        img = a.select_one("img")
        thumb = ""
        title = ""
        if img is not None:
            thumb = absolute(page_url, img.get("data-src") or img.get("data-original") or img.get("src") or "")
            title = " ".join(str(img.get("alt") or img.get("title") or "").split())
        if not title:
            title = " ".join(str(a.get("title") or a.get_text(" ", strip=True) or "").split())
        if not title and getattr(a, "parent", None) is not None:
            parent = a.parent
            caption = parent.select_one(".caption, .related-title, .gallery-title, .title") if hasattr(parent, "select_one") else None
            if caption is not None:
                title = " ".join(caption.get_text(" ", strip=True).split())
        out.append({"gallery_id": m.group(1), "title": title, "source_url": direct, "thumbnail": thumb})
    return out


def resolve_search_fallback(session: requests.Session, search_url: str, expected_title: str) -> tuple[dict | None, str, bool]:
    """Resolve one legacy title-search URL by an exact normalized-title match."""
    try:
        r = session.get(search_url, timeout=TIMEOUT)
        if r.status_code == 429:
            return None, f"429 from search page: {search_url}", True
        if r.status_code in {403, 502, 503, 504}:
            return None, f"HTTP {r.status_code} from search page: {search_url}", True
        r.raise_for_status()
        wanted = title_key(expected_title)
        if not wanted:
            return None, "empty expected title", False
        exact = [c for c in search_cards(r.text, r.url) if title_key(c.get("title")) == wanted]
        if len(exact) == 1:
            return exact[0], "", False
        if len(exact) > 1:
            return None, f"ambiguous exact-title matches={len(exact)}", False
        return None, "no exact-title match on search page", False
    except Exception as e:
        return None, f"search page: {e}", False


def page_image_candidates(html: str, page_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    out: list[str] = []

    # Metadata first: normally the best representative image.
    for selector, attr in (
        ('meta[property="og:image"]', "content"),
        ('meta[name="twitter:image"]', "content"),
        ('meta[property="twitter:image"]', "content"),
    ):
        node = soup.select_one(selector)
        if node:
            url = absolute(page_url, node.get(attr, ""))
            if url:
                out.append(url)

    # 3Hentai currently exposes lazy thumbnails with data-src on gallery cards
    # and page thumbs.  A first page thumbnail is a perfectly adequate card
    # image when a dedicated cover is absent.
    selectors = (
        ".gallery-cover img",
        ".cover img",
        ".gallery-thumb img",
        ".single-thumb img",
        "img.lazy",
        "img[data-src]",
        "img[src]",
    )
    for sel in selectors:
        for img in soup.select(sel)[:8]:
            raw = img.get("data-src") or img.get("data-original") or img.get("src") or ""
            url = absolute(page_url, raw)
            if url:
                out.append(url)

    seen = set()
    unique = []
    for u in out:
        if u in seen:
            continue
        seen.add(u)
        unique.append(u)
    return unique


def fetch_page_candidates(session: requests.Session, page_url: str) -> tuple[list[str], str, bool]:
    try:
        r = session.get(page_url, timeout=TIMEOUT)
        if r.status_code == 429:
            return [], f"429 from gallery page: {page_url}", True
        r.raise_for_status()
        return page_image_candidates(r.text, r.url), "", False
    except Exception as e:
        return [], f"gallery page: {e}", False


def download_thumbnail(session: requests.Session, urls: list[str]) -> tuple[bytes | None, str, bool]:
    last = ""
    for url in urls[:8]:
        try:
            r = session.get(url, timeout=TIMEOUT, stream=True)
            if r.status_code == 429:
                return None, f"429 from image: {url}", True
            r.raise_for_status()
            ctype = (r.headers.get("content-type") or "").lower()
            raw = r.raw.read(MAX_BYTES + 1, decode_content=True)
            if len(raw) > MAX_BYTES:
                last = f"too large: {url}"
                continue
            if not raw or ("image/" not in ctype and len(raw) < 1024):
                last = f"not image: {url} type={ctype} bytes={len(raw)}"
                continue
            with Image.open(io.BytesIO(raw)) as im:
                im = ImageOps.exif_transpose(im)
                if im.mode not in ("RGB", "RGBA"):
                    im = im.convert("RGB")
                if im.mode == "RGBA":
                    bg = Image.new("RGB", im.size, "white")
                    bg.paste(im, mask=im.getchannel("A"))
                    im = bg
                im.thumbnail((520, 760), Image.Resampling.LANCZOS)
                buf = io.BytesIO()
                im.save(buf, "WEBP", quality=78, method=4)
                data = buf.getvalue()
                if len(data) > 512:
                    return data, url, False
        except Exception as e:
            last = f"{url}: {e}"
    return None, last, False


def main() -> int:
    if not DATA.exists():
        print("[3hentai-thumbs] docs/data.json not found")
        return 0

    data = load(DATA, {"items": []})
    items = [x for x in data.get("items", []) if isinstance(x, dict) and x.get("source") == "3hentai"]
    OUTDIR.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (compatible; Japanese-Metadata-Library/1.9; +https://github.com/)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "ja,en;q=0.8",
    })

    attempted = cached = reused = failed = 0
    resolved_search_fallbacks = 0
    rate_limited = False
    upstream_unavailable = False
    failures: list[str] = []
    samples: list[str] = []

    for item in items:
        sid = str(item.get("source_id") or "").strip()
        if not sid:
            continue
        rel = f"thumbs/3hentai/{sid}.webp"
        dest = Path("docs") / rel
        if dest.exists() and dest.stat().st_size > 512:
            item["thumbnail"] = rel
            reused += 1
            continue
        if attempted >= LIMIT or rate_limited:
            continue

        attempted += 1
        page_url = str(item.get("source_url") or "").strip()
        source_url_kind = str(item.get("source_url_kind") or "").strip().lower()

        urls: list[str] = []
        current = str(item.get("thumbnail") or "").strip()
        if current.startswith(("http://", "https://")):
            urls.append(current)

        # Legacy Revision 11-16 rows may only have a title-search fallback.
        # Resolve that page conservatively: only a single exact normalized-title
        # match is accepted, so another work's cover is never guessed by order.
        if source_url_kind != "direct" and "/search" in page_url:
            card, resolve_error, stop = resolve_search_fallback(session, page_url, str(item.get("title") or ""))
            if resolve_error and len(failures) < 8:
                failures.append(f"{sid}: {resolve_error}")
            if stop:
                rate_limited = "429" in resolve_error
                upstream_unavailable = not rate_limited
                break
            if card:
                page_url = str(card.get("source_url") or "").strip()
                item["source_url"] = page_url
                item["source_url_kind"] = "direct"
                if card.get("gallery_id"):
                    item["resolved_gallery_id"] = str(card["gallery_id"])
                thumb = str(card.get("thumbnail") or "").strip()
                if thumb:
                    item["thumbnail"] = thumb
                    if thumb not in urls:
                        urls.append(thumb)
                source_url_kind = "direct"
                resolved_search_fallbacks += 1

        # For verified direct pages, only visit the gallery page when search/API
        # did not already provide an image URL.
        if source_url_kind == "direct" and page_url.startswith(("http://", "https://")) and not urls:
            page_urls, page_error, page_limited = fetch_page_candidates(session, page_url)
            if page_error and len(failures) < 8:
                failures.append(f"{sid}: {page_error}")
            if page_limited:
                rate_limited = True
                break
            for u in page_urls:
                if u not in urls:
                    urls.append(u)

        data_bytes, used, image_limited = download_thumbnail(session, urls)
        if image_limited:
            rate_limited = True
            if len(failures) < 8:
                failures.append(f"{sid}: {used}")
            break

        if data_bytes:
            dest.write_bytes(data_bytes)
            item["thumbnail"] = rel
            cached += 1
            if len(samples) < 5:
                samples.append(f"{sid}: {used} -> {rel}")
            print(f"[3hentai-thumbs] cached {sid} -> {rel} ({len(data_bytes)} bytes)")
        else:
            failed += 1
            if len(failures) < 8:
                failures.append(f"{sid}: {used or 'no image candidate'}")

        # Respect the upstream.  Do not hammer through 429s; hourly runs can
        # gradually fill the local cache.
        time.sleep(max(0.0, DELAY))

    save(DATA, data)
    status = load(STATUS, {})
    src = status.setdefault("3hentai", {})
    src["thumbnail_cache"] = {
        "mode": "public-search-exact-title+gallery",
        "attempted": attempted,
        "cached": cached,
        "reused": reused,
        "failed": failed,
        "pending": max(0, len(items) - reused - cached),
        "resolved_search_fallbacks": resolved_search_fallbacks,
        "rate_limited": rate_limited,
        "upstream_unavailable": upstream_unavailable,
        "samples": samples,
        "failures": failures,
    }
    save(STATUS, status)
    print(
        f"[3hentai-thumbs] total={len(items)} attempted={attempted} cached={cached} "
        f"reused={reused} failed={failed} resolved={resolved_search_fallbacks} "
        f"rate_limited={rate_limited} upstream_unavailable={upstream_unavailable}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
