import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import crawler
from tools import cache_3hentai_thumbs, cache_asmhentai_thumbs, json_storage


ROW = {"uid": "asmhentai:1", "source": "asmhentai", "source_id": "1",
       "title": "sample", "language": "japanese", "tags": ["sample"]}


class JsonPersistenceTests(unittest.TestCase):
    def setUp(self):
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        self.tmp_path = Path(temporary_directory.name)

    def patch_attribute(self, target, name, value):
        patcher = patch.object(target, name, value)
        patcher.start()
        self.addCleanup(patcher.stop)

    def crawler_files(self, initialize=True):
        paths = []
        for name in ("DATA_FILE", "STATE_FILE", "STATUS_FILE", "FILTER_STATE_FILE"):
            path = self.tmp_path / (name + ".json")
            self.patch_attribute(crawler, name, path)
            if initialize:
                json_storage.save_json(path, {"items": [ROW]} if name == "DATA_FILE" else {})
            paths.append(path)
        self.patch_attribute(crawler, "DOCS_DIR", self.tmp_path)
        self.patch_attribute(crawler.hentai3, "enrich_existing_for_filter", lambda items: {})
        self.patch_attribute(crawler, "HITOMI_FILTER_AUDIT_LIMIT", 0)
        return paths

    def test_crawler_invalid_input_never_saves(self):
        paths = self.crawler_files()
        for invalid in ('{', '[]', 'null'):
            for index in range(4):
                with self.subTest(index=index, invalid=invalid):
                    for position, path in enumerate(paths):
                        json_storage.save_json(path, {"items": [ROW]} if position == 0 else {})
                    paths[index].write_text(invalid)
                    before = [p.read_bytes() for p in paths]
                    with patch.object(crawler, "save_json") as save, \
                         patch.object(crawler.hentai3, "enrich_existing_for_filter") as audit:
                        with self.assertRaises((ValueError, TypeError)):
                            crawler.main()
                        save.assert_not_called()
                        audit.assert_not_called()
                    self.assertEqual([p.read_bytes() for p in paths], before)

    def test_invalid_catalog_rows_cannot_be_silently_dropped(self):
        self.crawler_files()
        for items in (None, {}, [None], [{}], [dict(ROW, tags="sample")]):
            with self.subTest(items=items):
                json_storage.save_json(crawler.DATA_FILE, {"items": items})
                with patch.object(crawler, "save_json") as save:
                    with self.assertRaises(ValueError):
                        crawler.main()
                    save.assert_not_called()

    def test_validate_catalog_rejects_duplicate_uids(self):
        catalog = {"items": [ROW, dict(ROW, title="another title")]}
        with self.assertRaisesRegex(ValueError, ROW["uid"]):
            json_storage.validate_catalog(catalog)

    def test_load_json_rejects_duplicate_catalog_uids(self):
        path = self.tmp_path / "duplicate.json"
        json_storage.save_json(path, {"items": [ROW, dict(ROW)]})
        with self.assertRaisesRegex(ValueError, ROW["uid"]):
            json_storage.load_json(path, {"items": []}, json_storage.validate_catalog)

    def test_validate_catalog_accepts_unique_uids(self):
        other = dict(ROW, uid="asmhentai:2", source_id="2")
        for items in ([], [ROW], [ROW, other]):
            with self.subTest(items=items):
                json_storage.validate_catalog({"items": items})

    def test_validate_catalog_rejects_non_string_metadata(self):
        fields = ("language", "title", "title_jp", "category", "source_token",
                  "source_url", "source_url_kind", "resolved_gallery_id",
                  "posted_at", "thumbnail", "first_seen")
        for field in fields:
            for value in ([], {}, 123):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ValueError):
                        json_storage.validate_catalog({"items": [dict(ROW, **{field: value})]})

    def test_validate_catalog_rejects_invalid_source_id_types(self):
        for source_id in (True, False, []):
            with self.subTest(source_id=source_id):
                with self.assertRaises(ValueError):
                    json_storage.validate_catalog({"items": [dict(ROW, source_id=source_id)]})

    def test_validate_catalog_accepts_integer_and_string_source_ids(self):
        for source_id in (0, 123, "123"):
            with self.subTest(source_id=source_id):
                json_storage.validate_catalog({"items": [dict(ROW, source_id=source_id)]})

    def test_validate_catalog_requires_works_array(self):
        with self.assertRaises(ValueError):
            json_storage.validate_catalog({"items": [dict(ROW, works="sample")]})
        for works in ([], ["sample"]):
            with self.subTest(works=works):
                json_storage.validate_catalog({"items": [dict(ROW, works=works)]})

    def test_validate_catalog_accepts_missing_empty_and_null_metadata(self):
        minimal = {"uid": ROW["uid"], "source": ROW["source"]}
        json_storage.validate_catalog({"items": [minimal]})
        fields = ("language", "title", "title_jp", "category", "source_token",
                  "source_url", "source_url_kind", "resolved_gallery_id",
                  "posted_at", "thumbnail", "first_seen", "source_id")
        for value in ("sample", "", None):
            with self.subTest(value=value):
                row = dict(minimal, **dict.fromkeys(fields, value))
                json_storage.validate_catalog({"items": [row]})

    def test_load_json_rejects_catalog_with_language_array(self):
        path = self.tmp_path / "invalid_language.json"
        path.write_text(json.dumps({"items": [dict(ROW, language=[])]}), encoding="utf-8")
        with self.assertRaises(ValueError):
            json_storage.load_json(path, {"items": []}, json_storage.validate_catalog)

    def test_validate_filter_store_accepts_unique_checkpoint_uids(self):
        other = dict(ROW, uid="asmhentai:2", source_id="2")
        json_storage.validate_filter_store({
            "records": {ROW["uid"]: {"item": ROW}, other["uid"]: {"item": other}},
            "verified_checkpoints": {ROW["uid"]: ROW, other["uid"]: other},
        })

    def test_validate_filter_store_rejects_checkpoint_key_uid_mismatch(self):
        key = "asmhentai:2"
        with self.assertRaisesRegex(ValueError, key):
            json_storage.validate_filter_store({"verified_checkpoints": {key: ROW}})

    def test_validate_filter_store_rejects_record_key_uid_mismatch(self):
        key = "asmhentai:2"
        with self.assertRaisesRegex(ValueError, key):
            json_storage.validate_filter_store({"records": {key: {"item": ROW}}})

    def test_validate_filter_store_rejects_invalid_record_items(self):
        missing_uid = {key: value for key, value in ROW.items() if key != "uid"}
        for entry in ({}, {"item": None}, {"item": []}, {"item": missing_uid}):
            with self.subTest(entry=entry):
                with self.assertRaises(ValueError):
                    json_storage.validate_filter_store({"records": {ROW["uid"]: entry}})

    def test_load_json_rejects_filter_store_key_uid_mismatch(self):
        path = self.tmp_path / "invalid_filter_store.json"
        key = "asmhentai:2"
        for field, entry in (("verified_checkpoints", ROW), ("records", {"item": ROW})):
            with self.subTest(field=field):
                json_storage.save_json(path, {field: {key: entry}})
                with self.assertRaisesRegex(ValueError, key):
                    json_storage.load_json(path, {}, json_storage.validate_filter_store)

    def test_all_collectors_fail_before_any_final_save(self):
        paths = self.crawler_files()
        self.patch_attribute(crawler, "SOURCES", {
            "asmhentai": lambda state: ([], state, {"status": "error"}),
        })
        before = [p.read_bytes() for p in paths]
        with patch.object(crawler, "save_json") as save:
            self.assertEqual(crawler.main(), 1)
            save.assert_not_called()
        self.assertEqual([p.read_bytes() for p in paths], before)

    def test_partial_success_preserves_existing_other_sources(self):
        self.crawler_files()
        incoming = dict(ROW, uid="ehentai:2", source="ehentai", source_id="2", category="manga")
        self.patch_attribute(crawler, "SOURCES", {
            "asmhentai": lambda state: ([], state, {"status": "error"}),
            "ehentai": lambda state: ([incoming], state, {"status": "ok"}),
        })
        self.assertEqual(crawler.main(), 0)
        self.assertEqual(
            {x["uid"] for x in json.loads(crawler.DATA_FILE.read_text())["items"]},
            {ROW["uid"], incoming["uid"]},
        )

    def test_first_run_total_failure_does_not_create_catalog(self):
        self.crawler_files(initialize=False)
        self.patch_attribute(crawler, "SOURCES", {
            "asmhentai": lambda state: ([], state, {"status": "error"}),
        })
        self.assertEqual(crawler.main(), 1)
        self.assertFalse(crawler.DATA_FILE.exists())
        self.assertFalse(crawler.STATE_FILE.exists())

    def test_cache_invalid_inputs_do_not_save(self):
        for module in (cache_3hentai_thumbs, cache_asmhentai_thumbs):
            for target in ("DATA", "STATUS"):
                for invalid in ('{', '[]', 'null', '{"items":[{}]}'):
                    with self.subTest(module=module.__name__, target=target, invalid=invalid):
                        data = self.tmp_path / "DATA.json"
                        status = self.tmp_path / "STATUS.json"
                        json_storage.save_json(data, {"items": [ROW]})
                        json_storage.save_json(status, {})
                        with patch.object(module, "DATA", data), patch.object(module, "STATUS", status):
                            getattr(module, target).write_text(invalid)
                            before = [data.read_bytes(), status.read_bytes()]
                            with patch.object(module, "save") as save, \
                                 patch.object(module.requests, "Session") as session:
                                with self.assertRaises(ValueError):
                                    module.main()
                                save.assert_not_called()
                                session.assert_not_called()
                            self.assertEqual([data.read_bytes(), status.read_bytes()], before)

    def cache_3hentai_files(self):
        for name, relative in (("DATA", "docs/data.json"),
                               ("STATUS", "docs/source_status.json"),
                               ("OUTDIR", "docs/thumbs/3hentai")):
            self.patch_attribute(cache_3hentai_thumbs, name, self.tmp_path / relative)
        return cache_3hentai_thumbs.DATA

    def test_cache_3hentai_missing_catalog_exits_without_side_effects(self):
        self.cache_3hentai_files()
        with patch("builtins.print") as output, \
             patch.object(cache_3hentai_thumbs.requests, "Session") as session, \
             patch.object(cache_3hentai_thumbs, "load") as load, \
             patch.object(cache_3hentai_thumbs, "save") as save:
            self.assertEqual(cache_3hentai_thumbs.main(), 0)
            output.assert_called_once_with("[3hentai-thumbs] docs/data.json not found")
            session.assert_not_called()
            load.assert_not_called()
            save.assert_not_called()
        self.assertFalse(cache_3hentai_thumbs.OUTDIR.exists())

    def test_cache_3hentai_invalid_or_unreadable_catalog_raises(self):
        data = self.cache_3hentai_files()
        data.parent.mkdir(parents=True)
        data.write_text("{", encoding="utf-8")
        with patch.object(cache_3hentai_thumbs.requests, "Session") as session:
            with self.assertRaises(json.JSONDecodeError):
                cache_3hentai_thumbs.main()
            data.write_text('{"items": []}', encoding="utf-8")
            with patch.object(Path, "open", side_effect=PermissionError("denied")):
                with self.assertRaises(PermissionError):
                    cache_3hentai_thumbs.main()
            session.assert_not_called()
        self.assertFalse(cache_3hentai_thumbs.OUTDIR.exists())

    def test_cache_3hentai_dangling_catalog_symlink_raises(self):
        data = self.cache_3hentai_files()
        data.parent.mkdir(parents=True)
        data.symlink_to(data.parent / "missing.json")
        with patch.object(cache_3hentai_thumbs.requests, "Session") as session:
            with self.assertRaises(FileNotFoundError):
                cache_3hentai_thumbs.main()
            session.assert_not_called()
        self.assertFalse(cache_3hentai_thumbs.OUTDIR.exists())

    def test_cache_3hentai_catalog_without_3hentai_rows_proceeds(self):
        data = self.cache_3hentai_files()
        catalog = {"items": [ROW]}
        json_storage.save_json(data, catalog)
        with patch.object(cache_3hentai_thumbs.requests, "Session") as session:
            self.assertEqual(cache_3hentai_thumbs.main(), 0)
            session.assert_called_once_with()
            session.return_value.get.assert_not_called()
            session.return_value.request.assert_not_called()
        self.assertTrue(cache_3hentai_thumbs.OUTDIR.is_dir())
        self.assertEqual(json.loads(data.read_text()), catalog)
        status = json.loads(cache_3hentai_thumbs.STATUS.read_text())
        self.assertEqual(status["3hentai"]["thumbnail_cache"]["attempted"], 0)

    def test_missing_and_unreadable_are_distinct(self):
        path = self.tmp_path / "missing.json"
        self.assertEqual(
            json_storage.load_json(path, {"items": []}, json_storage.validate_catalog),
            {"items": []},
        )
        with patch.object(Path, "open", side_effect=PermissionError("denied")):
            with self.assertRaises(PermissionError):
                json_storage.load_json(path, {})

    def test_atomic_save_failure_keeps_original(self):
        for failure in ("serialize", "replace"):
            with self.subTest(failure=failure):
                path = self.tmp_path / "data.json"
                path.write_text('{"items": []}\n')
                before = path.read_bytes()
                payload = {"items": [object()]} if failure == "serialize" else {"items": [ROW]}
                if failure == "replace":
                    with patch.object(json_storage.os, "replace", side_effect=OSError("interrupted")):
                        with self.assertRaises(OSError):
                            json_storage.save_json(path, payload)
                else:
                    with self.assertRaises(TypeError):
                        json_storage.save_json(path, payload)
                self.assertEqual(path.read_bytes(), before)

    def test_atomic_save_replaces_only_after_complete_json(self):
        path = self.tmp_path / "data.json"
        path.write_text('{"items": []}')
        replace = json_storage.os.replace

        def checked_replace(source, destination):
            self.assertEqual(Path(source).parent, path.parent)
            self.assertEqual(json.loads(Path(source).read_text()), {"items": [ROW]})
            self.assertEqual(json.loads(path.read_text()), {"items": []})
            replace(source, destination)

        with patch.object(json_storage.os, "replace", side_effect=checked_replace):
            json_storage.save_json(path, {"items": [ROW]})
        self.assertEqual(json.loads(path.read_text()), {"items": [ROW]})


if __name__ == "__main__":
    unittest.main()
