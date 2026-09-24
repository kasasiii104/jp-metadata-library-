# Revision 18 — E-Hentai fast latest detection

Revision 17 is preserved. This revision changes only E-Hentai newest-item discovery.

## What changed

- Latest E-Hentai pages are now read from the unfiltered global newest feed.
- The gdata API is then used to keep only rows tagged `language:japanese`.
- This avoids depending on the E-Hentai Japanese search index for newest-item discovery.
- Historical backfill remains `language:japanese$` + `next=<gid>` cursor, exactly as before.
- Existing `backfill_next` state is preserved.
- A fresh install bootstraps historical crawling from the live Japanese search page so it does not skip Japanese rows.
- Extra status fields make the behavior visible:
  - `latest_mode`
  - `latest_global_discovered`
  - `latest_metadata_found`
  - `latest_japanese_found`
  - `latest_non_japanese_skipped`
  - `backfill_mode`
  - `backfill_japanese_found`

## Files to overwrite

- `config.py`
- `sources/ehentai.py`
- `sources/hentai3.py`
- `tools/cache_3hentai_thumbs.py`

Do not overwrite `docs/data.json`, `docs/crawl_state.json`, or `docs/source_status.json`.
