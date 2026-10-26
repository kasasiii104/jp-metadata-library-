#!/usr/bin/env python3
import json
import math
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
DATA = DOCS / "data.json"
OUT = DOCS / "catalog"
CHUNK_SIZE = 400

PUBLIC_FIELDS = (
    "uid", "source", "source_id", "source_url", "source_url_kind",
    "title", "title_jp", "category", "artists", "groups", "parodies",
    "works", "characters", "tags", "pages", "rating", "popularity",
    "posted_at", "thumbnail", "duplicate_group", "duplicate_count",
    "duplicate_sources", "duplicate_uids",
)


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

    write_json(OUT / "manifest.json", {
        "updated_at": data.get("updated_at", ""),
        "item_count": len(items),
        "duplicate_group_count": int(data.get("duplicate_group_count") or 0),
        "duplicate_item_count": int(data.get("duplicate_item_count") or 0),
        "chunk_size": CHUNK_SIZE,
        "chunk_count": chunk_count,
        "version": str(data.get("updated_at") or ""),
    })
    print(f"public catalog: items={len(items)} chunks={chunk_count} chunk_size={CHUNK_SIZE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
