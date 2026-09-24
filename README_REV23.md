# Revision 23 — scat filter + existing cleanup

Integrated on top of Revision 22.

- Adds `scat` to the shared blocked-tag filter.
- Adds exact E-Hentai namespace matches `female:scat`, `male:scat`, and `other:scat`.
- New E-Hentai, Hitomi, and 3Hentai records with these tags are rejected.
- Existing rows with stored matching tags are removed by the normal cleanup pass.
- Existing 3Hentai rows continue to be re-audited and are removed when enrichment reveals a blocked tag.
- Keeps all Revision 22 filters (`farting`, `miniguy`, `vore`, BL/yaoi, gore/ryona family, insect family), E-Hentai fast-latest mode, and 3Hentai metadata/filter audit.

Do not overwrite `docs/data.json`, `docs/crawl_state.json`, or `docs/source_status.json`.
