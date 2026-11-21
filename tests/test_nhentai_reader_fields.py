import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

import crawler
from sources import nhentai
from sources.net_policy import URLPolicyError
from sources.reader_policy import normalize_reader_tag
from tools.build_public_catalog import compact_item


STAMP = "2026-11-21T00:00:00+00:00"
GID = "697843"
FIELDS = {"reader_media_id": "4333747", "reader_exts": "ww"}
DETAIL_PATH = Path(__file__).parent / "fixtures" / "nhentai" / f"detail-{GID}.json"


def saved(gid, **changes):
    return {
        "uid": f"nhentai:{gid}", "source": "nhentai", "source_id": gid,
        "title": "sample", "language": "japanese", "tags": ["original"], "pages": 2,
        **changes,
    }


def audit(rows, results, *, records=None, previous=None, readers=15, tag_limit=15):
    with patch.multiple(crawler, NH_READER_BACKFILL_LIMIT=readers,
                        NH_EXISTING_TAG_AUDIT_LIMIT=tag_limit), patch.object(
        nhentai, "_detail", side_effect=results,
    ) as fetch, patch.object(nhentai, "polite_sleep"):
        status = crawler.audit_existing_nhentai(
            rows, records or {}, previous or {}, STAMP, nhentai.DetailRun(),
        )
    return [call.args[1] for call in fetch.call_args_list], status


class NhentaiReaderFieldsTests(unittest.TestCase):
    def setUp(self):
        self.detail_text = DETAIL_PATH.read_text()
        self.detail = self.new_detail()

    def new_detail(self):
        raw = json.loads(self.detail_text)
        raw["num_pages"] = len(raw["pages"])
        return raw

    def test_api_v2_fixture_and_normalize(self):
        detail = self.detail
        self.assertEqual(nhentai.reader_fields(detail, GID, ["sample"]), FIELDS)
        row = nhentai._normalize(detail)
        self.assertEqual({key: row[key] for key in FIELDS}, FIELDS)

    def test_extension_codes_are_in_path_order(self):
        detail = self.detail
        detail["media_id"] = int(detail["media_id"])
        detail["pages"] = [
            {"path": f'galleries/{detail["media_id"]}/{number}.{ext}'}
            for number, ext in enumerate(("gif", "png", "webp", "jpg"), 1)
        ]
        detail["num_pages"] = len(detail["pages"])
        self.assertEqual(nhentai.reader_fields(detail, GID, ["sample"]), {
            "reader_media_id": FIELDS["reader_media_id"], "reader_exts": "gpwj",
        })

    def test_invalid_detail(self):
        for changes in [
            {"id": 1}, {"media_id": "04333747"}, {"media_id": "1" * 13},
            {"media_id": True}, {"media_id": "１２"}, {"media_id": "abc"},
            {"pages": None}, {"pages": [], "num_pages": 0}, {"pages": [{}]},
            {"num_pages": 12}, {"num_pages": "2"}, {"num_pages": False},
        ]:
            with self.subTest(changes=changes):
                detail = self.new_detail()
                self.assertEqual(nhentai.reader_fields({**detail, **changes}, GID, ["sample"]), {})

    def test_invalid_page_path(self):
        for path in [
            "galleries/3/1.webp", "galleries/4333747/2.webp", "galleries/4333747/01.webp",
            "galleries/4333747/1.jpeg", "galleries/4333747/1.WEBP",
            "galleries/4333747/1.webp\n", "galleries/4333747/1.webp?x=1", None,
        ]:
            with self.subTest(path=path):
                detail = self.new_detail()
                detail["pages"][0]["path"] = path
                self.assertEqual(nhentai.reader_fields(detail, GID, ["sample"]), {})

    def test_reader_excludes_tags(self):
        for tags in [
            [], [""], [1], ["lolicon"], ["female:lolicon"], ["Shotacon (male)"], ["ロリコン"],
            ["ﾃｽﾄ：タグ：ＳＨＯＴＡＣＯＮ （ M ）"], ["x:LOW__-LOLI"], ["続編 ｼｮﾀｺﾝ"],
            ["loli"], ["shota"], ["oppai-loli"], ["toddlercon"], ["low-lolicon"],
            ["low shotacon"], ["low shota"],
        ]:
            with self.subTest(tags=tags):
                detail = self.new_detail()
                self.assertEqual(nhentai.reader_fields(detail, GID, tags), {})
                detail["tags"] = [{"type": "tag", "name": tag} for tag in tags]
                self.assertTrue(FIELDS.keys().isdisjoint(nhentai._normalize(detail)))
                self.assertTrue(FIELDS.keys().isdisjoint(compact_item(saved(GID, tags=tags, **FIELDS))))

    def test_reader_tag_normalization(self):
        self.assertEqual(normalize_reader_tag(" Ｆｅｍａｌｅ ：  Ａ’Ｂ_ｃ- d "), "female:a'b c d")

    def test_backfill_candidates_and_shared_fetch(self):
        detail = self.detail
        dual = saved("10", tags=[])
        tagged = saved("11")
        rows = [
            dual, tagged, saved("12"), saved("13", reader_checked=crawler.NH_READER_AUDIT_VERSION),
            saved("14", reader_media_id="4333747"), saved("15"),
            saved("16", tags=["shotacon"]), saved("17", source="hitomi"),
            saved("18", uid="nhentai:19"), saved("bad"),
        ]
        calls, status = audit(rows, [{**detail, "id": 10}, {**detail, "id": 11}], readers=2,
                              previous={"last_attempted_id": "19"},
                              records={"nhentai:15": {"status": "blocked"}})
        self.assertEqual(calls, ["10", "11"])
        self.assertEqual(status["attempted"], 2)
        self.assertEqual(status["tagged"], 1)
        self.assertEqual(status["reader_filled"], 2)
        self.assertEqual(status["reader_unavailable"], 0)
        self.assertEqual(dual["filter_metadata_checked"], crawler.NH_TAG_AUDIT_VERSION)
        self.assertEqual(dual["tags"], nhentai._normalize(detail)["tags"])
        self.assertEqual(tagged["tags"], ["original"])
        for row in (dual, tagged):
            self.assertEqual({key: row[key] for key in FIELDS}, FIELDS)
            self.assertEqual(row["reader_checked"], crawler.NH_READER_AUDIT_VERSION)
            self.assertEqual(row["reader_checked_at"], STAMP)
        self.assertTrue(all("reader_checked_at" not in row for row in rows[2:]))
        calls, _ = audit(rows, [{**detail, "id": 12}], readers=15,
                         records={"nhentai:15": {"status": "blocked"}})
        self.assertEqual(calls, ["12"])

    def test_backfill_limit_and_cursor(self):
        detail = self.detail
        rows = [saved("10"), saved("11")]
        calls, status = audit(rows, [{**detail, "id": 11}], readers=1,
                              previous={"last_attempted_id": "10"})
        self.assertEqual(calls, ["11"])
        self.assertEqual(status["last_attempted_id"], "11")
        self.assertNotIn("reader_checked", rows[0])

    def test_backfill_terminal_unavailable(self):
        for response in [
            nhentai.HTTPError(404), ValueError("not an object"),
            {"tags": [], "pages": []}, {"id": int(GID), "tags": None},
            {"media_id": "invalid"}, {"id": 999},
            {"tags": [{"type": "tag", "name": "female:lolicon"}]},
        ]:
            with self.subTest(response=response):
                detail = self.new_detail()
                row = saved(GID)
                result = {**detail, **response} if isinstance(response, dict) else response
                calls, status = audit([row], [result])
                self.assertEqual(calls, [GID])
                self.assertEqual(row, saved(GID, reader_checked=crawler.NH_READER_AUDIT_VERSION))
                self.assertEqual(status["reader_unavailable"], 1)
                self.assertEqual(status["reader_filled"], 0)
                self.assertEqual(audit([row], [])[0], [])

    def test_backfill_transport_failure_is_retryable(self):
        for error, stopped in [
            (nhentai.HTTPError(500), False), (requests.ConnectionError("offline"), False),
            (URLPolicyError("denied"), True), (nhentai.HTTPError(401), True),
            (nhentai.HTTPError(403), True), (nhentai.HTTPError(429), True),
        ]:
            with self.subTest(error=error, stopped=stopped):
                detail = self.new_detail()
                row = saved(GID)
                calls, status = audit([row], [error])
                self.assertEqual(calls, [GID])
                self.assertEqual(row, saved(GID))
                self.assertIs(status["stopped"], stopped)
                self.assertEqual(status["reader_unavailable"], 0)
                self.assertEqual(audit([row], [detail])[0], [GID])

    def test_backfill_skips_saved_exclusion(self):
        detail = self.detail
        row = saved(GID, tags=["female:lolicon"])
        calls, status = audit([row], [detail])
        self.assertEqual(calls, [])
        self.assertEqual(status["attempted"], 0)
        self.assertEqual(row, saved(GID, tags=["female:lolicon"]))

    def test_backfill_marks_detail_exclusion(self):
        detail = self.detail
        row = saved(GID)
        detail["tags"] = [{"type": "tag", "name": "shotacon"}]
        calls, status = audit([row], [detail])
        self.assertEqual(calls, [GID])
        self.assertEqual(status["reader_unavailable"], 1)
        self.assertEqual(row, saved(GID, reader_checked=crawler.NH_READER_AUDIT_VERSION))

    def test_backfill_marks_empty_detail_tags_unavailable_despite_saved_tags(self):
        detail = self.detail
        row = saved(GID)
        detail["tags"] = []
        calls, status = audit([row], [detail])
        self.assertEqual(calls, [GID])
        self.assertEqual(status["reader_unavailable"], 1)
        self.assertEqual(status["reader_filled"], 0)
        self.assertEqual(row, saved(GID, reader_checked=crawler.NH_READER_AUDIT_VERSION))

    def test_backfill_skips_empty_saved_tags_without_current_tag_audit(self):
        for tag_audited, tag_limit in [(True, 15), (False, 0)]:
            with self.subTest(tag_audited=tag_audited, tag_limit=tag_limit):
                detail = self.new_detail()
                row = saved(GID, tags=[])
                if tag_audited:
                    row["filter_metadata_checked"] = crawler.NH_TAG_AUDIT_VERSION
                calls, status = audit([row], [detail], tag_limit=tag_limit)
                self.assertEqual(calls, [])
                self.assertEqual(status["attempted"], 0)
                self.assertEqual(row["tags"], [])
                self.assertNotIn("reader_checked", row)
                self.assertTrue(FIELDS.keys().isdisjoint(row))

    def test_backfill_publishes_reader_fields_with_tags_from_same_audit(self):
        detail = self.detail
        row = saved(GID, tags=[])
        calls, status = audit([row], [detail])
        self.assertEqual(calls, [GID])
        self.assertEqual(status["tagged"], 1)
        self.assertEqual(status["reader_filled"], 1)
        self.assertEqual(row["tags"], nhentai._normalize(detail)["tags"])
        self.assertEqual(row["filter_metadata_checked"], crawler.NH_TAG_AUDIT_VERSION)
        self.assertEqual(row["reader_checked"], crawler.NH_READER_AUDIT_VERSION)
        self.assertEqual({key: row[key] for key in FIELDS}, FIELDS)
        public = compact_item(row)
        self.assertEqual({key: public[key] for key in FIELDS}, FIELDS)

    def test_backfill_retries_non_json_response(self):
        detail = self.detail
        row = saved(GID)
        error = requests.exceptions.JSONDecodeError("Expecting value", "<html>", 0)
        calls, status = audit([row], [error])
        self.assertEqual(calls, [GID])
        self.assertEqual(status["reader_unavailable"], 0)
        self.assertEqual(row, saved(GID))
        self.assertEqual(audit([row], [detail])[0], [GID])

    def test_clean_and_merge_reader_fields(self):
        row = saved(GID, reader_media_id=4333747, reader_exts="ww",
                    reader_checked=crawler.NH_READER_AUDIT_VERSION, reader_checked_at=STAMP)
        cleaned = crawler.clean_item(row, STAMP)
        self.assertEqual({key: cleaned[key] for key in FIELDS}, FIELDS)
        self.assertEqual(cleaned["reader_checked"], row["reader_checked"])
        self.assertEqual(cleaned["reader_checked_at"], STAMP)
        refreshed = saved(GID, reader_media_id=FIELDS["reader_media_id"], reader_exts="wj")
        merged = crawler.merge_item(cleaned, refreshed, STAMP)
        self.assertEqual(merged["reader_media_id"], FIELDS["reader_media_id"])
        self.assertEqual(merged["reader_exts"], "wj")
        self.assertEqual(merged["reader_checked_at"], STAMP)
        other = crawler.clean_item({**row, "source": "hitomi"}, STAMP)
        self.assertTrue(all(other[key] == "" for key in (*FIELDS, "reader_checked", "reader_checked_at")))

    def test_detail_refresh_replaces_reader_pair(self):
        for tags, published in [
            ([], False), (["shotacon"], False), (["original"], True),
        ]:
            with self.subTest(tags=tags, published=published):
                detail = self.new_detail()
                old = saved(GID, **FIELDS, reader_checked="internal", reader_checked_at=STAMP)
                detail["tags"] = [{"type": "tag", "name": tag} for tag in tags]
                detail["pages"][1]["path"] = f'galleries/{detail["media_id"]}/2.jpg'
                merged = crawler.merge_item(old, nhentai._normalize(detail), STAMP)
                public = compact_item(merged)
                if published:
                    self.assertEqual(merged["reader_checked"], "internal")
                    self.assertEqual(merged["reader_checked_at"], STAMP)
                    self.assertEqual(public["reader_media_id"], FIELDS["reader_media_id"])
                    self.assertEqual(public["reader_exts"], "wj")
                else:
                    self.assertTrue(all(merged[key] == "" for key in FIELDS))
                    self.assertTrue(FIELDS.keys().isdisjoint(public))
                    # 司令塔追加（FIX11c 後）: 材料が消えた行は監査印も外れ、バックフィルの対象に戻る
                    self.assertEqual(merged["reader_checked"], "")
                    self.assertEqual(merged["reader_checked_at"], "")

    def test_detail_refresh_without_reader_fields_reopens_backfill(self):
        """Losing reader fields on a detail refresh makes the row eligible for backfill."""
        detail = self.detail
        old = saved(GID, **FIELDS, reader_checked=crawler.NH_READER_AUDIT_VERSION, reader_checked_at=STAMP)
        refreshed = saved(GID)
        merged = crawler.merge_item(old, refreshed, STAMP)
        self.assertTrue(all(merged[key] == "" for key in FIELDS))
        self.assertEqual(merged["reader_checked"], "")
        self.assertEqual(audit([merged], [detail])[0], [GID])

    def test_empty_detail_refresh_and_backfill_keep_reader_fields_unpublished(self):
        detail = self.detail
        old = saved(GID, **FIELDS, reader_checked=crawler.NH_READER_AUDIT_VERSION, reader_checked_at=STAMP)
        detail["tags"] = []
        refreshed = nhentai._normalize(detail)
        merged = crawler.merge_item(old, refreshed, STAMP)
        self.assertEqual(merged["tags"], ["original"])
        self.assertTrue(all(merged[key] == "" for key in FIELDS))
        self.assertEqual(merged["reader_checked"], "")
        self.assertEqual(merged["reader_checked_at"], "")

        calls, status = audit([merged], [detail])
        self.assertEqual(calls, [GID])
        self.assertEqual(status["reader_unavailable"], 1)
        self.assertEqual(merged["reader_checked"], crawler.NH_READER_AUDIT_VERSION)
        self.assertEqual(merged["reader_checked_at"], "")
        self.assertTrue(all(merged[key] == "" for key in FIELDS))
        self.assertTrue(FIELDS.keys().isdisjoint(compact_item(merged)))

        merged = crawler.merge_item(merged, refreshed, STAMP)
        self.assertEqual(merged["reader_checked"], crawler.NH_READER_AUDIT_VERSION)
        self.assertEqual(audit([merged], [detail])[0], [])
        self.assertTrue(FIELDS.keys().isdisjoint(compact_item(merged)))

    def test_reader_page_limit(self):
        for page_count, published in [(2000, True), (2001, False)]:
            with self.subTest(page_count=page_count, published=published):
                detail = self.new_detail()
                detail["num_pages"] = page_count
                detail["pages"] = [
                    {"path": f'galleries/{detail["media_id"]}/{number}.webp'}
                    for number in range(1, page_count + 1)
                ]
                fields = {**FIELDS, "reader_exts": "w" * page_count}
                public = compact_item(saved(GID, pages=page_count, **fields))
                self.assertEqual(nhentai.reader_fields(detail, GID, ["original"]), (fields if published else {}))
                self.assertEqual({key: public[key] for key in FIELDS if key in public}, (fields if published else {}))

    def test_public_reader_pair_hides_audit_markers(self):
        out = compact_item(saved(GID, **FIELDS, reader_checked="internal", reader_checked_at=STAMP))
        self.assertEqual({key: out[key] for key in FIELDS}, FIELDS)
        self.assertNotIn("reader_checked", out)
        self.assertNotIn("reader_checked_at", out)

    def test_public_reader_pair_is_atomic(self):
        for changes in [
            {"source": "hitomi"}, {"tags": []}, {"tags": "sample"}, {"tags": [1]},
            {"reader_media_id": ""}, {"reader_media_id": 4333747},
            {"reader_media_id": "012"}, {"reader_media_id": "1" * 13},
            {"reader_media_id": "123\n"}, {"reader_exts": ""}, {"reader_exts": "Wx"},
            {"reader_exts": "ww\n"}, {"reader_exts": ["w", "w"]},
            {"pages": "2"}, {"pages": 2.0}, {"pages": 1}, {"pages": True, "reader_exts": "w"},
        ]:
            with self.subTest(changes=changes):
                self.assertTrue(FIELDS.keys().isdisjoint(compact_item(saved(GID, **{**FIELDS, **changes}))))

    def test_reader_failure_limit_keeps_later_rows_unattempted(self):
        rows = [saved(gid) for gid in ("10", "11", "12")]
        with patch.object(nhentai, "NH_DETAIL_MAX_FAILURES", 2):
            calls, status = audit(rows, [nhentai.HTTPError(500)] * 2)
        self.assertEqual(calls, ["10", "11"])
        self.assertEqual(status["last_attempted_id"], "11")
        self.assertEqual(rows, [saved(gid) for gid in ("10", "11", "12")])

    def test_main_persists_reader_backfill(self):
        detail = self.detail
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        docs = root / "docs"
        self.enterContext(patch.multiple(
            crawler, DOCS_DIR=docs, DATA_FILE=docs / "data.json",
            STATE_FILE=docs / "crawl_state.json", STATUS_FILE=docs / "source_status.json",
            FILTER_STATE_FILE=root / "state" / "filter_audit.json", HITOMI_FILTER_AUDIT_LIMIT=0,
        ))
        self.enterContext(patch.object(crawler, "SOURCES", {
            "hitomi": lambda state: ([], state, {"status": "ok"}),
        }))
        self.enterContext(patch.object(
            crawler.hentai3, "enrich_existing_for_filter", lambda *args, **kwargs: {},
        ))
        crawler.save_json(crawler.DATA_FILE, {"items": [saved(GID)]})
        with patch.object(nhentai, "_detail", return_value=detail) as fetch:
            self.assertEqual(crawler.main(), 0)
            self.assertEqual(crawler.main(), 0)
        fetch.assert_called_once()
        row, = crawler.load_json(crawler.DATA_FILE, {})["items"]
        self.assertEqual(row["tags"], ["original"])
        self.assertEqual(row["reader_checked"], crawler.NH_READER_AUDIT_VERSION)
        self.assertEqual({key: compact_item(row)[key] for key in FIELDS}, FIELDS)


if __name__ == "__main__":
    unittest.main()
