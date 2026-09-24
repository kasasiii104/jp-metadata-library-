# Revision 19 — 3Hentai metadata filtering + existing-library cleanup

This patch is integrated on top of Revision 18.

## Included behavior

- Keeps Revision 18 E-Hentai fast-latest mode.
- Keeps Revision 17 3Hentai direct gallery URL + thumbnail repair.
- Fetches public 3Hentai gallery metadata for the current batch before saving:
  - artists
  - groups/circles
  - parodies/works
  - characters
  - tags
  - language
  - category
- Applies the existing common exclusion rules to 3Hentai using those tags:
  - yaoi / boys love / BL
  - guro / snuff / ryona / torture / amputee / decapitation / corpse
  - insect / bug / spider / parasite / worm and the configured Japanese equivalents
- New 3Hentai rows are fail-closed: if filter-relevant gallery metadata cannot be verified, the item is not added that run.
- Existing 3Hentai rows are gradually audited from their public gallery pages, 24 per run by default.
- As soon as an existing row is audited, the common filter is applied in the same crawler run; blocked rows are removed from `docs/data.json`.
- Existing E-Hentai / Hitomi rows that already carry blocked tags are also purged by the same current rules.

## Default audit rate

- Current 3Hentai batch metadata checks: up to 60 gallery pages/run.
- Existing-library 3Hentai audit: up to 24 saved rows/run.
- The existing audit progresses across runs because successfully checked rows receive `filter_metadata_checked = 3hentai-gallery-v1`.

Environment overrides:

- `HENTAI3_GALLERY_METADATA_LIMIT`
- `HENTAI3_GALLERY_METADATA_DELAY_SEC`
- `HENTAI3_EXISTING_FILTER_AUDIT_LIMIT`
- `HENTAI3_EXISTING_FILTER_AUDIT_DELAY_SEC`

## New status fields

Under `3hentai` in `docs/source_status.json`:

- `gallery_metadata_mode`
- `gallery_metadata_attempted`
- `gallery_metadata_enriched`
- `gallery_metadata_failed`
- `gallery_metadata_stopped`
- `existing_filter_audit_attempted`
- `existing_filter_audit_enriched`
- `existing_filter_audit_pending`
- `purged_existing_this_run`

Under `filters`:

- `purged_existing_blocked`
- `purged_existing_3hentai`
- `purged_existing_reasons`
- `purged_existing_by_source`
- `3hentai_existing_audit`

## Files to overwrite

- `config.py`
- `crawler.py`
- `sources/ehentai.py`
- `sources/hentai3.py`
- `tools/cache_3hentai_thumbs.py`

Do **not** overwrite these existing data files:

- `docs/data.json`
- `docs/crawl_state.json`
- `docs/source_status.json`

