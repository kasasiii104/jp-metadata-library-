#!/usr/bin/env python3
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
CATALOG = DOCS / "catalog"


def load(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def fail(message: str) -> None:
    raise SystemExit(f"Pages bundle verification failed: {message}")


def main() -> int:
    manifest_path = CATALOG / "manifest.json"
    if not manifest_path.is_file():
        fail("catalog/manifest.json is missing")
    manifest = load(manifest_path)
    item_count = int(manifest.get("item_count") or 0)
    chunk_size = int(manifest.get("chunk_size") or 0)
    chunk_count = int(manifest.get("chunk_count") or 0)
    if item_count <= 0 or chunk_size <= 0 or chunk_count <= 0:
        fail(f"invalid manifest counts: {manifest}")

    total = 0
    first_two_bytes = 0
    for i in range(chunk_count):
        path = CATALOG / f"chunk-{i:03d}.json"
        if not path.is_file():
            fail(f"missing {path.name}")
        payload = load(path)
        rows = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            fail(f"{path.name} has no items list")
        if i < chunk_count - 1 and len(rows) != chunk_size:
            fail(f"{path.name} has {len(rows)} items, expected {chunk_size}")
        if i == chunk_count - 1 and not (1 <= len(rows) <= chunk_size):
            fail(f"last chunk size is invalid: {len(rows)}")
        if i < 2:
            first_two_bytes += path.stat().st_size
        total += len(rows)

    if total != item_count:
        fail(f"chunk total {total} != manifest item_count {item_count}")
    # Guard against accidentally returning to a huge startup payload.
    if first_two_bytes > 5 * 1024 * 1024:
        fail(f"initial two chunks are too large: {first_two_bytes} bytes")

    html = (DOCS / "index.html").read_text(encoding="utf-8")
    app = (DOCS / "app.js").read_text(encoding="utf-8")
    if not re.search(r'<script[^>]+src=["\']app\.js', html):
        fail("index.html does not load app.js")
    if "catalog/manifest.json" not in app or "catalog/chunk-" not in app:
        fail("app.js does not use the chunked catalog")
    if re.search(r"fetch\(\s*['\"]data\.json", app):
        fail("app.js still fetches the full data.json")

    print(
        f"Pages bundle OK: items={item_count} chunks={chunk_count} "
        f"chunk_size={chunk_size} startup_bytes={first_two_bytes}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
