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
    rate_limited = False
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

        # Revision 11 may intentionally store a title-search fallback when the
        # API payload did not contain a verified /d/<id> URL. Never scrape that
        # search result page as if it were this work's gallery: doing so could
        # attach another work's cover. Only direct, verified gallery URLs are
        # page-scraped. Search-fallback rows may still use an image URL already
        # present in the search payload.
        if source_url_kind == "direct" and page_url.startswith(("http://", "https://")):
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
        "mode": "public-gallery-page",
        "attempted": attempted,
        "cached": cached,
        "reused": reused,
        "failed": failed,
        "pending": max(0, len(items) - reused - cached),
        "rate_limited": rate_limited,
        "samples": samples,
        "failures": failures,
    }
    save(STATUS, status)
    print(
        f"[3hentai-thumbs] total={len(items)} attempted={attempted} cached={cached} "
        f"reused={reused} failed={failed} rate_limited={rate_limited}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
