# Japanese Metadata Library — Complete Hourly Build

## 自動更新と公開待ちの復旧

毎時17分のスケジュールで取得・保存・GitHub Pagesへの公開を実行します。GitHub側の混雑により開始時刻は遅れる場合があります。

同じ更新グループでは最新の実行を優先し、以前の実行が公開待ちのまま残っていても次の定期実行が置き換えます。取得・保存は最大55分、開始後の公開ジョブは最大10分です。公開ジョブの待機時間にはジョブの実行時間上限が適用されないため、待ち状態を解消する役割は次回実行の置き換えが担います。保存済みデータと既存の除外フィルターを引き継ぎます。

公開用ファイルは実行IDと試行番号を含む名前で受け渡すため、再実行で同名ファイルが重複しません。取得中にコードが更新された場合は、保存結果を最新のmainへリベースしてから通常のpushを行います。競合が起きた場合は停止し、強制上書きしません。

更新が止まった場合は、Actionsの取得ジョブと公開ジョブを別々に確認してください。`docs/source_status.json` の最終取得日時だけでは、公開の完了までは確認できません。

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

## 共通ブロック判定とHitomi再監査

`blood` を含む既存の除外条件と日本語表記（スカトロ、おなら、リョナ、丸呑み等）をタグ・タイトルに適用します。大文字小文字、全角、名前空間、スマートクォートの差も正規化します。「マグロ」「グローバル」などは短い「グロ」の一致から除きます。

毎回、保存済みの全作品を保存済みメタデータで再判定します。新しい取得結果にNGがあれば、以前の同じ作品も公開一覧から除外します。確認済みNGは、後のレスポンスからタグが欠けても自動復活させません。

Hitomiのタグ不足作品は公開せず、`state/filter_audit.json` に保留します。確認済みの除外作品も理由・元データとともにこのファイルに保持し、誤判定時の復元に備えます。このファイルはPages配信対象の `docs/` には含めません。

Hitomiの元メタデータ再取得は1回200件を上限とし、保留・未監査・古い確認から進めます。正常確認済みは7日後、失敗は12時間から最大72時間待ちます。403/429は取得先全体を12時間休止し、連続3回の通信失敗でもその実行の再監査を停止します。画像・本文は取得せず、メタデータだけを確認します。通常の毎時更新でもこの監査を継続します。

`python crawler.py --audit-filters-only` は新規発見処理をせず、既存全件の再判定とHitomiの元データ再取得を実行します。`[filter-audit]` を含むmainへのコード更新時は、このモードで最大500件を再取得します。保存済み情報の全件判定と、元サイトでの再確認完了件数は区別し、`docs/source_status.json` の `filters.hitomi_existing_audit` に残り件数を記録します。

```sh
python -m unittest discover -s tests -v
python crawler.py --audit-filters-only
```

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


## Revision 3 fixes

- Hitomi resource host updated to `ltn.gold-usergeneratedcontent.net`, with the legacy `ltn.hitomi.la` kept only as fallback.
- Hitomi remembers the working resource host in `crawl_state.json`.
- Pururin language detection now checks structured metadata, exact language links, label/value layouts, scripts/JSON-LD, page text, and Japanese-script titles.
- `source_status.json` now records `blocked_reasons`; Pururin also records `languages_detected`, `language_evidence`, and a few sample detections for debugging.
- Existing `docs/data.json`, `docs/crawl_state.json`, and `docs/source_status.json` should be preserved when upgrading an existing repository.
