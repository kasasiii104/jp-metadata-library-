#!/usr/bin/env python3
import io
import os
import re
import time
from pathlib import Path

import requests
from PIL import Image, ImageOps

if __package__:
    from .json_storage import load_json as load, save_json as save, validate_catalog, validate_source_mapping
else:
    from json_storage import load_json as load, save_json as save, validate_catalog, validate_source_mapping

DATA = Path("docs/data.json")
STATUS = Path("docs/source_status.json")
OUT = Path("docs/thumbs/nhentai")
LIMIT = int(os.environ.get("NHENTAI_THUMB_LIMIT", "60"))
DELAY = float(os.environ.get("NHENTAI_THUMB_DELAY_SEC", "0.5"))
TIMEOUT = 15
DEADLINE_SEC = float(os.environ.get("NHENTAI_THUMB_DEADLINE_SEC", "180"))
MAX_BYTES = 15 * 1024 * 1024
THUMB_PATTERN = r"https://t\.nhentai\.net/galleries/[0-9]+/thumb\.[A-Za-z0-9.]+"


def valid_url(url: str) -> bool:
    return isinstance(url, str) and re.fullmatch(THUMB_PATTERN, url) is not None


def cache_destination(sid: str) -> Path:
    if not re.fullmatch(r"[0-9]+", sid):
        raise ValueError(f"invalid source ID: {sid!r}")
    root = OUT.resolve()
    dest = (root / f"{sid}.webp").resolve()
    if not dest.is_relative_to(root):
        raise ValueError(f"thumbnail destination outside cache: {sid!r}")
    return dest


def download(sess: requests.Session, url: str) -> bytes | None:
    if not valid_url(url):
        return None
    try:
        with sess.get(url, timeout=TIMEOUT, stream=True, allow_redirects=False,
                      headers={"Referer": "https://nhentai.net/"}) as response:
            if response.status_code != 200:
                return None
            raw = response.raw.read(MAX_BYTES + 1, decode_content=True)
        if not raw or len(raw) > MAX_BYTES:
            return None
        with Image.open(io.BytesIO(raw)) as image:
            image = ImageOps.exif_transpose(image)
            if image.mode == "RGBA":
                background = Image.new("RGB", image.size, "white")
                background.paste(image, mask=image.getchannel("A"))
                image = background
            elif image.mode != "RGB":
                image = image.convert("RGB")
            image.thumbnail((520, 760), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            image.save(output, "WEBP", quality=78, method=4)
            return output.getvalue()
    except Exception:
        return None


def _validate_catalog(value):
    if not isinstance(value, dict):
        raise ValueError("catalog must be an object")
    validate_catalog({**value, "items": value.get("items", [])})


def main():
    started = time.monotonic()
    deadline_reached = False
    data = load(DATA, {"items": []}, _validate_catalog)
    status = load(STATUS, {}, validate_source_mapping)
    items = [item for item in data.get("items", []) if item.get("source") == "nhentai"]
    OUT.mkdir(parents=True, exist_ok=True)
    attempted = cached = reused = failed = 0
    with requests.Session() as session:
        session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; Japanese-Metadata-Library/2.0)"})
        for item in items:
            sid = str(item.get("source_id") or "")
            try:
                dest = cache_destination(sid)
            except (ValueError, OSError, RuntimeError):
                failed += 1
                continue
            relative = f"thumbs/nhentai/{sid}.webp"
            if dest.exists() and dest.stat().st_size > 512:
                item["thumbnail"] = relative
                reused += 1
                continue
            url = str(item.get("thumbnail") or "")
            if not valid_url(url):
                failed += 1
                continue
            if time.monotonic() - started > DEADLINE_SEC:
                deadline_reached = True
            if deadline_reached or attempted >= LIMIT:
                continue
            attempted += 1
            raw = download(session, url)
            if raw and len(raw) > 512:
                dest.write_bytes(raw)
                item["thumbnail"] = relative
                cached += 1
            else:
                failed += 1
            time.sleep(DELAY)
    save(DATA, data)
    status.setdefault("nhentai", {})["thumbnail_cache"] = {
        "deadline_reached": deadline_reached, "attempted": attempted, "cached": cached, "reused": reused, "failed": failed,
        "pending": max(0, len(items) - cached - reused),
    }
    save(STATUS, status)
    print(f"[nhentai-thumbs] total={len(items)} attempted={attempted} cached={cached} reused={reused} failed={failed}")


if __name__ == "__main__":
    main()
