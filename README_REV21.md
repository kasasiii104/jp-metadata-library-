# Revision 21 — farting filter + existing cleanup

Revision 20 full integrated patch plus:

- Adds `farting` to the shared blocked-tag filter.
- New E-Hentai, Hitomi, and 3Hentai records tagged `farting` are rejected.
- Existing records already present in `docs/data.json` are purged by the existing cleanup pass when their stored/enriched tags contain `farting`.
- Existing 3Hentai records continue to be enriched/audited in batches first, so historical 3Hentai entries can also be removed once the public gallery metadata exposes the tag.
- Keeps Revision 18 E-Hentai fast latest mode and Revision 19/20 3Hentai metadata/filter cleanup.

Do not overwrite/delete deployment state/data files when applying the patch.
