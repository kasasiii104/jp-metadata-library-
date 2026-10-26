#!/usr/bin/env python3
import html
import json
import math
import shutil
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
DATA = DOCS / "data.json"
OUT = DOCS / "catalog"
INDEX = DOCS / "index.html"
CHUNK_SIZE = 400
FALLBACK_LIMIT = 40
FALLBACK_START = "<!-- FALLBACK_CARDS_START -->"
FALLBACK_END = "<!-- FALLBACK_CARDS_END -->"
STATUS_START = "<!-- FALLBACK_STATUS_START -->"
STATUS_END = "<!-- FALLBACK_STATUS_END -->"

PUBLIC_FIELDS = (
    "uid", "source", "source_id", "source_url", "source_url_kind",
    "title", "title_jp", "category", "artists", "groups", "parodies",
    "works", "characters", "tags", "pages", "rating", "popularity",
    "posted_at", "thumbnail", "duplicate_group", "duplicate_count",
    "duplicate_sources", "duplicate_uids",
)

SOURCE_LABELS = {
    "ehentai": "E-Hentai",
    "hitomi": "Hitomi",
    "3hentai": "3Hentai",
    "asmhentai": "ASMHentai",
}


def compact_item(item: dict) -> dict:
    out = {}
    for key in PUBLIC_FIELDS:
        value = item.get(key)
        if value in (None, "", [], {}, False):
            continue
        out[key] = value
    return out


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
        f.write("\n")
    tmp.replace(path)


def safe_link(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "#"
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return "#"
    return raw if parsed.scheme in ("http", "https") and parsed.netloc else "#"


def safe_image(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw.startswith(("thumbs/", "./thumbs/")):
        return raw[2:] if raw.startswith("./") else raw
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return ""
    return raw if parsed.scheme in ("http", "https") and parsed.netloc else ""


def fallback_card(item: dict) -> str:
    title = str(item.get("title_jp") or item.get("title") or item.get("uid") or "作品")
    url = safe_link(item.get("source_url") or "")
    thumb = safe_image(item.get("thumbnail") or "")
    source = SOURCE_LABELS.get(str(item.get("source") or ""), str(item.get("source") or ""))
    meta = []
    if item.get("rating") not in (None, ""):
        try:
            meta.append(f"★{float(item['rating']):.2f}".rstrip("0").rstrip("."))
        except (TypeError, ValueError):
            pass
    if item.get("pages"):
        meta.append(f"{item['pages']}p")
    image = (
        f'<img src="{html.escape(thumb, quote=True)}" alt="" loading="lazy" referrerpolicy="no-referrer">'
        if thumb else '<div class="ph">サムネイルなし</div>'
    )
    return (
        '<article class="card fallback-card">'
        f'<a class="thumb" href="{html.escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">'
        f'{image}<span class="source">{html.escape(source)}</span></a>'
        '<div class="body">'
        f'<a class="title" href="{html.escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">{html.escape(title)}</a>'
        f'<p class="meta">{html.escape(" ・ ".join(meta))}</p>'
        '</div></article>'
    )


def replace_between(text: str, start: str, end: str, body: str) -> str:
    if start not in text or end not in text:
        raise RuntimeError(f"missing HTML marker: {start} / {end}")
    before, rest = text.split(start, 1)
    _, after = rest.split(end, 1)
    return before + start + "\n" + body + "\n" + end + after


def render_fallback(items: list[dict], updated_at: str) -> None:
    text = INDEX.read_text(encoding="utf-8")
    cards = "\n".join(fallback_card(item) for item in items[:FALLBACK_LIMIT])
    text = replace_between(text, FALLBACK_START, FALLBACK_END, cards)
    status = f"最新{min(len(items), FALLBACK_LIMIT)}件を表示 ・ 全{len(items):,}件"
    if updated_at:
        status += f" ・ {updated_at}"
    text = replace_between(text, STATUS_START, STATUS_END, html.escape(status))
    INDEX.write_text(text, encoding="utf-8")


def main() -> int:
    with DATA.open("r", encoding="utf-8") as f:
        data = json.load(f)
    items = [compact_item(x) for x in data.get("items", []) if isinstance(x, dict)]

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True, exist_ok=True)

    chunk_count = math.ceil(len(items) / CHUNK_SIZE) if items else 0
    for i in range(chunk_count):
        start = i * CHUNK_SIZE
        rows = items[start:start + CHUNK_SIZE]
        write_json(OUT / f"chunk-{i:03d}.json", {"index": i, "items": rows})

    updated_at = str(data.get("updated_at") or "")
    write_json(OUT / "manifest.json", {
        "updated_at": updated_at,
        "item_count": len(items),
        "duplicate_group_count": int(data.get("duplicate_group_count") or 0),
        "duplicate_item_count": int(data.get("duplicate_item_count") or 0),
        "chunk_size": CHUNK_SIZE,
        "chunk_count": chunk_count,
        "version": updated_at,
    })
    render_fallback(items, updated_at)
    print(
        f"public catalog: items={len(items)} chunks={chunk_count} "
        f"chunk_size={CHUNK_SIZE} fallback={min(len(items), FALLBACK_LIMIT)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
