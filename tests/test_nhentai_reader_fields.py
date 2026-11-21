import json
from pathlib import Path
from unittest.mock import patch

import pytest
import requests

import crawler
from sources import nhentai
from sources.net_policy import URLPolicyError
from sources.reader_policy import normalize_reader_tag
from tools.build_public_catalog import compact_item


STAMP = "2026-11-21T00:00:00+00:00"
GID = "697843"
FIELDS = {"reader_media_id": "4333747", "reader_exts": "ww"}


@pytest.fixture
def detail():
    path = Path(__file__).parent / "fixtures" / "nhentai" / f"detail-{GID}.json"
    raw = json.loads(path.read_text())
    raw["num_pages"] = len(raw["pages"])
    return raw


def test_api_v2_fixture_and_normalize(detail):
    assert nhentai.reader_fields(detail, GID, ["sample"]) == FIELDS
    row = nhentai._normalize(detail)
    assert {key: row[key] for key in FIELDS} == FIELDS


def test_extension_codes_are_in_path_order(detail):
    detail["media_id"] = int(detail["media_id"])
    detail["pages"] = [
        {"path": f'galleries/{detail["media_id"]}/{number}.{ext}'}
        for number, ext in enumerate(("gif", "png", "webp", "jpg"), 1)
    ]
    detail["num_pages"] = len(detail["pages"])
    assert nhentai.reader_fields(detail, GID, ["sample"]) == {
        "reader_media_id": FIELDS["reader_media_id"], "reader_exts": "gpwj",
    }


@pytest.mark.parametrize("changes", [
    {"id": 1}, {"media_id": "04333747"}, {"media_id": "1" * 13},
    {"media_id": True}, {"media_id": "１２"}, {"media_id": "abc"},
    {"pages": None}, {"pages": [], "num_pages": 0}, {"pages": [{}]},
    {"num_pages": 12}, {"num_pages": "2"}, {"num_pages": False},
])
def test_invalid_detail(detail, changes):
    assert nhentai.reader_fields({**detail, **changes}, GID, ["sample"]) == {}


@pytest.mark.parametrize("path", [
    "galleries/3/1.webp", "galleries/4333747/2.webp", "galleries/4333747/01.webp",
    "galleries/4333747/1.jpeg", "galleries/4333747/1.WEBP",
    "galleries/4333747/1.webp\n", "galleries/4333747/1.webp?x=1", None,
])
def test_invalid_page_path(detail, path):
    detail["pages"][0]["path"] = path
    assert nhentai.reader_fields(detail, GID, ["sample"]) == {}


@pytest.mark.parametrize("tags", [
    [], [""], [1], ["lolicon"], ["female:lolicon"], ["Shotacon (male)"], ["ロリコン"],
    ["ﾃｽﾄ：タグ：ＳＨＯＴＡＣＯＮ （ M ）"], ["x:LOW__-LOLI"], ["続編 ｼｮﾀｺﾝ"],
    ["loli"], ["shota"], ["oppai-loli"], ["toddlercon"], ["low-lolicon"],
    ["low shotacon"], ["low shota"],
])
def test_reader_excludes_tags(detail, tags):
    assert nhentai.reader_fields(detail, GID, tags) == {}
    detail["tags"] = [{"type": "tag", "name": tag} for tag in tags]
    assert FIELDS.keys().isdisjoint(nhentai._normalize(detail))
    assert FIELDS.keys().isdisjoint(compact_item(saved(GID, tags=tags, **FIELDS)))


def test_reader_tag_normalization():
    assert normalize_reader_tag(" Ｆｅｍａｌｅ ：  Ａ’Ｂ_ｃ- d ") == "female:a'b c d"


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


def test_backfill_candidates_and_shared_fetch(detail):
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
    assert calls == ["10", "11"]
    assert status["attempted"] == 2
    assert status["tagged"] == 1
    assert status["reader_filled"] == 2
    assert status["reader_unavailable"] == 0
    assert dual["filter_metadata_checked"] == crawler.NH_TAG_AUDIT_VERSION
    assert dual["tags"] == nhentai._normalize(detail)["tags"]
    assert tagged["tags"] == ["original"]
    for row in (dual, tagged):
        assert {key: row[key] for key in FIELDS} == FIELDS
        assert row["reader_checked"] == crawler.NH_READER_AUDIT_VERSION
        assert row["reader_checked_at"] == STAMP
    assert all("reader_checked_at" not in row for row in rows[2:])
    calls, _ = audit(rows, [{**detail, "id": 12}], readers=15,
                     records={"nhentai:15": {"status": "blocked"}})
    assert calls == ["12"]


def test_backfill_limit_and_cursor(detail):
    rows = [saved("10"), saved("11")]
    calls, status = audit(rows, [{**detail, "id": 11}], readers=1,
                          previous={"last_attempted_id": "10"})
    assert calls == ["11"]
    assert status["last_attempted_id"] == "11"
    assert "reader_checked" not in rows[0]


@pytest.mark.parametrize("response", [
    nhentai.HTTPError(404), ValueError("not an object"),
    {"tags": [], "pages": []}, {"id": int(GID), "tags": None},
    {"media_id": "invalid"}, {"id": 999},
    {"tags": [{"type": "tag", "name": "female:lolicon"}]},
])
def test_backfill_terminal_unavailable(detail, response):
    row = saved(GID)
    result = {**detail, **response} if isinstance(response, dict) else response
    calls, status = audit([row], [result])
    assert calls == [GID]
    assert row == saved(GID, reader_checked=crawler.NH_READER_AUDIT_VERSION)
    assert status["reader_unavailable"] == 1
    assert status["reader_filled"] == 0
    assert audit([row], [])[0] == []


@pytest.mark.parametrize("error, stopped", [
    (nhentai.HTTPError(500), False), (requests.ConnectionError("offline"), False),
    (URLPolicyError("denied"), True), (nhentai.HTTPError(401), True),
    (nhentai.HTTPError(403), True), (nhentai.HTTPError(429), True),
])
def test_backfill_transport_failure_is_retryable(detail, error, stopped):
    row = saved(GID)
    calls, status = audit([row], [error])
    assert calls == [GID]
    assert row == saved(GID)
    assert status["stopped"] is stopped
    assert status["reader_unavailable"] == 0
    assert audit([row], [detail])[0] == [GID]


def test_backfill_skips_saved_exclusion(detail):
    row = saved(GID, tags=["female:lolicon"])
    calls, status = audit([row], [detail])
    assert calls == []
    assert status["attempted"] == 0
    assert row == saved(GID, tags=["female:lolicon"])


def test_backfill_marks_detail_exclusion(detail):
    row = saved(GID)
    detail["tags"] = [{"type": "tag", "name": "shotacon"}]
    calls, status = audit([row], [detail])
    assert calls == [GID]
    assert status["reader_unavailable"] == 1
    assert row == saved(GID, reader_checked=crawler.NH_READER_AUDIT_VERSION)


def test_backfill_marks_empty_detail_tags_unavailable_despite_saved_tags(detail):
    row = saved(GID)
    detail["tags"] = []
    calls, status = audit([row], [detail])
    assert calls == [GID]
    assert status["reader_unavailable"] == 1
    assert status["reader_filled"] == 0
    assert row == saved(GID, reader_checked=crawler.NH_READER_AUDIT_VERSION)


@pytest.mark.parametrize("tag_audited, tag_limit", [(True, 15), (False, 0)])
def test_backfill_skips_empty_saved_tags_without_current_tag_audit(detail, tag_audited, tag_limit):
    row = saved(GID, tags=[])
    if tag_audited:
        row["filter_metadata_checked"] = crawler.NH_TAG_AUDIT_VERSION
    calls, status = audit([row], [detail], tag_limit=tag_limit)
    assert calls == []
    assert status["attempted"] == 0
    assert row["tags"] == []
    assert "reader_checked" not in row
    assert FIELDS.keys().isdisjoint(row)


def test_backfill_publishes_reader_fields_with_tags_from_same_audit(detail):
    row = saved(GID, tags=[])
    calls, status = audit([row], [detail])
    assert calls == [GID]
    assert status["tagged"] == 1
    assert status["reader_filled"] == 1
    assert row["tags"] == nhentai._normalize(detail)["tags"]
    assert row["filter_metadata_checked"] == crawler.NH_TAG_AUDIT_VERSION
    assert row["reader_checked"] == crawler.NH_READER_AUDIT_VERSION
    assert {key: row[key] for key in FIELDS} == FIELDS
    public = compact_item(row)
    assert {key: public[key] for key in FIELDS} == FIELDS


def test_backfill_retries_non_json_response(detail):
    row = saved(GID)
    error = requests.exceptions.JSONDecodeError("Expecting value", "<html>", 0)
    calls, status = audit([row], [error])
    assert calls == [GID]
    assert status["reader_unavailable"] == 0
    assert row == saved(GID)
    assert audit([row], [detail])[0] == [GID]


def test_clean_and_merge_reader_fields():
    row = saved(GID, reader_media_id=4333747, reader_exts="ww",
                reader_checked=crawler.NH_READER_AUDIT_VERSION, reader_checked_at=STAMP)
    cleaned = crawler.clean_item(row, STAMP)
    assert {key: cleaned[key] for key in FIELDS} == FIELDS
    assert cleaned["reader_checked"] == row["reader_checked"]
    assert cleaned["reader_checked_at"] == STAMP
    refreshed = saved(GID, reader_media_id=FIELDS["reader_media_id"], reader_exts="wj")
    merged = crawler.merge_item(cleaned, refreshed, STAMP)
    assert merged["reader_media_id"] == FIELDS["reader_media_id"]
    assert merged["reader_exts"] == "wj"
    assert merged["reader_checked_at"] == STAMP
    other = crawler.clean_item({**row, "source": "hitomi"}, STAMP)
    assert all(other[key] == "" for key in (*FIELDS, "reader_checked", "reader_checked_at"))


@pytest.mark.parametrize("tags, published", [
    ([], False), (["shotacon"], False), (["original"], True),
])
def test_detail_refresh_replaces_reader_pair(detail, tags, published):
    old = saved(GID, **FIELDS, reader_checked="internal", reader_checked_at=STAMP)
    detail["tags"] = [{"type": "tag", "name": tag} for tag in tags]
    detail["pages"][1]["path"] = f'galleries/{detail["media_id"]}/2.jpg'
    merged = crawler.merge_item(old, nhentai._normalize(detail), STAMP)
    public = compact_item(merged)
    if published:
        assert merged["reader_checked"] == "internal"
        assert merged["reader_checked_at"] == STAMP
        assert public["reader_media_id"] == FIELDS["reader_media_id"]
        assert public["reader_exts"] == "wj"
    else:
        assert all(merged[key] == "" for key in FIELDS)
        assert FIELDS.keys().isdisjoint(public)
        # 司令塔追加（FIX11c 後）: 材料が消えた行は監査印も外れ、バックフィルの対象に戻る
        assert merged["reader_checked"] == ""
        assert merged["reader_checked_at"] == ""


def test_detail_refresh_without_reader_fields_reopens_backfill(detail):
    """Losing reader fields on a detail refresh makes the row eligible for backfill."""
    old = saved(GID, **FIELDS, reader_checked=crawler.NH_READER_AUDIT_VERSION, reader_checked_at=STAMP)
    refreshed = saved(GID)
    merged = crawler.merge_item(old, refreshed, STAMP)
    assert all(merged[key] == "" for key in FIELDS)
    assert merged["reader_checked"] == ""
    assert audit([merged], [detail])[0] == [GID]


def test_empty_detail_refresh_and_backfill_keep_reader_fields_unpublished(detail):
    old = saved(GID, **FIELDS, reader_checked=crawler.NH_READER_AUDIT_VERSION, reader_checked_at=STAMP)
    detail["tags"] = []
    refreshed = nhentai._normalize(detail)
    merged = crawler.merge_item(old, refreshed, STAMP)
    assert merged["tags"] == ["original"]
    assert all(merged[key] == "" for key in FIELDS)
    assert merged["reader_checked"] == ""
    assert merged["reader_checked_at"] == ""

    calls, status = audit([merged], [detail])
    assert calls == [GID]
    assert status["reader_unavailable"] == 1
    assert merged["reader_checked"] == crawler.NH_READER_AUDIT_VERSION
    assert merged["reader_checked_at"] == ""
    assert all(merged[key] == "" for key in FIELDS)
    assert FIELDS.keys().isdisjoint(compact_item(merged))

    merged = crawler.merge_item(merged, refreshed, STAMP)
    assert merged["reader_checked"] == crawler.NH_READER_AUDIT_VERSION
    assert audit([merged], [detail])[0] == []
    assert FIELDS.keys().isdisjoint(compact_item(merged))


@pytest.mark.parametrize("page_count, published", [(2000, True), (2001, False)])
def test_reader_page_limit(detail, page_count, published):
    detail["num_pages"] = page_count
    detail["pages"] = [
        {"path": f'galleries/{detail["media_id"]}/{number}.webp'}
        for number in range(1, page_count + 1)
    ]
    fields = {**FIELDS, "reader_exts": "w" * page_count}
    public = compact_item(saved(GID, pages=page_count, **fields))
    assert nhentai.reader_fields(detail, GID, ["original"]) == (fields if published else {})
    assert {key: public[key] for key in FIELDS if key in public} == (fields if published else {})


def test_public_reader_pair_hides_audit_markers():
    out = compact_item(saved(GID, **FIELDS, reader_checked="internal", reader_checked_at=STAMP))
    assert {key: out[key] for key in FIELDS} == FIELDS
    assert "reader_checked" not in out
    assert "reader_checked_at" not in out


@pytest.mark.parametrize("changes", [
    {"source": "hitomi"}, {"tags": []}, {"tags": "sample"}, {"tags": [1]},
    {"reader_media_id": ""}, {"reader_media_id": 4333747},
    {"reader_media_id": "012"}, {"reader_media_id": "1" * 13},
    {"reader_media_id": "123\n"}, {"reader_exts": ""}, {"reader_exts": "Wx"},
    {"reader_exts": "ww\n"}, {"reader_exts": ["w", "w"]},
    {"pages": "2"}, {"pages": 2.0}, {"pages": 1}, {"pages": True, "reader_exts": "w"},
])
def test_public_reader_pair_is_atomic(changes):
    assert FIELDS.keys().isdisjoint(compact_item(saved(GID, **{**FIELDS, **changes})))


def test_reader_failure_limit_keeps_later_rows_unattempted():
    rows = [saved(gid) for gid in ("10", "11", "12")]
    with patch.object(nhentai, "NH_DETAIL_MAX_FAILURES", 2):
        calls, status = audit(rows, [nhentai.HTTPError(500)] * 2)
    assert calls == ["10", "11"]
    assert status["last_attempted_id"] == "11"
    assert rows == [saved(gid) for gid in ("10", "11", "12")]


def test_main_persists_reader_backfill(tmp_path, detail, monkeypatch):
    docs = tmp_path / "docs"
    monkeypatch.setattr(crawler, "DOCS_DIR", docs)
    monkeypatch.setattr(crawler, "DATA_FILE", docs / "data.json")
    monkeypatch.setattr(crawler, "STATE_FILE", docs / "crawl_state.json")
    monkeypatch.setattr(crawler, "STATUS_FILE", docs / "source_status.json")
    monkeypatch.setattr(crawler, "FILTER_STATE_FILE", tmp_path / "state" / "filter_audit.json")
    monkeypatch.setattr(crawler, "HITOMI_FILTER_AUDIT_LIMIT", 0)
    monkeypatch.setattr(crawler, "SOURCES", {
        "hitomi": lambda state: ([], state, {"status": "ok"}),
    })
    monkeypatch.setattr(crawler.hentai3, "enrich_existing_for_filter", lambda *args, **kwargs: {})
    crawler.save_json(crawler.DATA_FILE, {"items": [saved(GID)]})
    with patch.object(nhentai, "_detail", return_value=detail) as fetch:
        assert crawler.main() == 0
        assert crawler.main() == 0
    fetch.assert_called_once()
    row, = crawler.load_json(crawler.DATA_FILE, {})["items"]
    assert row["tags"] == ["original"]
    assert row["reader_checked"] == crawler.NH_READER_AUDIT_VERSION
    assert {key: compact_item(row)[key] for key in FIELDS} == FIELDS
