# Revision 4

Changes:

- Hitomi thumbnails: use og:image / galleryblock-specific selectors first, then a hash-based big thumbnail fallback.
- Adds Hitomi thumbnail diagnostics to `docs/source_status.json`: `thumbnails_found`, `thumbnails_missing`, `thumbnail_samples`.
- Adds `referrerpolicy="no-referrer"` to frontend thumbnail images.
- Fixes top-level `source_status.json` `updated_at` remaining blank due to dict merge order.
- Pururin behavior is intentionally not relabeled: current `/browse` results are English, so they remain blocked by the Japanese-only filter rather than being incorrectly accepted as Japanese.

For an existing installation, overwrite only:

- `sources/hitomi.py`
- `crawler.py`
- `docs/index.html`

Keep your existing `docs/data.json`, `docs/crawl_state.json`, and `docs/source_status.json`.
