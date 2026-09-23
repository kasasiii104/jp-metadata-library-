# Revision 5 - Hitomi thumbnail cache

Revision 4 correctly discovered thumbnail candidates, but the fallback `tn.hitomi.la/bigtn/...` URL is legacy/guessed and can fail in browsers.

Revision 5 uses the maintained `node-hitomi` URL resolver/fetcher in GitHub Actions, downloads only the representative small WebP thumbnail, and serves it locally from GitHub Pages.

## Replace/add these files

- `.github/workflows/update.yml`
- `tools/cache_hitomi_thumbs.mjs`

Do not overwrite `docs/data.json`, `docs/crawl_state.json`, or `docs/source_status.json` manually.

After Actions runs, successful Hitomi items will have:

```json
"thumbnail": "thumbs/hitomi/4207187.webp"
```

and `source_status.json` will include `hitomi.thumbnail_cache` stats.
