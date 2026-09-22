# Japanese Metadata Library

日本語作品だけを集約する **メタデータ型** の静的ライブラリです。本文画像・ギャラリー全ページ・ZIP・動画は保存しません。

## 収集元

- **E-Hentai**: 日本語検索ページから `gid/token` を発見し、公式 Gallery Metadata API (`gdata`) でメタデータ取得。
- **Hitomi**: `index-japanese.nozomi` から日本語ギャラリーIDを取得し、公開 `galleries/{id}.js` からメタデータ取得。サムネイルは公開 gallery block から取得できた場合のみ使用。
- **nHentai**: 公開JSON APIの日本語検索を利用。現行v2を先に試し、旧APIへフォールバック。

アクセス制御、Cloudflare Challenge、CAPTCHA 等を回避する処理は入れていません。取得できないソースはその回だけ失敗扱いになり、他ソースと既存データは維持されます。

## 自動収集

各GitHub Actions実行で同時に行います。

1. **Latest crawl**: 最新作品を再確認。
2. **Historical backfill**: `docs/crawl_state.json` の続きから過去作品を少しずつ収集。
3. 日本語判定。
4. BL / Yaoi / グロ系の設定タグを除外。
5. 既存作品とmergeし、`docs/data.json` を更新。

バックフィル量は `config.py` の各 `*_BACKFILL_*` で調整できます。

## 除外タグ

`config.py` の `BLOCK_TAGS` / `BLOCK_FULL_TAGS` を編集してください。判定はタイトルではなく、原則として各ソースのタグ・カテゴリ単位で行います。

## サムネイル

公開メタデータ・公開サムネイルURLだけを利用します。Hotlink拒否や取得失敗時はプレースホルダー表示です。

## ローカル実行

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
python crawler.py
python -m http.server 8000 -d docs
```

ブラウザで `http://localhost:8000` を開きます。

## GitHub Pages

1. このプロジェクトをGitHubリポジトリへ配置。
2. リポジトリ Settings → Pages → Source を **GitHub Actions** に設定。
3. `main` へpush。
4. `.github/workflows/update.yml` が6時間ごとに更新・デプロイします。

## データ

- `docs/data.json`: 採用済み作品メタデータ
- `docs/crawl_state.json`: 各ソースの過去バックフィル位置
- `docs/source_status.json`: 各ソースの最終成功・失敗状況

同じ作品が複数ソースにある場合も、初期版では誤統合を避けるため `ehentai:ID` / `hitomi:ID` / `nhentai:ID` として別レコードで保持します。
