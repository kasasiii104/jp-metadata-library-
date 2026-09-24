# Revision 13 integrated patch

This patch consolidates the previous updates into one package.

Included:
- E-Hentai / Hitomi metadata enrichment (artists, groups/circles, works/parodies, characters, tags)
- insect/bug-related filtering for new items and cleanup of existing saved items
- duplicate detection / grouped display support from the integrated branch
- 3Hentai search-based acquisition via local Jandapress
- 3Hentai link fix: only verified direct /d/<id> URLs are used; otherwise use a safe title-search fallback
- 3Hentai thumbnail cache from public gallery pages, low-rate incremental caching, 429 stop behavior
- Hitomi local thumbnail cache
- hourly GitHub Actions workflow

Do NOT overwrite existing runtime data files when applying this patch:
- docs/data.json
- docs/crawl_state.json
- docs/source_status.json

Upload/overwrite the files from this patch, then run the GitHub Actions workflow once manually.
