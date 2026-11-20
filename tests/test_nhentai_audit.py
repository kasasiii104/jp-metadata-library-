import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import crawler as c


class NhentaiAuditTests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        docs = root / "docs"
        self.enterContext(patch.multiple(
            c, DOCS_DIR=docs, DATA_FILE=docs / "data.json",
            STATE_FILE=docs / "crawl_state.json", STATUS_FILE=docs / "source_status.json",
            FILTER_STATE_FILE=root / "state/filter_audit.json", HITOMI_FILTER_AUDIT_LIMIT=0,
        ))
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.enterContext(patch.object(c.hentai3, "enrich_existing_for_filter", return_value={}))
        self.enterContext(patch("requests.adapters.HTTPAdapter.send", side_effect=AssertionError("Network forbidden")))
        self.enterContext(patch.dict(c.SOURCES, {
            "hitomi": lambda state: ([], state, {"status": "ok"}),
        }, clear=True))

    def item(self, gid):
        return {"uid": f"nhentai:{gid}", "source": "nhentai", "source_id": gid,
                "title": "サンプル", "language": "japanese", "tags": []}

    def save_items(self, items):
        c.save_json(c.DATA_FILE, {"items": items})

    def saved(self):
        return c.load_json(c.DATA_FILE, {})["items"]

    def state(self):
        return c.load_json(c.FILTER_STATE_FILE, {})

    def test_nhentai_saved_tags_audit_limit_purge_and_confirmed_empty(self):
        items = [self.item(gid)
                 for gid in ("9000001", "9000002", "9000003")]
        self.save_items(items)
        with patch.object(c, "NH_EXISTING_TAG_AUDIT_LIMIT", 2), patch.object(
            c.nhentai, "_detail", side_effect=[
                {"id": 9000001, "tags": [{"type": "tag", "name": "shotacon"}]},
                {"id": 9000002, "tags": [{"type": "language", "name": "japanese"}]},
                {"id": 9000003, "tags": [{"type": "tag", "name": "sample"}]},
            ],
        ) as detail, patch.object(c.nhentai, "polite_sleep"):
            self.assertEqual(c.main(audit_only=True), 0)
            detail.assert_not_called()
            self.assertEqual(c.main(), 0)
            status = c.load_json(c.STATUS_FILE, {})["nhentai"]["existing_tag_audit"]
            self.assertEqual([status[key] for key in ("attempted", "tagged", "excluded", "confirmed_empty")],
                             [2, 1, 1, 1])
            self.assertEqual(self.state()["records"]["nhentai:9000001"]["reason"], "blocked:shotacon")
            self.assertEqual(c.main(), 0)
            self.assertEqual(c.main(), 0)
        self.assertEqual([call.args[1] for call in detail.call_args_list], ["9000001", "9000002", "9000003"])
        saved = {item["uid"]: item for item in self.saved()}
        self.assertEqual(saved["nhentai:9000002"]["filter_metadata_checked"], c.NH_TAG_AUDIT_VERSION)
        self.assertEqual(saved["nhentai:9000003"]["tags"], ["sample"])

    def test_nhentai_audit_stops_source_on_access_denial(self):
        self.save_items([self.item(gid)
                         for gid in ("9000001", "9000002")])
        for error in (c.URLPolicyError("denied"), c.nhentai.HTTPError(403), c.nhentai.HTTPError(429)):
            with self.subTest(error=error), patch.dict(c.SOURCES, {"nhentai": Mock()}) as sources, patch.object(
                c.nhentai, "_detail", side_effect=error,
            ) as detail:
                self.assertEqual(c.main(), 0)
                detail.assert_called_once()
                sources["nhentai"].assert_not_called()
                status = c.load_json(c.STATUS_FILE, {})["nhentai"]["existing_tag_audit"]
                self.assertTrue(status["stopped"])
                self.assertEqual(len(self.saved()), 2)

    def test_audit_and_collect_share_detail_budget_and_skip_attempted_ids(self):
        self.save_items([self.item("9000001"), self.item("9000002")])
        with patch.dict(c.SOURCES, {"nhentai": c.nhentai.collect}, clear=True), patch.multiple(
            c.nhentai, NH_MAX_DETAILS_PER_RUN=3, NH_INITIAL_BACKFILL_ITEMS=0,
        ), patch.object(c.nhentai, "_search_page", return_value=[
            {"id": gid} for gid in range(9000004, 9000000, -1)
        ]), patch.object(c.nhentai, "polite_sleep"), patch.object(
            c.nhentai, "_detail", side_effect=[
                {"id": 9000001, "tags": [{"type": "tag", "name": "sample"}]},
                c.nhentai.HTTPError(500),
                {"id": 9000003, "tags": [{"type": "language", "name": "japanese"}]},
            ],
        ) as detail:
            self.assertEqual(c.main(), 0)
        self.assertEqual([call.args[1] for call in detail.call_args_list],
                         ["9000001", "9000002", "9000003"])
        self.assertEqual(c.load_json(c.STATE_FILE, {})["nhentai"]["latest_id"], 9000003)
        saved = {item["uid"]: item for item in self.saved()}
        self.assertEqual(saved["nhentai:9000001"]["tags"], ["sample"])
        self.assertEqual(saved["nhentai:9000002"]["tags"], [])
        self.assertIn("nhentai:9000003", saved)
        self.assertNotIn("nhentai:9000004", saved)

    def test_audit_and_collect_share_deadline(self):
        self.save_items([self.item("9000001")])
        with patch.dict(c.SOURCES, {"nhentai": c.nhentai.collect}, clear=True), patch.object(
            c.nhentai.time, "monotonic", side_effect=[0, 0, c.nhentai.NH_RUN_DEADLINE_SEC],
        ), patch.object(c.nhentai, "_search_page") as search, patch.object(
            c.nhentai, "_detail", return_value={"id": 9000001, "tags": []},
        ) as detail:
            self.assertEqual(c.main(), 0)
        detail.assert_called_once()
        search.assert_not_called()
        status = c.load_json(c.STATUS_FILE, {})["nhentai"]
        self.assertTrue(status["deadline_reached"])
