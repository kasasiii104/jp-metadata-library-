import contextlib
import copy
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import crawler as c
from tools import purge_r18g as cleanup


class R18GFilterTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for name, value in {
            "DOCS_DIR": self.root / "docs", "DATA_FILE": self.root / "docs/data.json",
            "STATE_FILE": self.root / "docs/crawl_state.json",
            "STATUS_FILE": self.root / "docs/source_status.json",
            "FILTER_STATE_FILE": self.root / "state/filter_audit.json",
        }.items():
            self.enterContext(patch.object(c, name, value))
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.enterContext(patch.object(c, "now_iso", return_value="2026-10-19T07:30:00+00:00"))
        self.enterContext(patch.object(c, "HITOMI_FILTER_AUDIT_LIMIT", 0))
        self.enterContext(patch.object(c.hentai3, "enrich_existing_for_filter", return_value={}))
        self.enterContext(patch.object(c.hitomi.requests.Session, "request", side_effect=AssertionError("Network forbidden")))
        self.enterContext(patch.dict(c.SOURCES, {}, clear=True))
        c.save_json(c.DATA_FILE, {"items": [], "custom_field": "preserved"})
        c.save_json(c.STATE_FILE, {"ehentai": {"next": "keep"}, "hitomi": {"offset": 900}})
        c.save_json(c.STATUS_FILE, {"ehentai": {"last_success": "unchanged"}, "filters": {"existing": True}})
        c.save_json(c.FILTER_STATE_FILE, {"records": {}, "hitomi_attempts": {"keep": {"retry_at": 100}}, "custom": True})

    def item(self, number="100", source="ehentai", **extra):
        return {"uid": source + ":" + number, "source": source, "source_id": number,
                "category": "Manga", "language": "japanese", "title": "Neutral sample " + number,
                "tags": ["sample"], "first_seen": "2026-01-01T00:00:00+00:00", **extra}

    def seed(self, items):
        data = c.load_json(c.DATA_FILE, {})
        data["items"] = items
        c.save_json(c.DATA_FILE, data)

    def saved(self):
        return c.load_json(c.DATA_FILE, {})["items"]

    def store(self):
        return c.load_json(c.FILTER_STATE_FILE, {})

    def snapshot(self):
        return {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*.json")}

    def test_all_sources_fields_and_label_variants(self):
        variants = ("R-18G", "R18G", "r18g", "Ｒ－１８Ｇ", "R 18 G", "R_18_G", "R18-G",
                    "R‐18G", "R‑18G", "R–18G", "R—18G", "R−18G", "R\u200b18G", "R&#45;18G", "R１８g注意")
        for source in ("ehentai", "hitomi", "3hentai", "asmhentai", "hentaifox"):
            for variant in variants:
                for field in ("title", "title_jp", "category", "tags"):
                    with self.subTest(source=source, label=variant, field=field):
                        value = ["other:" + variant] if field == "tags" else "【" + variant + "】"
                        item = self.item(source=source, **{field: value})
                        self.assertEqual(c.blocked_reason(item), c.R18G_REASON)
                        self.assertEqual(c.r18g_match_fields(item), [field])

    def test_ordinary_r18_and_longer_words_are_not_r18g(self):
        for text in ("R-18", "R18", "Ｒ１８", "R18 Games", "R18Games", "XR18G", "R18Gamer", "R180G", "GR18G", "R-18 guide"):
            with self.subTest(text=text):
                self.assertEqual(c.blocked_reason(self.item(title=text, category=text, tags=[text])), "")

    def test_identity_fields_and_namespaced_identity_tags_are_not_scanned(self):
        tags = [name + ":R-18G" for name in c.IDENTITY_TAG_NAMESPACES]
        item = self.item(tags=tags, artists=["R18G"], groups=["R18G"], works=["R18G"],
                         parodies=["R18G"], characters=["R18G"], meta_tags=["work:R18G"])
        self.assertEqual(c.r18g_match_fields(item), [])
        self.assertEqual(c.blocked_reason(item), "")

    def test_namespace_and_string_tag_support(self):
        for tag in ("female:R18G", "male:R18G", "other:R18G", "warning:R18G", "その他：Ｒ－１８Ｇ", "R18G"):
            with self.subTest(tag=tag):
                self.assertEqual(c.blocked_reason(self.item(tags=tag)), c.R18G_REASON)

    def test_confirmed_warning_wins_over_hitomi_missing_metadata(self):
        for item in (self.item(source="hitomi", language="", tags=["R18G"]),
                     self.item(source="hitomi", title="", tags=["R-18G"])):
            self.assertEqual(c.publication_reason(item, fresh=True), c.R18G_REASON)
        self.assertEqual(c.publication_reason(self.item(source="hitomi", language=""), fresh=True), "metadata_unverified")

    def test_new_and_backfill_reject_all_sources_without_losing_confirmed_warnings(self):
        for source in ("ehentai", "hitomi", "3hentai", "asmhentai"):
            rows = [self.item("100", source, title="Sample [R-18G]", language=""),
                    self.item("200", source, tags=["other:R18G"])]
            c.SOURCES[source] = lambda state, rows=rows: (rows, state, {"status": "ok"})
        c.main()
        self.assertEqual(self.saved(), [])
        self.assertEqual(len(self.store()["records"]), 8)
        self.assertTrue(all(rec["status"] == "blocked" and rec["reason"] == c.R18G_REASON
                            for rec in self.store()["records"].values()))

    def test_periodic_run_rechecks_existing_records(self):
        self.seed([self.item(title="Sample [R18G]"), self.item("200")])
        c.main(audit_only=True)
        self.assertEqual([x["uid"] for x in self.saved()], ["ehentai:200"])

    def test_new_warning_removes_old_visible_version(self):
        self.seed([self.item()])
        c.SOURCES["ehentai"] = lambda state: ([self.item(tags=["R-18G"])], state, {"status": "ok"})
        c.main()
        self.assertEqual(self.saved(), [])
        self.assertEqual(self.store()["records"]["ehentai:100"]["reason"], c.R18G_REASON)

    def test_excluded_record_cannot_return_with_missing_label(self):
        self.seed([self.item(tags=["R-18G"])])
        cleanup.purge()
        evidence = copy.deepcopy(self.store()["records"])
        c.SOURCES["ehentai"] = lambda state: ([self.item()], state, {"status": "ok"})
        c.main()
        self.assertEqual(self.saved(), [])
        self.assertEqual(self.store()["records"], evidence)

    def test_cleanup_preserves_unrelated_items_cursors_and_status(self):
        targets = [self.item(tags=["R18G"]), self.item("200", "asmhentai", title="Sample R-18G")]
        safe = [self.item("300", custom_metadata={"keep": [1, 2]}), self.item("400", title="Sample [R18]")]
        self.seed(targets + copy.deepcopy(safe))
        state, status = c.STATE_FILE.read_bytes(), c.STATUS_FILE.read_bytes()
        report = cleanup.purge()
        self.assertEqual((report["before"], report["removed"], report["after"]), (4, 2, 2))
        self.assertEqual(report["remaining_r18g"], 0)
        self.assertTrue(report["verified"])
        self.assertEqual(self.saved(), safe)
        self.assertEqual(c.load_json(c.DATA_FILE, {})["custom_field"], "preserved")
        self.assertEqual(c.STATE_FILE.read_bytes(), state)
        self.assertEqual(c.STATUS_FILE.read_bytes(), status)
        self.assertEqual(self.store()["hitomi_attempts"], {"keep": {"retry_at": 100}})
        self.assertEqual(self.store()["records"][targets[0]["uid"]]["item"], targets[0])

    def test_dry_run_never_writes(self):
        self.seed([self.item(tags=["R18G"])])
        before = self.snapshot()
        self.assertEqual(cleanup.purge(dry_run=True)["removed"], 1)
        self.assertEqual(self.snapshot(), before)

    def test_repeat_cleanup_is_noop(self):
        self.seed([self.item(tags=["R18G"])])
        cleanup.purge()
        before = self.snapshot()
        self.assertEqual(cleanup.purge()["removed"], 0)
        self.assertEqual(self.snapshot(), before)

    def test_empty_match_audit_leaves_existing_data_unchanged(self):
        self.seed([self.item()])
        before = self.snapshot()
        report = cleanup.purge()
        self.assertTrue(report["verified"])
        after = self.snapshot()
        after.pop("docs/r18g_filter_audit.json")
        self.assertEqual(after, before)

    def test_duplicate_links_drop_deleted_member(self):
        a = self.item(title="Neutral shared sample", tags=["R18G"], pages=20)
        b = self.item("200", "asmhentai", title=a["title"], pages=20)
        rows = [a, b]
        self.assertEqual(c.apply_duplicate_groups(rows), 1)
        self.seed(rows)
        cleanup.purge()
        self.assertEqual([x["uid"] for x in self.saved()], [b["uid"]])
        self.assertNotIn("duplicate_uids", self.saved()[0])
        self.assertEqual(c.load_json(c.DATA_FILE, {})["duplicate_group_count"], 0)

    def test_pending_and_checkpoint_evidence_becomes_permanent_exclusion(self):
        store = self.store()
        item = self.item(source="hitomi", tags=["R18G"])
        checkpoint = self.item("200", "hitomi", title="Sample R18G")
        store["records"][item["uid"]] = {"status": "pending", "reason": "metadata_unverified", "item": item}
        store["verified_checkpoints"] = {checkpoint["uid"]: checkpoint}
        c.save_json(c.FILTER_STATE_FILE, store)
        report = cleanup.purge()
        self.assertEqual(report["held_records_blocked"], 2)
        self.assertTrue(all(rec["status"] == "blocked" for rec in self.store()["records"].values()))
        self.assertEqual(self.store()["verified_checkpoints"], store["verified_checkpoints"])

    def test_saved_evidence_removes_row_with_missing_current_label(self):
        self.seed([self.item()])
        store = self.store()
        store["records"]["ehentai:100"] = {"item": self.item(tags=["R18G"]), "status": "blocked", "reason": "blocked:guro"}
        c.save_json(c.FILTER_STATE_FILE, store)
        report = cleanup.purge()
        self.assertEqual(report["removed_from_saved_evidence"], 1)
        self.assertEqual(self.saved(), [])
        self.assertEqual(self.store()["records"], store["records"])

    def test_invalid_catalog_schema_fails_without_writes(self):
        for payload in ({}, {"items": "invalid"}, {"items": [None]}, {"items": [self.item(), self.item()]}, {"items": [{"uid": ""}]}):
            with self.subTest(payload=payload):
                c.save_json(c.DATA_FILE, payload)
                before = self.snapshot()
                with self.assertRaises(ValueError):
                    cleanup.purge()
                self.assertEqual(self.snapshot(), before)

    def test_invalid_evidence_schema_fails_without_writes(self):
        for store in ({}, {"records": []}, {"records": {"old": "bad"}}, {"records": {"old": {"item": []}}},
                      {"records": {}, "verified_checkpoints": []},
                      {"records": {}, "verified_checkpoints": {"wrong": self.item(tags=["R18G"])}}):
            with self.subTest(store=store):
                c.save_json(c.FILTER_STATE_FILE, store)
                before = self.snapshot()
                with self.assertRaises(ValueError):
                    cleanup.purge()
                self.assertEqual(self.snapshot(), before)

    def test_missing_and_corrupt_files_fail_without_writes(self):
        for path in (c.DATA_FILE, c.FILTER_STATE_FILE, c.STATE_FILE, c.STATUS_FILE):
            with self.subTest(path=path.name):
                original = path.read_bytes()
                path.unlink()
                before = self.snapshot()
                with self.assertRaises(FileNotFoundError):
                    cleanup.purge()
                self.assertEqual(self.snapshot(), before)
                path.write_bytes(original)
        for path in (c.DATA_FILE, c.FILTER_STATE_FILE):
            with self.subTest(corrupt=path.name):
                original = path.read_bytes()
                path.write_text("{", encoding="utf-8")
                before = self.snapshot()
                with self.assertRaises(ValueError):
                    cleanup.purge()
                self.assertEqual(self.snapshot(), before)
                path.write_bytes(original)


if __name__ == "__main__":
    unittest.main()
