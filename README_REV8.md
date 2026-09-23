# Revision 8

今回の修正は2点です。

1. **重複検出・同一作品まとめ表示**
   - E-Hentai / Hitomi / 3Hentai のレコードを破壊的に統合せず、同一作品候補へ `duplicate_group` を付与します。
   - 日本語タイトルの正規化、作者/サークル一致、ページ数近似を使った保守的な判定です。
   - Web画面では同一作品を1カードへまとめ、`同一作品 2ソース` のように表示します。
   - 三点メニューから各元サイトへ個別に移動できます。
   - data.json 上部に `duplicate_group_count` / `duplicate_item_count` が追加されます。

2. **3Hentaiサムネイル**
   - Jandapressレスポンスの `thumbnail/cover/images/pages/files` 等を再帰的に探索します。
   - Actions内で代表画像を取得し、520x760以内の小さいWebPサムネイルに変換して `docs/thumbs/3hentai/` へ保存します。
   - ブラウザは3Hentaiへ直接hotlinkせず、自分のGitHub Pagesのサムネイルを表示します。
   - `source_status.json` の `3hentai.thumbnail_cache` で cached/reused/failed を確認できます。

## パッチ版で上書き・追加するファイル

- `crawler.py`
- `sources/hentai3.py`
- `docs/index.html`
- `requirements.txt`
- `.github/workflows/update.yml`
- `tools/cache_3hentai_thumbs.py`

既存の `docs/data.json` / `docs/crawl_state.json` / `docs/source_status.json` は上書きしないでください。
