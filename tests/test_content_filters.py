import contextlib
import copy
import io
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import crawler as c
from sources import hitomi


class ContentFilterTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for name, value in {
            'DOCS_DIR': self.root / 'docs', 'DATA_FILE': self.root / 'docs/data.json',
            'STATE_FILE': self.root / 'docs/crawl_state.json',
            'STATUS_FILE': self.root / 'docs/source_status.json',
            'FILTER_STATE_FILE': self.root / 'state/filter_audit.json',
        }.items():
            self.enterContext(patch.object(c, name, value))
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.enterContext(patch.object(c, 'now_iso', return_value='2026-09-29T11:00:00+00:00'))
        self.enterContext(patch.object(c, 'HITOMI_FILTER_AUDIT_LIMIT', 0))
        self.enterContext(patch.object(c, 'HITOMI_FILTER_AUDIT_DELAY_SEC', 0))
        self.enterContext(patch.object(c.hentai3, 'enrich_existing_for_filter', return_value={}))
        self.enterContext(patch('requests.adapters.HTTPAdapter.send', side_effect=AssertionError('Network forbidden')))
        self.enterContext(patch.dict(c.SOURCES, {}, clear=True))

    def item(self, num='9000001', **extra):
        return {'uid': 'hitomi:' + num, 'source_id': num, 'source': 'hitomi',
                'title': '中立なサンプル作品', 'language': 'japanese', 'category': 'test',
                'tags': ['sample'], 'first_seen': '2026-01-01T00:00:00+00:00',
                'thumbnail': 'thumbs/hitomi/' + num + '.webp', **extra}

    def fresh(self, num='9000001', **extra):
        return self.item(num, filter_metadata_checked=hitomi.FILTER_METADATA_VERSION,
                         filter_metadata_checked_at=c.now_iso(), **extra)

    def collect(self, rows):
        c.SOURCES['hitomi'] = lambda state: (rows, state, {'status': 'ok'})

    def save_items(self, items):
        c.save_json(c.DATA_FILE, {'items': items})

    def saved(self):
        return c.load_json(c.DATA_FILE, {})['items']

    def state(self):
        return c.load_json(c.FILTER_STATE_FILE, {})

    def test_existing_requested_terms_and_namespace_normalization(self):
        for tag in ('female:scat', 'male:MINIGUY', 'other:vore', 'blood', 'male:male',
                    'male:boys_love', 'male:boys’ love', 'ｆｅｍａｌｅ：ｆａｒｔｉｎｇ'):
            with self.subTest(tag=tag):
                self.assertTrue(c.blocked_reason(self.item(tags=[tag])))

    def test_japanese_and_english_aliases_apply_to_tags_and_titles(self):
        aliases = {'スカトロ': 'scat', 'おなら': 'farting', 'オナラ': 'farting',
                   'リョナ': 'ryona', '拷問': 'torture', '丸呑み': 'vore', '捕食': 'vore',
                   'ボーイズラブ': 'yaoi', 'ミニガイ': 'miniguy', '流血': 'blood', 'gore': 'guro'}
        for alias, canonical in aliases.items():
            with self.subTest(alias=alias):
                self.assertIn(canonical, c.blocked_reason(self.item(tags=['female:' + alias])))
                self.assertIn(canonical, c.blocked_reason(self.item(title='中立テスト【' + alias + '】')))

    def test_short_terms_do_not_match_benign_words_or_creator_fields(self):
        for title in ('Blue sky', 'Scattered samples', 'Bloodless example', 'グローバルな視点', 'マグロの図鑑',
                      'マイショタコンテスト'):
            with self.subTest(title=title):
                self.assertEqual(c.blocked_reason(self.item(title=title, artists=['blood'], groups=['guro'])), '')
        self.assertIn('guro', c.blocked_reason(self.item(title='中立テスト【グロ注意】')))

    def test_normalizer_only_marks_real_nonempty_tag_metadata_verified(self):
        for raw_tags in (None, [], {}, 'sample', [{'name': ''}]):
            with self.subTest(tags=raw_tags):
                value = hitomi._normalize(9000001, {'title': 'サンプル', 'language': 'japanese', 'tags': raw_tags}, '')
                self.assertTrue(c.unverified_hitomi(value, fresh=True))
                self.assertEqual(value['filter_metadata_checked'], '')
        value = hitomi._normalize(9000001, {'title': 'サンプル', 'language': 'japanese', 'tags': [{'tag': 'sample'}]}, '')
        self.assertFalse(c.unverified_hitomi(value, fresh=True))

    def test_tagless_new_work_is_held_not_published(self):
        self.collect([self.item(tags=[])])
        c.main()
        self.assertEqual(self.saved(), [])
        self.assertEqual(self.state()['records']['hitomi:9000001']['status'], 'pending')

    def test_missing_identity_fields_are_pending_not_permanently_blocked(self):
        self.collect([self.item(language='', title='', tags=[])])
        c.main()
        self.assertEqual(self.state()['records']['hitomi:9000001']['status'], 'pending')

    def test_existing_catalog_is_fully_rechecked_without_remote_requests(self):
        items = [self.item(tags=['blood']), self.item('9000002', title='サンプル【丸呑み】'),
                 self.item('9000003', tags=[]), self.item('9000004')]
        self.save_items(items)
        c.main(audit_only=True)
        self.assertEqual([x['uid'] for x in self.saved()], ['hitomi:9000004'])
        records = self.state()['records']
        self.assertEqual(records['hitomi:9000001']['status'], 'blocked')
        self.assertEqual(records['hitomi:9000003']['status'], 'pending')
        self.assertFalse(str(c.FILTER_STATE_FILE).startswith(str(c.DOCS_DIR)))
        self.assertEqual(records['hitomi:9000003']['item']['thumbnail'], items[2]['thumbnail'])

    def test_new_tag_families_and_variants(self):
        families = {
            "bestiality": ("female:bestiality", "male:bestiality", "bestiality",
                           "bestiality (female)", "low bestiality (female)",
                           "female:low bestiality", "animal on animal"),
            "shotacon": ("shotacon", "male:shotacon", "low shotacon"),
            "males only": ("males only", "male:males only"),
            "bara": ("bara", "low bara (mixed)"),
            "gay": ("gay", "male:gay"),
            "guro": ("low gore (other)",),
        }
        for reason, tags in families.items():
            for tag in tags:
                with self.subTest(tag=tag):
                    self.assertEqual(c.blocked_reason(self.item(tags=[tag])), f"blocked:{reason}")
        self.assertEqual(c.blocked_reason(self.item(title="【ショタコン】")), "blocked:title:shotacon")
        self.assertEqual(c.blocked_reason(self.item(title="獣姦")), "blocked:title:bestiality")
        self.assertEqual(c.blocked_reason(self.item(title="Bara no Hanazono", category="gay")), "")

    def test_ambiguous_tags_require_explicit_opt_in(self):
        for tag in ("male:shota", "female:kemonomimi", "female:dog girl", "male:dog",
                    "male:horse", "female:furry"):
            with self.subTest(tag=tag):
                self.assertEqual(c.blocked_reason(self.item(tags=[tag])), "")
        for tag in ("male:shota", "male:dog", "male:horse"):
            with patch.object(c, "NORMALIZED_BLOCK_FULL_TAGS", c.NORMALIZED_BLOCK_FULL_TAGS | {tag}):
                self.assertEqual(c.blocked_reason(self.item(tags=[tag])), f"blocked:{tag}")

    def test_chinese_title_markers(self):
        markers = ("汉化", "漢化", "汉化组", "漢化組", "中国翻訳", "中国翻译", "中國翻譯",
                   "中文翻译", "中文翻譯", "中文翻訳", "个人翻译", "個人翻譯", "机翻",
                   "机器翻译", "機械翻譯", "中译", "中譯", "简体中文", "簡體中文",
                   "繁體中文", "繁体中文", "简中", "簡中", "繁中", "中文版")
        for signal in (*markers, "[Chinese]", "[中国語]", "【中國語】", "（中国语）",
                       "&#91; Ｃｈｉｎｅｓｅ &#93;", "(中文)", "[中国翻訳]", "カラセイ日本語翻訳 [Chinese]",
                       "SKEB委託日本語/中文翻譯", "[Japaneseish] [Chinese]"):
            with self.subTest(signal=signal):
                item = self.item(title="我们读书", title_jp=f"例の作品 {signal}")
                self.assertEqual(c.blocked_reason(item), c.CHINESE_MARKER_REASON)

    def test_chinese_title_field_guards(self):
        for title in ("我们读书", "(们书)", "们书ー", "[作者 (画家)] 们书"):
            with self.subTest(title=title):
                self.assertEqual(c.blocked_reason(self.item(title=title)), c.CHINESE_SIMPLIFIED_REASON)
        for title in ("书书", "我们の読书", "[Chinese, Japanese] 我们读书",
                      "[中国語、日本語] 我们读书", "我们读书 (日本語/English/中文)",
                      "云开明 (19935159) 2026.09.17", "[冷冷冰可乐]仙子的修行",
                      "[龙裔 (画家)] 【书马】 仙子的修行"):
            with self.subTest(title=title):
                self.assertEqual(c.blocked_reason(self.item(title=title)), "")
    def test_japanese_labels_exempt_the_whole_work_unless_opted_in(self):
        marker = c.CHINESE_MARKER_REASON
        simplified = c.CHINESE_SIMPLIFIED_REASON
        for title, title_jp, reason in (
            ("[Chinese, Japanese]", "", marker),
            ("[Chinese] [Japanese]", "", marker),
            ("[Japanese] [Chinese]", "", marker),
            ("[Chinese] [Japanese] [English]", "", marker),
            ("[Chinese] [Japanese] [Digital]", "[个人汉化]", marker),
            ("[日本語、中国翻訳]", "", marker),
            ("[中国翻訳, 日本語]", "", marker),
            ("[Chinese, Japanese] [白杨汉化组]", "", marker),
            ("[Japanese/Chinese]", "[日本語/繁中]", marker),
            ("[Mixed Japanese &amp; Chinese versions]",
             "[日文版和中文版的混合・日本語版と中国語版の混在]", marker),
            ("[Japanese] 【基德漢化組】【日本語】", "", marker),
            ("[Japanese] [星野干拉个人翻译]", "[日本語翻訳]", marker),
            ("[星野干拉个人翻译]", "[日本語翻訳]", marker),
            ("[XAL] 寻找间谍 [中日雙語]", "[XAL] 熊三の物語 [Chinese＆Japanese]", marker),
            ("[XAL] 我们读书 [中日雙語]", "[XAL] 熊三の物語", simplified),
            ("[Chinese]", "【作者（English and 日文版）】", marker),
            ("[Chinese]", "［English＋日本語版］", marker),
            ("[Chinese]", "［English／日本翻訳］", marker),
        ):
            with self.subTest(title=title, title_jp=title_jp):
                item = self.item(title=title, title_jp=title_jp)
                self.assertEqual(c.blocked_reason(item), "")
                with patch.object(c, "CHINESE_BLOCK_BILINGUAL", True):
                    self.assertEqual(c.blocked_reason(item), reason)

        item = self.item(title="[Japanese] [Chinese]", tags=["male:shotacon"])
        self.assertEqual(c.blocked_reason(item), "blocked:shotacon")

    def test_snapshot_japanese_titles_are_kept(self):
        # Census kana examples and kanji-rich titles from the 2026-11-19 snapshot.
        for title in (
            "随笔爽图 落書き",
            "pixiv凤梨&#124;才羽ミドリ碧蓝档案小绿合集",
            "加贺さん漫画",
            "百合園セイア開発日記",
            "和風ファンタジーの剣士が女世界線とリンクして女体化",
            "[天聖] ヤマトさん温泉でマナーを学ぶ♡ (ワンピース) [日本語、英語]",
            "[yukio] 新キャラ 楓 5p漫画 [日本語、英語]",
            "くりいむレモンメモリー 記録写真集 Part3 SF超次元伝説ラル",
            "[Paya8] 天撃将軍開発日記 (崩壊:スターレイル) [無修正]",
            "[たけのこぺんぎん] 開発済み蛍 (原神) [日本語、中国語]",
            "[加瀬大輝] t4.豹変する漫研部の部長さん [日本語、英語]",
            "[菜さん] 極秘作戦記録 (明日方舟) [中国語、英語、日本語]",
            "[菜さん] 篠澤広 (学園アイドルマスター) [中国語、英語、日本語]",
        ):
            with self.subTest(title=title):
                self.assertEqual(c.blocked_reason(self.item(title=title)), "")

    def test_shotacon_tag_conditions(self):
        for tags, reason in (
            (["shotacon", "female:sample"], ""),
            (["shotacon", "sample (female)"], ""),
            (["shotacon", "sole female"], ""),
            (["shotacon", "lolicon"], ""),
            (["shotacon", "males only", "sole female"], "blocked:shotacon"),
            (["shotacon"], "blocked:shotacon"),
            (["male:shotacon", "female:lolicon"], ""),
            (["bestiality", "female:sample"], "blocked:bestiality"),
            (["shotacon", "yaoi", "sole female"], "blocked:shotacon"),
            (["shotacon", "male:yaoi", "sole female"], "blocked:shotacon"),
            (["male:yaoi", "shotacon", "sole female"], "blocked:male:yaoi"),
            (["shotacon", "ボーイズラブ", "sole female"], "blocked:shotacon"),
            (["shotacon", "bara", "sole female"], "blocked:shotacon"),
            (["shotacon", "male:gay", "sole female"], "blocked:shotacon"),
            (["shotacon", "bestiality", "sole female"], "blocked:bestiality"),
            (["low shotacon (male)", "ffm threesome"], ""),
            (["male:shota"], ""),
        ):
            with self.subTest(tags=tags):
                self.assertEqual(c.blocked_reason(self.item(tags=tags)), reason)
        self.assertEqual(c.blocked_reason(self.item(tags=["shotacon", "sole female"], category="bara")), "")
        self.assertEqual(c.blocked_reason(self.item(tags=["shotacon", "sole female"], title="【ショタコン】")),
                         "blocked:title:shotacon")

    def test_restores_only_eligible_shotacon_quarantine_records(self):
        eligible = self.item(tags=["shotacon", "female:sample"])
        records = {}
        for item in (
            eligible,
            self.item("9000002", tags=["shotacon", "female:sample", "bestiality"]),
            self.item("9000003", tags=["shotacon", "female:sample", "males only"]),
            self.item("9000004", tags=["shotacon", "female:sample"], title=""),
        ):
            c.quarantine_item({"records": records}, item, "blocked:shotacon", c.now_iso())
        c.save_json(c.FILTER_STATE_FILE, {"records": records})

        save = c.save_json

        def interrupted(path, payload):
            if path == c.DATA_FILE:
                raise OSError("simulated interrupted write")
            save(path, payload)

        with patch.object(c, "save_json", side_effect=interrupted):
            with self.assertRaises(OSError):
                c.main(audit_only=True)
        self.assertEqual(self.state()["records"], records)

        for _ in range(2):
            self.assertEqual(c.main(audit_only=True), 0)
            self.assertEqual([row["uid"] for row in self.saved()], [eligible["uid"]])
            self.assertEqual(self.state()["records"],
                             {uid: rec for uid, rec in records.items() if uid != eligible["uid"]})

    def test_new_rules_purge_saved_rows_across_sources(self):
        tags_by_source = {"ehentai": "female:low bestiality", "nhentai": "shotacon",
                          "asmhentai": "bestiality", "hitomi": "male:males only",
                          "3hentai": "low bestiality (female)"}
        items = [self.item(str(9000001 + index), source=source,
                           uid=f"{source}:{9000001 + index}", tags=[tag],
                           source_url=f"https://3hentai.net/d/{9000001 + index}/" if source == "3hentai" else "",
                           filter_metadata_checked="3hentai-gallery-v1" if source == "3hentai" else "")
                 for index, (source, tag) in enumerate(tags_by_source.items())]
        items.append(self.item("9000006", source="hentaifox", uid="hentaifox:9000006",
                               title="サンプル [Chinese]", tags=[]))
        for audit_only in (True, False):
            with self.subTest(audit_only=audit_only), patch.dict(c.SOURCES, {
                "nhentai": Mock(return_value=([], {}, {"status": "ok"})),
            }, clear=True):
                self.save_items(items)
                c.save_json(c.FILTER_STATE_FILE, {})
                self.assertEqual(c.main(audit_only=audit_only), 0)
                self.assertEqual(self.saved(), [])
                status = c.load_json(c.STATUS_FILE, {})["filters"]
                self.assertEqual(status["purged_existing_by_source"], dict.fromkeys([*tags_by_source, "hentaifox"], 1))

    def test_collection_hitomi_preserves_saved_japanese_label(self):
        original = self.fresh(title="サンプル [Japanese]")
        self.save_items([original])
        fetched = hitomi._normalize(9000001, {
            "japanese_title": "サンプル [Chinese]", "language": "japanese",
            "tags": [{"tag": "sample"}],
        }, "")
        self.collect([fetched])

        self.assertEqual(c.main(), 0)
        self.assertEqual(self.saved(), [])
        record = self.state()["records"][original["uid"]]
        self.assertEqual((record["status"], record["reason"]), ("pending", "metadata_unverified"))
        self.assertEqual(record["item"]["title"], original["title"])

    def test_collection_hitomi_blocks_chinese_without_saved_japanese_label(self):
        original = self.item(title="サンプル")
        self.save_items([original])
        fetched = hitomi._normalize(9000001, {
            "japanese_title": "サンプル [Chinese]", "language": "japanese",
            "tags": [{"tag": "sample"}],
        }, "")
        self.collect([fetched])

        self.assertEqual(c.main(), 0)
        self.assertEqual(self.saved(), [])
        record = self.state()["records"][original["uid"]]
        self.assertEqual((record["status"], record["reason"]),
                         ("blocked", "blocked:language:chinese:marker"))

    def test_collection_hitomi_requires_fetched_metadata_despite_saved_fields(self):
        for fields in (("language",), ("title", "title_jp"), ("filter_metadata_checked",)):
            with self.subTest(fields=fields):
                original = self.fresh(title_jp="サンプル")
                self.save_items([original])
                c.save_json(c.FILTER_STATE_FILE, {})
                fetched = dict(original)
                for field in fields:
                    fetched.pop(field)
                self.collect([fetched])

                self.assertEqual(c.main(), 0)
                self.assertEqual(self.saved(), [])
                record = self.state()["records"][original["uid"]]
                self.assertEqual((record["status"], record["reason"]), ("pending", "metadata_unverified"))
                for field in fields:
                    self.assertEqual(record["item"][field], original[field])

    def test_collection_ehentai_preserves_saved_japanese_label(self):
        original = self.item(source="ehentai", uid="ehentai:9000001", title="サンプル [Japanese]")
        self.save_items([original])
        fetched = dict(original, title="", title_jp="サンプル [Chinese]")
        c.SOURCES["ehentai"] = Mock(return_value=([fetched], {}, {"status": "ok"}))

        self.assertEqual(c.main(), 0)
        self.assertEqual([(row["uid"], row["title"], row["title_jp"]) for row in self.saved()],
                         [(original["uid"], original["title"], fetched["title_jp"])])
        self.assertNotIn(original["uid"], self.state()["records"])

    def test_newly_discovered_blocked_tag_removes_older_visible_row(self):
        self.save_items([self.item()])
        self.collect([self.fresh(tags=['male:scat'])])
        c.main()
        self.assertEqual(self.saved(), [])
        record = self.state()['records']['hitomi:9000001']
        self.assertEqual(record['reason'], 'blocked:male:scat')
        self.assertEqual(record['item']['tags'], ['male:scat'])

    def test_confirmed_exclusion_cannot_resurface_with_incomplete_or_changed_tags(self):
        self.save_items([self.item(tags=['blood'])])
        c.main(audit_only=True)
        evidence = copy.deepcopy(self.state()['records']['hitomi:9000001'])
        for later in (self.item(tags=[]), self.fresh(tags=['sample'])):
            self.collect([later])
            c.main()
            self.assertEqual(self.saved(), [])
            self.assertEqual(self.state()['records']['hitomi:9000001'], evidence)

    def test_audit_verifies_and_restores_pending_work_preserving_metadata(self):
        original = self.item(tags=[])
        self.save_items([original])
        c.main(audit_only=True)
        with patch.object(c, 'HITOMI_FILTER_AUDIT_LIMIT', 1), \
             patch.object(hitomi, 'fetch_filter_metadata', return_value=self.fresh(thumbnail='')):
            c.main(audit_only=True)
        saved = self.saved()[0]
        self.assertEqual(saved['thumbnail'], original['thumbnail'])
        self.assertEqual(saved['first_seen'], original['first_seen'])
        self.assertEqual(saved['filter_metadata_checked'], hitomi.FILTER_METADATA_VERSION)
        self.assertNotIn(original['uid'], self.state()['records'])
        self.assertEqual(self.state()['last_audit']['restored'], 1)

    def test_audit_applies_chinese_filter_to_titles_as_saved(self):
        cases = (("サンプル [Japanese]", "pending", "metadata_unverified"),
                 ("サンプル", "blocked", "blocked:language:chinese:marker"))
        for index, (title, status, reason) in enumerate(cases):
            with self.subTest(title=title):
                gid = str(9000001 + index)
                original = self.item(gid, title=title)
                self.save_items([original])
                c.save_json(c.FILTER_STATE_FILE, {})
                fresh = hitomi._normalize(int(gid), {
                    "japanese_title": "サンプル [Chinese]", "language": "japanese",
                    "tags": [{"tag": "sample"}],
                }, "")
                self.assertFalse(fresh["title"])
                self.assertEqual(fresh["title_jp"], "サンプル [Chinese]")
                with patch.object(c, "HITOMI_FILTER_AUDIT_LIMIT", 1), patch.object(
                    hitomi, "fetch_filter_metadata", return_value=fresh,
                ):
                    self.assertEqual(c.main(audit_only=True), 0)
                self.assertEqual(self.saved(), [])
                record = self.state()["records"][original["uid"]]
                self.assertEqual((record["status"], record["reason"]), (status, reason))
                self.assertEqual((record["item"]["title"], record["item"]["title_jp"]),
                                 (title, fresh["title_jp"]))

    def test_audit_discovers_blocked_metadata_and_removes_visible_row(self):
        self.save_items([self.item()])
        with patch.object(c, 'HITOMI_FILTER_AUDIT_LIMIT', 1), \
             patch.object(hitomi, 'fetch_filter_metadata', return_value=self.fresh(tags=['farting'])):
            c.main(audit_only=True)
        self.assertEqual(self.saved(), [])
        self.assertEqual(self.state()['last_audit']['blocked'], 1)

    def test_audit_prioritizes_pending_and_respects_per_work_retry(self):
        self.save_items([self.item(), self.item('9000002', tags=[])])
        with patch.object(c, 'HITOMI_FILTER_AUDIT_LIMIT', 1), \
             patch.object(hitomi, 'fetch_filter_metadata', side_effect=TimeoutError) as fetch:
            c.main(audit_only=True)
            self.assertEqual(fetch.call_args.args[1], 9000002)
            c.main(audit_only=True)
            self.assertEqual(fetch.call_args.args[1], 9000001)
            c.main(audit_only=True)
            self.assertEqual(fetch.call_count, 2)
        self.assertEqual(len(self.saved()), 1)
        self.assertEqual(self.state()['records']['hitomi:9000002']['status'], 'pending')

    def test_access_denial_stops_audit_and_persists_host_cooldown(self):
        self.save_items([self.item(str(num)) for num in range(9000001, 9000010)])
        with patch.object(c, 'HITOMI_FILTER_AUDIT_LIMIT', 20), \
             patch.object(hitomi, 'fetch_filter_metadata', side_effect=RuntimeError('HTTP 403')) as fetch:
            c.main(audit_only=True)
            c.main(audit_only=True)
            self.assertEqual(fetch.call_count, 1)
        self.assertTrue(self.state()['last_audit']['stopped'])
        self.assertEqual(len(self.saved()), 9)

    def test_three_network_failures_stop_without_erasing_saved_records(self):
        self.save_items([self.item(str(num)) for num in range(9000001, 9000010)])
        with patch.object(c, 'HITOMI_FILTER_AUDIT_LIMIT', 20), \
             patch.object(hitomi, 'fetch_filter_metadata', side_effect=TimeoutError) as fetch:
            c.main(audit_only=True)
            self.assertEqual(fetch.call_count, 3)
        self.assertEqual(len(self.saved()), 9)

    def test_verified_rows_do_not_repeat_audit_until_due(self):
        self.save_items([self.fresh()])
        with patch.object(c, 'HITOMI_FILTER_AUDIT_LIMIT', 1), \
             patch.object(hitomi, 'fetch_filter_metadata') as fetch:
            c.main(audit_only=True)
        fetch.assert_not_called()

    def test_removed_or_malformed_galleries_do_not_pause_the_entire_source(self):
        self.save_items([self.item(str(num)) for num in range(9000001, 9000005)])
        failures = [RuntimeError('HTTP 404'), RuntimeError('HTTP 410'), ValueError('malformed metadata'), self.fresh('9000004')]
        with patch.object(c, 'HITOMI_FILTER_AUDIT_LIMIT', 4), \
             patch.object(hitomi, 'fetch_filter_metadata', side_effect=failures) as fetch:
            c.main(audit_only=True)
        self.assertEqual(fetch.call_count, 4)
        state = self.state()
        self.assertFalse(state['last_audit']['stopped'])
        self.assertEqual(state['last_audit']['verified'], 1)
        self.assertEqual(state['last_audit']['failed_reasons'], {'http_404': 1, 'http_410': 1, 'metadata_invalid': 1})
        self.assertNotIn('hitomi_host_retry_at', state)

    def test_existing_unknown_host_cooldown_is_preserved(self):
        self.save_items([self.item()])
        future = c._timestamp(c.now_iso()) + 3600
        c.save_json(c.FILTER_STATE_FILE, {'hitomi_host_retry_at': future})
        with patch.object(c, 'HITOMI_FILTER_AUDIT_LIMIT', 1), \
             patch.object(hitomi, 'fetch_filter_metadata') as fetch:
            c.main(audit_only=True)
        fetch.assert_not_called()
        self.assertEqual(self.state()['hitomi_host_retry_at'], future)

    def test_restoration_checkpoint_survives_interrupted_catalog_write(self):
        self.save_items([self.item(tags=[])])
        c.main(audit_only=True)
        save = c.save_json
        def interrupted(path, payload):
            if path == c.DATA_FILE:
                raise OSError('simulated interrupted write')
            save(path, payload)
        with patch.object(c, 'HITOMI_FILTER_AUDIT_LIMIT', 1), \
             patch.object(hitomi, 'fetch_filter_metadata', return_value=self.fresh()), \
             patch.object(c, 'save_json', side_effect=interrupted):
            with self.assertRaises(OSError):
                c.main(audit_only=True)
        self.assertIn('hitomi:9000001', self.state()['verified_checkpoints'])
        c.main(audit_only=True)
        self.assertEqual(len(self.saved()), 1)
        self.assertEqual(self.state()['verified_checkpoints'], {})

    def test_metadata_only_fetch_does_not_request_images_or_switch_hosts(self):
        response = Mock(text='var galleryinfo = {"id":"9000001","title":"サンプル","language":"japanese","tags":[{"tag":"sample"}]};')
        session = Mock()
        with patch.object(hitomi, 'safe_get', return_value=response) as get, \
             patch.object(hitomi, '_thumbnail') as thumbnail:
            value = hitomi.fetch_filter_metadata(session, 9000001)
        self.assertEqual(value['thumbnail'], '')
        self.assertEqual(get.call_count, 1)
        thumbnail.assert_not_called()
        with patch.object(hitomi, 'safe_get', side_effect=RuntimeError('HTTP 429')) as get:
            with self.assertRaises(RuntimeError):
                hitomi.fetch_filter_metadata(session, 9000001)
        self.assertEqual(get.call_count, 1)

    def test_wrong_gallery_identity_cannot_restore_pending_work(self):
        self.save_items([self.item(tags=[])])
        with patch.object(c, 'HITOMI_FILTER_AUDIT_LIMIT', 1), \
             patch.object(hitomi, 'fetch_filter_metadata', return_value=self.fresh('9000002')):
            c.main(audit_only=True)
        self.assertEqual(self.saved(), [])
        self.assertEqual(self.state()['last_audit']['failed'], 1)


if __name__ == '__main__':
    unittest.main()
