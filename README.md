# Japanese Metadata Library — Complete Hourly Build

このZIPは、以下を1つにまとめた完全版です。

- E-Hentai
- Hitomi（`/n/index-japanese.nozomi` + Range取得対応）
- Pururin
- 日本語作品のみ採用
- BL / Yaoi / グロ系タグを保存前に除外
- サムネイル表示
- 最新作品取得 + 過去バックフィル
- 1時間ごとのGitHub Actions自動更新
- スマホ向け検索・絞り込み・お気に入り・非表示

## 高速バックフィル設定

- E-Hentai: 最新2ページ + 過去4ページ / 実行、最大100件
- Hitomi: 最新30件 + 過去60件 / 実行
- Pururin: 最新2ページ + 過去2ページ / 実行、最大50件
- GitHub Actions: 毎時17分

## GitHubへ配置

ZIPを展開し、このフォルダの「中身」をリポジトリ直下へ配置してください。

正しい構成:

```text
.github/workflows/update.yml
docs/index.html
docs/data.json
docs/crawl_state.json
docs/source_status.json
sources/ehentai.py
sources/hitomi.py
sources/pururin.py
crawler.py
config.py
requirements.txt
```

GitHub Pages は `Settings > Pages > Source: GitHub Actions` に設定してください。

## 既存データを残したい場合

すでに `docs/data.json` / `docs/crawl_state.json` / `docs/source_status.json` に収集済みデータがある場合、これら3ファイルは上書きしないでください。

それ以外のファイルを上書きすれば、既存データを保持したまま完全版へ更新できます。

## 実行

`Actions > Japanese Metadata Library > Run workflow`

ログ例:

```text
[ehentai] ... status=ok
[hitomi] ... status=ok
[pururin] ... status=ok
```

取得先が403/429等を返した場合は、そのソースだけ失敗扱いにし、他ソースと既存データは維持します。
