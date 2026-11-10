#!/usr/bin/env python3
import html
import json
import math
import os
import shutil
import tempfile
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


def fallback_html(items: list[dict], updated_at: str) -> str:
    text = INDEX.read_text(encoding="utf-8")
    cards = "\n".join(fallback_card(item) for item in items[:FALLBACK_LIMIT])
    text = replace_between(text, FALLBACK_START, FALLBACK_END, cards)
    status = f"最新{min(len(items), FALLBACK_LIMIT)}件を表示 ・ 全{len(items):,}件"
    if updated_at:
        status += f" ・ {updated_at}"
    text = replace_between(text, STATUS_START, STATUS_END, html.escape(status))
    return text


def _umask() -> int:
    current = os.umask(0)
    os.umask(current)
    return current


def render_fallback(text: str) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=INDEX.parent,
                                         prefix=INDEX.name + ".", suffix=".tmp", delete=False) as f:
            temporary = Path(f.name)
            f.write(text)
        # NamedTemporaryFile creates 0600; keep index.html readable as before.
        os.chmod(temporary, 0o666 & ~_umask())
        temporary.replace(INDEX)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def validate_bundle(directory: Path, items: list[dict], manifest: dict) -> None:
    if json.loads((directory / "manifest.json").read_text(encoding="utf-8")) != manifest:
        raise ValueError("public catalog manifest mismatch")
    expected = {"manifest.json"}
    count = 0
    for i in range(manifest["chunk_count"]):
        name = f"chunk-{i:03d}.json"
        expected.add(name)
        chunk = json.loads((directory / name).read_text(encoding="utf-8"))
        rows = items[i * CHUNK_SIZE:(i + 1) * CHUNK_SIZE]
        if chunk != {"index": i, "items": rows}:
            raise ValueError("public catalog chunk mismatch")
        count += len(chunk["items"])
    if count != manifest["item_count"] or {p.name for p in directory.iterdir()} != expected:
        raise ValueError("public catalog count or files mismatch")


def main() -> int:
    with DATA.open("r", encoding="utf-8") as f:
        data = json.load(f)
    items = [compact_item(x) for x in data.get("items", []) if isinstance(x, dict)]

    if not items:
        raise ValueError("public catalog must contain items")

    chunk_count = math.ceil(len(items) / CHUNK_SIZE) if items else 0
    updated_at = str(data.get("updated_at") or "")
    manifest = {
        "updated_at": updated_at,
        "item_count": len(items),
        "duplicate_group_count": int(data.get("duplicate_group_count") or 0),
        "duplicate_item_count": int(data.get("duplicate_item_count") or 0),
        "chunk_size": CHUNK_SIZE,
        "chunk_count": chunk_count,
        "version": updated_at,
    }
    text = fallback_html(items, updated_at)
    parent = OUT.absolute().parent
    parent.mkdir(parents=True, exist_ok=True)
    owned = set()

    def temporary_directory(kind):
        path = Path(tempfile.mkdtemp(prefix=f".{OUT.name}-{kind}-", dir=parent))
        owned.add(path)
        return path

    def cleanup(path):
        if (path not in owned or path.parent != parent or path == OUT.absolute()
                or path.is_symlink()):
            raise ValueError("refusing to remove unowned catalog directory")
        if path.exists():
            shutil.rmtree(path)

    staged = temporary_directory("tmp")
    backup = None
    try:
        for i in range(chunk_count):
            rows = items[i * CHUNK_SIZE:(i + 1) * CHUNK_SIZE]
            write_json(staged / f"chunk-{i:03d}.json", {"index": i, "items": rows})
        write_json(staged / "manifest.json", manifest)
        validate_bundle(staged, items, manifest)
        # mkdtemp creates 0700; keep the published directory as readable as a
        # normally created one (umask-based), since it is served by Pages.
        os.chmod(staged, 0o777 & ~_umask())
        if OUT.exists():
            backup = temporary_directory("backup")
            OUT.rename(backup)
        try:
            staged.rename(OUT)
            render_fallback(text)
        except BaseException:
            if OUT.exists():
                OUT.rename(staged)
            if backup is not None:
                backup.rename(OUT)
            raise
        if backup is not None:
            cleanup(backup)
    finally:
        cleanup(staged)
        # A failed rollback must retain the backup for recovery.
        if backup is not None and backup.exists() and not any(backup.iterdir()):
            cleanup(backup)
    print(
        f"public catalog: items={len(items)} chunks={chunk_count} "
        f"chunk_size={CHUNK_SIZE} fallback={min(len(items), FALLBACK_LIMIT)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
