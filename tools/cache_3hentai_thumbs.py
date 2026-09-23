#!/usr/bin/env python3
import io
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
from PIL import Image, ImageOps

DATA = Path("docs/data.json")
STATUS = Path("docs/source_status.json")
OUTDIR = Path("docs/thumbs/3hentai")
API_BASE = os.environ.get("JANDAPRESS_URL", "http://127.0.0.1:3000").rstrip("/")
LIMIT = int(os.environ.get("HENTAI3_THUMB_LIMIT", "80"))
MAX_BYTES = int(os.environ.get("HENTAI3_THUMB_MAX_BYTES", str(15 * 1024 * 1024)))
TIMEOUT = 30
SOURCE_BASE = "https://3hentai.net"


def load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def image_candidates(value: Any, path: str = "") -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    image_ext = (".jpg", ".jpeg", ".png", ".webp", ".avif", ".gif")

    def add(raw: str, context: str):
        raw = " ".join(str(raw or "").split()).strip()
        if not raw or not raw.startswith(("http://", "https://", "//", "/")):
            return
        url = urljoin(SOURCE_BASE, raw)
        clean = url.lower().split("?", 1)[0]
        ctx = context.lower()
        score = 0
        if "thumbnail" in ctx or "thumb" in ctx:
            score += 120
        if "cover" in ctx:
            score += 110
        if "preview" in ctx or "poster" in ctx:
            score += 90
        if any(k in ctx for k in ("image", "images", "picture")):
            score += 75
        if any(k in ctx for k in ("page", "pages", "file", "files", "media")):
            score += 35
        if clean.endswith(image_ext):
            score += 50
        if "3hentai" in clean:
            score += 5
        if score >= 50:
            out.append((score, url))

    def walk(v: Any, context: str):
        if isinstance(v, str):
            add(v, context)
        elif isinstance(v, dict):
            for k, vv in v.items():
                walk(vv, f"{context}.{k}" if context else str(k))
        elif isinstance(v, list):
            for i, vv in enumerate(v):
                walk(vv, f"{context}[{i}]")

    walk(value, path)
    seen = set()
    result = []
    for score, url in sorted(out, key=lambda x: x[0], reverse=True):
        if url in seen:
            continue
        seen.add(url)
        result.append((score, url))
    return result


def fetch_api_candidates(session: requests.Session, sid: str) -> list[str]:
    r = session.get(f"{API_BASE}/3hentai/get", params={"book": sid}, timeout=TIMEOUT)
    r.raise_for_status()
    payload = r.json()
    return [u for _, u in image_candidates(payload)]


def download_thumbnail(session: requests.Session, urls: list[str]) -> tuple[bytes | None, str]:
    last = ""
    for url in urls[:8]:
        try:
            r = session.get(url, timeout=TIMEOUT, stream=True)
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
                    return data, url
        except Exception as e:
            last = f"{url}: {e}"
    return None, last


def main() -> int:
    if not DATA.exists():
        print("[3hentai-thumbs] docs/data.json not found")
        return 0

    data = load(DATA, {"items": []})
    items = [x for x in data.get("items", []) if isinstance(x, dict) and x.get("source") == "3hentai"]
    OUTDIR.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update({"User-Agent": "Japanese-Metadata-Library/1.8 thumbnail-cache"})

    attempted = cached = reused = failed = 0
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
        if attempted >= LIMIT:
            continue
        attempted += 1

        urls: list[str] = []
        current = str(item.get("thumbnail") or "")
        if current.startswith(("http://", "https://")):
            urls.append(current)
        try:
            for u in fetch_api_candidates(session, sid):
                if u not in urls:
                    urls.append(u)
        except Exception as e:
            if len(failures) < 8:
                failures.append(f"{sid} api: {e}")

        data_bytes, used = download_thumbnail(session, urls)
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

    save(DATA, data)
    status = load(STATUS, {})
    status.setdefault("3hentai", {})["thumbnail_cache"] = {
        "attempted": attempted,
        "cached": cached,
        "reused": reused,
        "failed": failed,
        "pending": max(0, len(items) - reused - cached),
        "samples": samples,
        "failures": failures,
    }
    save(STATUS, status)
    print(f"[3hentai-thumbs] total={len(items)} attempted={attempted} cached={cached} reused={reused} failed={failed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
