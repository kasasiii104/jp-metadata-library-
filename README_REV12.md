# Revision 12 — Integrated patch

This bundle merges the recent changes so they can be applied together without one patch overwriting another.

Included changes:

- Revision 9 dependency: gradual local 3Hentai thumbnail cache + hourly workflow
- Revision 10: E-Hentai/Hitomi structured metadata facets (artist/group/work/character/tag)
- Revision 10: insect/bug-content exclusion for new and already-retained rows
- Revision 11: safe 3Hentai source links; direct gallery URLs are used only when verified in the API payload, otherwise the UI opens a title search
- Integration fix: the 3Hentai thumbnail cacher never treats a search-results URL as a gallery page, preventing a wrong cover from being attached to a fallback result
- Existing duplicate grouping / same-work consolidation remains intact through the Revision 10 crawler/frontend base

## Replace/add these files

- `config.py`
- `crawler.py`
- `sources/ehentai.py`
- `sources/hitomi.py`
- `sources/hentai3.py`
- `docs/index.html`
- `tools/cache_3hentai_thumbs.py`
- `tools/cache_hitomi_thumbs.mjs`
- `.github/workflows/update.yml`
- `requirements.txt`

Do NOT overwrite your existing:

- `docs/data.json`
- `docs/crawl_state.json`
- `docs/source_status.json`

After replacing the files, run the GitHub Actions workflow once manually.
