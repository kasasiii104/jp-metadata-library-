# Revision 17 — 3Hentai thumbnail/source URL repair

This patch keeps the Revision 15 E-Hentai next-cursor fix and the Revision 16 diagnostics, then changes 3Hentai handling based on the confirmed logs:

- Jandapress search currently returns only `title` + an internal numeric `id`.
- `/3hentai/get?book=<that id>` returns HTTP 400 because the upstream lookup returns 404.
- Therefore the Jandapress search id is not treated as a public `/d/<gallery id>`.

## New behavior

### Crawler (`sources/hentai3.py`)
- Continue using Jandapress search for discovery/backfill and stable local IDs.
- Also read the public 3Hentai search-result HTML at a low rate.
- Extract only public card metadata: verified `/d/<id>` links and lazy thumbnail URLs.
- Match HTML cards to Jandapress results by conservative normalized exact title.
- On a verified match, keep the existing local UID/source_id but enrich with:
  - `source_url_kind: direct`
  - real public `source_url`
  - public thumbnail URL
  - `resolved_gallery_id`
- No guess-by-order fallback.
- The now-proven-useless `/3hentai/get` probe is disabled by default (`HENTAI3_GET_DEBUG_LIMIT=0`).

### Thumbnail cache (`tools/cache_3hentai_thumbs.py`)
- Repair legacy rows that only have title-search fallback URLs.
- Visit that public search URL and accept only one exact normalized-title match.
- Use the matched card thumbnail, or the verified direct gallery page if needed.
- Cache the resulting thumbnail as `docs/thumbs/3hentai/<existing-source-id>.webp`.
- Stops early on 429 or common upstream outage responses instead of repeating failures.

## New status fields

`source_status.json` may include:

- `direct_html_mode`
- `direct_html_cards`
- `direct_html_enriched`
- `direct_html_pages`
- `thumbnail_cache.resolved_search_fallbacks`
- `thumbnail_cache.upstream_unavailable`

A successful run should start showing `direct_html_enriched > 0` and/or `thumbnail_cache.cached > 0`.

## Replace these files

- `config.py`
- `sources/ehentai.py`
- `sources/hentai3.py`
- `tools/cache_3hentai_thumbs.py`

Do not replace existing `docs/data.json`, `docs/crawl_state.json`, or `docs/source_status.json`.
