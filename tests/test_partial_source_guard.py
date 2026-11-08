import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import crawler as c


class PartialSourceGuardTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(
            dir=Path(__file__).resolve().parents[1])))
        for name, value in {
            "DOCS_DIR": self.root / "docs",
            "DATA_FILE": self.root / "docs/data.json",
            "STATE_FILE": self.root / "docs/crawl_state.json",
            "STATUS_FILE": self.root / "docs/source_status.json",
            "FILTER_STATE_FILE": self.root / "state/filter_audit.json",
        }.items():
            self.enterContext(patch.object(c, name, value))
        self.output = self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.enterContext(patch.object(c, "now_iso", return_value="2026-11-08T00:00:00+00:00"))
        self.enterContext(patch.object(c, "HITOMI_FILTER_AUDIT_LIMIT", 0))
        self.enterContext(patch.object(c.hentai3, "enrich_existing_for_filter", return_value={}))
        self.enterContext(patch.object(c.hitomi.requests.Session, "request",
                                       side_effect=AssertionError("Network forbidden")))
        self.enterContext(patch.dict(c.SOURCES, {}, clear=True))

    def item(self, number="100", source="ehentai", **extra):
        return {"uid": source + ":" + number, "source": source, "source_id": number,
                "category": "Manga", "language": "japanese", "title": "Sample " + number,
                "tags": ["sample"], **extra}

    def test_non_ok_collector_mutation_does_not_change_saved_state(self):
        original_state = {"next": "keep", "nested": {"pages": [1, 2]}}
        c.save_json(c.STATE_FILE, {"ehentai": original_state})

        def failed_collector(state):
            state["added"] = "partial"
            state["nested"]["pages"].append(3)
            return [], state, {"status": "error"}

        c.SOURCES["ehentai"] = failed_collector
        c.SOURCES["asmhentai"] = lambda state: (
            [], {"page": 2}, {"status": "ok"})

        self.assertEqual(c.main(), 0)

        saved_state = c.load_json(c.STATE_FILE, {})
        self.assertEqual(saved_state["ehentai"], original_state)
        self.assertEqual(saved_state["asmhentai"], {"page": 2})

    def test_non_ok_partial_results_are_discarded_while_ok_results_are_saved(self):
        for status in ("error", "empty"):
            # A non-object state file is rejected up front by validate_source_mapping.
            for old_state in ({"next": "keep"}, {}):
                with self.subTest(status=status, old_state=old_state):
                    original = self.item()
                    c.save_json(c.DATA_FILE, {"items": [original]})
                    c.save_json(c.STATE_FILE, {"ehentai": old_state})
                    c.save_json(c.STATUS_FILE, {"ehentai": {"last_success": "previous"}})
                    c.save_json(c.FILTER_STATE_FILE, {"records": {}})
                    partial = [self.item(tags=["R-18G"]), self.item("200")]
                    failed = Mock(return_value=(partial, {"next": "advanced"},
                                                {"status": status, "message": "partial"}))
                    added = self.item("300", "asmhentai")
                    c.SOURCES["ehentai"] = failed
                    c.SOURCES["asmhentai"] = lambda state: (
                        [added], {"page": 2}, {"status": "ok"})
                    self.output.seek(0)
                    self.output.truncate(0)

                    self.assertEqual(c.main(), 0)

                    expected_state = old_state if isinstance(old_state, dict) else {}
                    failed.assert_called_once_with(expected_state)
                    saved = {row["uid"]: row for row in c.load_json(c.DATA_FILE, {})["items"]}
                    self.assertEqual(set(saved), {original["uid"], added["uid"]})
                    self.assertEqual(saved[original["uid"]]["tags"], original["tags"])
                    self.assertEqual(saved[added["uid"]]["title"], added["title"])
                    self.assertEqual(c.load_json(c.FILTER_STATE_FILE, {})["records"], {})
                    state = c.load_json(c.STATE_FILE, {})
                    self.assertEqual(state["ehentai"], expected_state)
                    self.assertEqual(state["asmhentai"], {"page": 2})
                    recorded = c.load_json(c.STATUS_FILE, {})["ehentai"]
                    self.assertEqual(recorded["status"], status)
                    self.assertEqual(recorded["message"], "partial")
                    self.assertEqual(recorded["last_success"], "previous")
                    self.assertEqual(recorded["last_attempt"], "2026-11-08T00:00:00+00:00")
                    self.assertEqual((recorded["accepted"], recorded["blocked"]), (0, 0))
                    self.assertEqual(self.output.getvalue().count(
                        f"[ehentai] status={status} のため結果を破棄"), 1)
