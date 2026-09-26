# Revision 24

Integrated on top of Revision 23.

Adds 3Hentai metadata visibility and tap-to-filter UI.

Changes:
- 3Hentai public gallery metadata continues to populate tags, artists, groups/circles, parody/work, characters, language and category.
- Existing 3Hentai metadata/filter audit default increased from 24 to 60 records per run so older rows gain metadata faster.
- 3Hentai status now reports tagged_items / artists_found / groups_found / works_found / characters_found.
- Gallery metadata is merged when any useful metadata evidence exists; filter approval still requires filter-relevant metadata, so existing safety filters are not weakened.
- Cards now show clickable artist/tag/work chips.
- Bottom-sheet artist/group/work/character/tag chips are clickable.
- Tapping a chip sets the existing facet filter immediately and scrolls back to the filtered results.
- Active metadata filter is shown as a removable chip.
- Noisy language/meta-prefix tags are hidden from the user-facing tag facet.

Retains all Revision 23 filters and previous functionality, including scat, farting, miniguy, vore, BL/yaoi, gore/ryona/torture/snuff and insect filtering.

Overwrite these files from this patch:
- config.py
- crawler.py
- sources/ehentai.py
- sources/hentai3.py
- tools/cache_3hentai_thumbs.py
- docs/index.html

Do not overwrite/delete existing deployment data:
- docs/data.json
- docs/crawl_state.json
- docs/source_status.json
