# Revision 15 — E-Hentai cursor pagination fix + Revision 14 3Hentai debug

This patch is based on Revision 13 and integrates Revision 14's 3Hentai debug collector.

## E-Hentai fixes

- Replaces the old `page=N` backfill assumption with E-Hentai's current `next=<gid>` cursor pagination.
- Always follows the site's own Next link/cursor.
- Keeps separate latest and historical cursor chains.
- Migrates away from the unsafe legacy `backfill_page` integer.
- Does not advance the historical cursor when a page repeats, fails, or is blocked by the per-run item cap.
- Adds per-page diagnostics to `source_status.json`:
  - `pagination_mode`
  - `latest_pages_fetched`
  - `backfill_pages_fetched`
  - `backfill_cursor_before`
  - `backfill_cursor_after`
  - `repeated_page_detected`
  - `page_debug[]` with requested cursor, final URL, counts, first/last gid and next cursor.
- Raises default `EH_MAX_GALLERIES_PER_RUN` from 100 to 150 so 2 latest + 4 historical pages can fit in a normal run.

## 3Hentai debug retained

Revision 14's `3hentai_debug_samples` output is included unchanged so the next Actions run can still diagnose missing gallery URLs / thumbnails.

## Files to overwrite

- `config.py`
- `sources/ehentai.py`
- `sources/hentai3.py`

Do not overwrite `docs/data.json`, `docs/crawl_state.json`, or `docs/source_status.json`.
