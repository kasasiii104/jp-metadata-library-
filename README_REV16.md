# Revision 16 — 3Hentai `/get` sample diagnostics

This patch is based on Revision 15 and keeps the E-Hentai `next=` cursor fix and the existing 3Hentai search diagnostics.

## What changed

- Probes Jandapress `/3hentai/get?book=<id>` for only the first 3 discovered 3Hentai IDs per run.
- Does **not** raise on HTTP 400/403/429/500; instead it records a compact diagnostic entry in `docs/source_status.json`.
- Records HTTP status, short response preview, response keys, URL-like fields, image-like fields, real gallery URL, and best thumbnail candidate.
- Stops the sample probe early on 403/429 to avoid hammering the upstream.
- Waits 1.5 seconds between sample probes by default.
- If a sample `/get` call returns HTTP 200 with richer metadata, the corresponding in-memory item is conservatively enriched for that run (thumbnail/direct public URL/tags etc.) without changing its existing UID.

## New status fields

Look under `3hentai` in `docs/source_status.json`:

- `3hentai_get_debug`
- `get_debug_count`
- `get_debug_successes`
- `get_debug_http_statuses`
- `get_debug_rate_limited`
- `get_probe_enriched_items`

The most important fields inside each `3hentai_get_debug` row are:

- `http_status`
- `response_preview`
- `response_keys`
- `raw_urls`
- `image_like_values`
- `real_gallery_url`
- `best_thumbnail_url`

## Files to overwrite

- `config.py`
- `sources/ehentai.py`
- `sources/hentai3.py`

Do not overwrite your existing `docs/data.json`, `docs/crawl_state.json`, or `docs/source_status.json`.
