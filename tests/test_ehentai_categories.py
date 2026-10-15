import contextlib
import copy
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import crawler as c
from tools import purge_ehentai_non_h as cleanup


class EhentaiCategoryTests(unittest.TestCase):
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
        self.enterContext(patch.object(c, "now_iso", return_value="2026-10-15T16:00:00+00:00"))
        self.enterContext(patch.object(c, "HITOMI_FILTER_AUDIT_LIMIT", 0))
        self.enterContext(patch.object(c.hentai3, "enrich_existing_for_filter", return_value={}))
        self.enterContext(patch.object(c.hitomi.requests.Session, "request", side_effect=AssertionError("Network forbidden")))
        self.enterContext(patch.dict(c.SOURCES, {}, clear=True))
        c.save_json(c.DATA_FILE, {"items": [], "custom_field": "preserved"})
        c.save_json(c.STATE_FILE, {"ehentai": {"next": "keep-cursor"}, "hitomi": {"offset": 900}})
        c.save_json(c.STATUS_FILE, {"ehentai": {"last_success": "unchanged"}, "filters": {"existing": True}})
        c.save_json(c.FILTER_STATE_FILE, {"records": {}, "hitomi_attempts": {"keep": {"retry_at": 100}}, "custom": True})

    def item(self, number="100", **extra):
        return {"uid": "ehentai:" + number, "source": "ehentai", "source_id": number,
                "category": "Non-H", "language": "japanese", "title": "Neutral sample " + number,
                "tags": ["sample"], "first_seen": "2026-01-01T00:00:00+00:00", **extra}

    def seed(self, items):
        data = c.load_json(c.DATA_FILE, {})
        data["items"] = items
        c.save_json(c.DATA_FILE, data)

    def saved(self):
        return c.load_json(c.DATA_FILE, {})["items"]

    def snapshot(self):
        return {str(path.relative_to(self.root)): path.read_bytes() for path in self.root.rglob("*.json")}

    def test_exact_category_normalization(self):
        for category in ("Non-H", "non-h", " NON H ", "Ｎｏｎ－Ｈ", "Non‐H", "Non‑H", "Non–H", "Non—H", "Non−H", "Non_H", "NonH"):
            with self.subTest(category=category):
                self.assertEqual(c.blocked_reason(self.item(category=category)), c.EHENTAI_NON_H_REASON)

    def test_no_title_tag_creator_or_similar_category_match(self):
        for category in ("Manga", "Non-Hero", "Non-H collection", "", None):
            with self.subTest(category=category):
                self.assertEqual(c.blocked_reason(self.item(category=category, title="Non-H sample",
                    tags=["Non-H"], artists=["Non-H"], groups=["Non-H"])), "")

    def test_other_sources_not_excluded(self):
        for source in ("hitomi", "3hentai", "asmhentai", "", None):
            with self.subTest(source=source):
                self.assertEqual(c.blocked_reason(self.item(source=source)), "")

    def test_new_and_archive_records_use_common_filter(self):
        c.SOURCES["ehentai"] = lambda state: ([self.item("100"), self.item("200"),
            self.item("300", category="Manga")], state, {"status": "ok"})
        c.main()
        self.assertEqual([x["uid"] for x in self.saved()], ["ehentai:300"])
        records = c.load_json(c.FILTER_STATE_FILE, {})["records"]
        self.assertEqual(set(records), {"ehentai:100", "ehentai:200"})

    def test_periodic_run_removes_saved_non_h(self):
        self.seed([self.item(), self.item("200", category="Manga")])
        c.main(audit_only=True)
        self.assertEqual([x["uid"] for x in self.saved()], ["ehentai:200"])

    def test_refreshed_category_removes_previously_visible_record(self):
        self.seed([self.item(category="Manga")])
        c.SOURCES["ehentai"] = lambda state: ([self.item()], state, {"status": "ok"})
        c.main()
        self.assertEqual(self.saved(), [])

    def test_confirmed_record_does_not_return_with_missing_category(self):
        self.seed([self.item()])
        cleanup.purge()
        evidence = copy.deepcopy(c.load_json(c.FILTER_STATE_FILE, {})["records"])
        c.SOURCES["ehentai"] = lambda state: ([self.item(category="")], state, {"status": "ok"})
        c.main()
        self.assertEqual(self.saved(), [])
        self.assertEqual(c.load_json(c.FILTER_STATE_FILE, {})["records"], evidence)

    def test_targeted_cleanup_preserves_other_rows_metadata_and_cursors(self):
        other = self.item("200", category="Manga", custom_metadata={"keep": [1, 2]})
        other_source = self.item("300", source="asmhentai", uid="asmhentai:300")
        unrelated_blocked = self.item("400", category="Manga", tags=["blood"])
        retained = [other, other_source, unrelated_blocked]
        self.seed([self.item()] + copy.deepcopy(retained))
        state = c.STATE_FILE.read_bytes()
        status = c.STATUS_FILE.read_bytes()
        report = cleanup.purge()
        self.assertEqual((report["before"], report["removed"], report["after"]), (4, 1, 3))
        self.assertTrue(report["verified"])
        self.assertEqual(self.saved(), retained)
        self.assertEqual(c.load_json(c.DATA_FILE, {})["custom_field"], "preserved")
        self.assertEqual(c.STATE_FILE.read_bytes(), state)
        self.assertEqual(c.STATUS_FILE.read_bytes(), status)
        store = c.load_json(c.FILTER_STATE_FILE, {})
        self.assertTrue(store["custom"])
        self.assertEqual(store["hitomi_attempts"], {"keep": {"retry_at": 100}})
        self.assertEqual(store["records"]["ehentai:100"]["item"], self.item())
        self.assertEqual(store["records"]["ehentai:100"]["reason"], c.EHENTAI_NON_H_REASON)

    def test_dry_run_never_writes(self):
        self.seed([self.item()])
        before = self.snapshot()
        self.assertEqual(cleanup.purge(dry_run=True)["removed"], 1)
        self.assertEqual(self.snapshot(), before)

    def test_repeat_cleanup_is_noop(self):
        self.seed([self.item()])
        cleanup.purge()
        before = self.snapshot()
        self.assertEqual(cleanup.purge()["removed"], 0)
        self.assertEqual(self.snapshot(), before)

    def test_duplicate_links_rebuilt_without_deleted_source(self):
        a = self.item(title="Neutral shared sample", pages=20)
        b = self.item("200", uid="asmhentai:200", source="asmhentai", title=a["title"], pages=20)
        rows = [a, b]
        self.assertEqual(c.apply_duplicate_groups(rows), 1)
        self.seed(rows)
        cleanup.purge()
        self.assertEqual([x["uid"] for x in self.saved()], [b["uid"]])
        self.assertNotIn("duplicate_uids", self.saved()[0])
        self.assertEqual(c.load_json(c.DATA_FILE, {})["duplicate_group_count"], 0)

    def test_existing_exclusion_evidence_is_preserved(self):
        old = {"item": self.item(), "reason": "blocked:other", "status": "blocked"}
        store = c.load_json(c.FILTER_STATE_FILE, {})
        store["records"]["ehentai:100"] = old
        c.save_json(c.FILTER_STATE_FILE, store)
        self.seed([self.item()])
        cleanup.purge()
        self.assertEqual(c.load_json(c.FILTER_STATE_FILE, {})["records"]["ehentai:100"], old)

    def test_invalid_catalog_schema_fails_without_writes(self):
        for payload in ({}, {"items": "invalid"}, {"items": [None]}, {"items": [self.item(), self.item()]}, {"items": [{"uid": ""}]}):
            with self.subTest(payload=payload):
                c.save_json(c.DATA_FILE, payload)
                before = self.snapshot()
                with self.assertRaises(ValueError):
                    cleanup.purge()
                self.assertEqual(self.snapshot(), before)

    def test_missing_or_corrupt_files_fail_without_writes(self):
        self.seed([self.item()])
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

    def test_invalid_quarantine_schema_fails_without_writes(self):
        self.seed([self.item()])
        for store in ({}, {"records": []}, {"records": {"old": "invalid"}}):
            with self.subTest(store=store):
                c.save_json(c.FILTER_STATE_FILE, store)
                before = self.snapshot()
                with self.assertRaises(ValueError):
                    cleanup.purge()
                self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
