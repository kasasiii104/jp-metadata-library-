# Revision 7 — 3Hentai via local Jandapress

Revision 6's direct 3Hentai scraper used guessed public URL patterns and was not reliable enough.
Revision 7 replaces only the 3Hentai acquisition path with a locally self-hosted Jandapress container during GitHub Actions.

Replace these files:

- `sources/hentai3.py`
- `.github/workflows/update.yml`

Keep existing `docs/data.json`, `docs/crawl_state.json`, and `docs/source_status.json`.

After running Actions, inspect `docs/source_status.json` → `3hentai`.
Useful fields:

- `jandapress_http`
- `discovered`
- `accepted_raw`
- `languages_detected`
- `thumbnails_found`
- `debug`
- `message`

Important: Jandapress itself documents that some upstream sites can reject CI/shared-IP traffic. If this revision shows Jandapress ready but 3Hentai search still fails/returns 0, the likely blocker is upstream CI access rather than the parser. In that case a different third metadata source is the more reliable choice.
