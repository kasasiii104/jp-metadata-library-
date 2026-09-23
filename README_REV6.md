# Revision 6 Patch — Pururin -> 3Hentai

Overwrite/upload:

- `config.py`
- `crawler.py`
- `sources/hentai3.py`
- `sources/hitomi.py`
- `docs/index.html`
- `.github/workflows/update.yml`
- `tools/cache_hitomi_thumbs.mjs`

Do NOT overwrite:

- `docs/data.json`
- `docs/crawl_state.json`
- `docs/source_status.json`

`sources/pururin.py` may remain in the repository; Revision 6 no longer imports or uses it. You may delete it manually if desired.

On the next run, old Pururin records/state/status are retired automatically.
