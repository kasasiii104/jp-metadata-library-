# Revision 22 — miniguy / vore filter + existing cleanup

Integrated on top of Revision 21.

- Adds `miniguy` and `vore` to the shared blocked-tag filter.
- Adds exact E-Hentai namespace matches `male:miniguy` and `male:vore`.
- New E-Hentai, Hitomi, and 3Hentai records carrying these tags are rejected.
- Existing rows in `docs/data.json` are removed by the normal existing-data cleanup when their stored tags match.
- Existing 3Hentai rows without complete stored tags continue through the Revision 19/20 gallery metadata audit, so matching historical rows are removed as they are enriched.
- Keeps Revision 18 E-Hentai fast-latest mode, Revision 19/20 3Hentai metadata/filter audit, and Revision 21 farting filter.

Do not overwrite deployment state/data files from a patch archive.
