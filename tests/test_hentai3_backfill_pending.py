import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import crawler as c
from sources import hentai3 as h


def row(number):
    number = str(number)
    return {"uid": "3hentai:" + number, "source": "3hentai", "source_id": number,
            "title": "Sample " + number, "language": "japanese", "category": "Manga",
            "tags": ["sample"], "source_url": "https://3hentai.net/d/" + str(int(number) + 3000000)}


def metadata(tags=None):
    return {"evidence_count": 1, "filter_evidence_count": 1,
            "tags": tags or ["sample"]}, {"http_status": 200}


class BackfillTests(unittest.TestCase):
    def setUp(self):
        for name, value in {"HENTAI3_LATEST_PAGES": 2, "HENTAI3_BACKFILL_PAGES": 3,
                            "HENTAI3_MAX_GALLERIES_PER_RUN": 4,
                            "HENTAI3_GET_DEBUG_LIMIT": 0,
                            "HENTAI3_GALLERY_METADATA_LIMIT": 20}.items():
            self.enterContext(patch.object(h, name, value))
        self.enterContext(patch.object(h.requests, "Session", return_value=Mock(
            get=Mock(return_value=Mock(status_code=200)))))
        self.enterContext(patch.object(h.time, "sleep"))
        self.enterContext(patch.object(h, "_direct_search_cards", return_value=([], {})))
        self.enterContext(patch.object(h, "_find_candidate_dicts", side_effect=lambda p: p))
        self.enterContext(patch.object(h, "_normalize", side_effect=lambda obj, **kw: dict(obj)))
        self.fetch = self.enterContext(patch.object(h, "_fetch_gallery_metadata", return_value=metadata()))
        self.pages = []
        self.failure = None
        self.empty = False

        def search(session, path, params):
            page = params["page"]
            self.pages.append((params["key"], page))
            if page == self.failure:
                raise RuntimeError("API failure")
            return ([] if self.empty else [row(page * 10 + n) for n in range(2)]), "mock"
        self.enterContext(patch.object(h, "_get_json", side_effect=search))

    def test_latest_cap_still_ingests_whole_history_page(self):
        items, state, status = h.collect({"backfill_page": 10})
        self.assertEqual(len(items), 6)
        self.assertEqual({x["source_id"] for x in items}, {"10", "11", "20", "21", "100", "101"})
        self.assertEqual(state["backfill_page"], 11)
        self.assertEqual(status["backfill_pages_completed"], 1)
        self.assertEqual((status["backfill_page_before"], status["backfill_page_after"]), (10, 11))
        self.assertTrue(status["partial"])
        self.assertEqual(status["status"], "ok")

    def test_history_api_failure_stops_contiguous_progress(self):
        self.failure = 11
        with patch.object(h, "HENTAI3_MAX_GALLERIES_PER_RUN", 100):
            _, state, status = h.collect({"backfill_page": 10})
        self.assertEqual(state["backfill_page"], 11)
        self.assertEqual(status["backfill_pages_failed"], 1)
        self.assertEqual(status["backfill_pages_completed"], 1)
        self.assertNotIn((h.SEARCH_KEY, 12), self.pages)
        self.assertTrue(status["partial"])

    def test_cap_does_not_advance_unrequested_history_pages(self):
        _, state, _ = h.collect({"backfill_page": 10})
        self.assertEqual([p for _, p in self.pages], [1, 2, 10])
        self.assertEqual(state["backfill_page"], 11)

    def test_metadata_prioritizes_history_candidates(self):
        with patch.object(h, "HENTAI3_GALLERY_METADATA_LIMIT", 3):
            items, _, status = h.collect({"backfill_page": 10})
        self.assertEqual([call.args[1].rsplit("/", 1)[1] for call in self.fetch.call_args_list],
                         [row(n)["source_url"].rsplit("/", 1)[1] for n in (100, 101, 10)])
        self.assertTrue(status["partial"])
        self.assertEqual(sum(x.get("filter_metadata_checked") == "3hentai-gallery-v1" for x in items), 3)

    def test_fallback_does_not_complete_failed_history_page(self):
        self.failure = 10
        self.empty = True
        _, state, status = h.collect({"backfill_page": 10})
        self.assertEqual(state["backfill_page"], 10)
        self.assertEqual(status["backfill_pages_completed"], 0)

    def test_overlapping_history_pages_and_duplicate_candidates_complete(self):
        with patch.object(h, "HENTAI3_MAX_GALLERIES_PER_RUN", 100):
            items, state, status = h.collect({"backfill_page": 1})
        self.assertEqual(len(items), 6)
        self.assertEqual(state["backfill_page"], 4)
        self.assertEqual(status["backfill_pages_completed"], 3)
        self.assertFalse(status["partial"])


class PendingAuditTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(
            dir=Path(__file__).resolve().parents[1])))
        for name, value in {
            "DOCS_DIR": self.root / "docs", "DATA_FILE": self.root / "docs/data.json",
            "STATE_FILE": self.root / "docs/crawl_state.json",
            "STATUS_FILE": self.root / "docs/source_status.json",
            "FILTER_STATE_FILE": self.root / "state/filter_audit.json",
        }.items():
            self.enterContext(patch.object(c, name, value))
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.enterContext(patch.object(c, "now_iso", return_value="2026-11-09T00:00:00+00:00"))
        self.enterContext(patch.object(c, "HITOMI_FILTER_AUDIT_LIMIT", 0))
        self.enterContext(patch.dict(c.SOURCES, {"3hentai": lambda state: ([], state, {"status": "ok"})}, clear=True))
        self.enterContext(patch.object(h.requests.Session, "request", side_effect=AssertionError("Network forbidden")))
        self.enterContext(patch.object(h.time, "sleep"))
        self.enterContext(patch.object(h, "HENTAI3_EXISTING_FILTER_AUDIT_LIMIT", 1))
        self.fetch = self.enterContext(patch.object(h, "_fetch_gallery_metadata", return_value=metadata()))

    def seed(self, *items):
        store = {"records": {}}
        for item in items:
            c.quarantine_item(store, item, "metadata_unverified", "2026-11-08T00:00:00+00:00")
        c.save_json(c.FILTER_STATE_FILE, store)

    def collect_row(self, item):
        collector = Mock(return_value=([item], {}, {"status": "ok"}))
        self.enterContext(patch.dict(c.SOURCES, {"3hentai": collector}, clear=True))
        return collector

    def assert_retained_without_pending(self):
        self.assertEqual([x["uid"] for x in c.load_json(c.DATA_FILE, {})["items"]],
                         ["3hentai:100"])
        self.assertEqual(c.load_json(c.DATA_FILE, {})["items"][0]["filter_metadata_checked"],
                         "3hentai-gallery-v1")
        self.assertNotIn("3hentai:100", c.load_json(c.FILTER_STATE_FILE, {})["records"])

    def test_restored_pending_survives_checkpoint_and_all_collectors_failed(self):
        self.seed(row(100))
        hitomi_rows = [dict(uid=f"hitomi:{n}", source="hitomi", source_id=str(n),
                            title=f"Sample {n}", language="japanese", tags=["sample"])
                       for n in range(1000, 1025)]
        c.save_json(c.DATA_FILE, {"items": hitomi_rows})
        original_catalog = c.DATA_FILE.read_bytes()
        c.save_json(c.STATE_FILE, {"3hentai": {"page": 7}})
        original_state = c.STATE_FILE.read_bytes()
        def fetch(session, number, base):
            return dict(hitomi_rows[number - 1000],
                        filter_metadata_checked=c.hitomi.FILTER_METADATA_VERSION)
        with patch.object(c, "HITOMI_FILTER_AUDIT_LIMIT", 25), \
             patch.object(c.hitomi, "fetch_filter_metadata", side_effect=fetch) as audit, \
             patch.object(c, "polite_sleep"), \
             patch.dict(c.SOURCES, {"3hentai": lambda state: ([], state, {"status": "error"})}, clear=True):
            self.assertEqual(c.main(), 1)
        self.assertEqual(audit.call_count, 25)
        store = c.load_json(c.FILTER_STATE_FILE, {})
        self.assertIn("3hentai:100", store["verified_checkpoints"])
        self.assertNotIn("3hentai:100", store["records"])
        self.assertEqual(c.DATA_FILE.read_bytes(), original_catalog)
        self.assertEqual(c.STATE_FILE.read_bytes(), original_state)
        with patch.object(h, "enrich_existing_for_filter", return_value={}):
            self.assertEqual(c.main(), 0)
        self.assertIn("3hentai:100", {x["uid"] for x in c.load_json(c.DATA_FILE, {})["items"]})

    def test_restored_pending_survives_unverified_collector_row(self):
        self.seed(row(100))
        collector = self.collect_row(row(100))
        self.assertEqual(c.main(), 0)
        collector.assert_called_once()
        self.assert_retained_without_pending()
        self.assertEqual(c.load_json(c.STATUS_FILE, {})["3hentai"]
                         ["existing_filter_audit_pending_restored"], 1)

    def test_verified_catalog_survives_unverified_collector_row(self):
        c.save_json(c.DATA_FILE, {"items": [dict(row(100),
                    filter_metadata_checked="3hentai-gallery-v1")]})
        collector = self.collect_row(row(100))
        self.assertEqual(c.main(), 0)
        collector.assert_called_once()
        self.assert_retained_without_pending()
        self.fetch.assert_not_called()

    def test_verified_catalog_is_blocked_by_new_collector_tags(self):
        c.save_json(c.DATA_FILE, {"items": [dict(row(100),
                    filter_metadata_checked="3hentai-gallery-v1")]})
        collector = self.collect_row(dict(row(100), tags=["guro"]))
        self.assertEqual(c.main(), 0)
        collector.assert_called_once()
        self.assertEqual(c.load_json(c.DATA_FILE, {})["items"], [])
        record = c.load_json(c.FILTER_STATE_FILE, {})["records"]["3hentai:100"]
        self.assertEqual(record["status"], "blocked")
        self.assertEqual(record["reason"], "blocked:guro")

    def test_new_unverified_collector_row_remains_pending(self):
        collector = self.collect_row(row(100))
        self.assertEqual(c.main(), 0)
        collector.assert_called_once()
        self.assertEqual(c.load_json(c.DATA_FILE, {})["items"], [])
        record = c.load_json(c.FILTER_STATE_FILE, {})["records"]["3hentai:100"]
        self.assertEqual(record["status"], "pending")
        self.assertEqual(record["reason"], "metadata_unverified")

    def test_pending_row_is_restored_to_catalog(self):
        self.seed(row(100))
        self.assertEqual(c.main(), 0)
        items = c.load_json(c.DATA_FILE, {})["items"]
        self.assertEqual([x["uid"] for x in items], ["3hentai:100"])
        self.assertEqual(items[0]["filter_metadata_checked"], "3hentai-gallery-v1")
        store = c.load_json(c.FILTER_STATE_FILE, {})
        self.assertEqual(store["records"], {})
        self.assertEqual(store["hentai3_attempts"], {})
        status = c.load_json(c.STATUS_FILE, {})
        audit = status["3hentai"]
        self.assertNotIn("existing_filter_audit_results", audit)
        self.assertNotIn("existing_filter_audit_results", status["filters"]["3hentai_existing_audit"])
        self.assertEqual(audit["existing_filter_audit_pending_restored"], 1)
        self.assertEqual(audit["existing_filter_audit_pending_targets"], 1)

    def test_pending_row_with_block_reason_becomes_blocked(self):
        self.seed(row(100))
        self.fetch.return_value = metadata(["R-18G"])
        self.assertEqual(c.main(), 0)
        self.assertEqual(c.load_json(c.DATA_FILE, {})["items"], [])
        self.assertEqual(c.load_json(c.FILTER_STATE_FILE, {})["records"]["3hentai:100"]["status"], "blocked")
        self.assertEqual(c.load_json(c.STATUS_FILE, {})["3hentai"]["existing_filter_audit_pending_blocked"], 1)

    def test_failed_pending_row_rotates_behind_unattempted_existing_row(self):
        self.seed(row(100))
        self.fetch.return_value = (None, {"http_status": 429})
        self.assertEqual(c.main(), 0)
        store = c.load_json(c.FILTER_STATE_FILE, {})
        self.assertEqual(store["hentai3_attempts"]["3hentai:100"]["failures"], 1)
        self.assertEqual(store["records"]["3hentai:100"]["status"], "pending")
        status = c.load_json(c.STATUS_FILE, {})
        self.assertNotIn("existing_filter_audit_results", status["3hentai"])
        self.assertNotIn("existing_filter_audit_results", status["filters"]["3hentai_existing_audit"])
        c.save_json(c.DATA_FILE, {"items": [row(200)]})
        self.fetch.reset_mock()
        self.fetch.return_value = metadata()
        self.assertEqual(c.main(), 0)
        self.assertEqual(self.fetch.call_args.args[1], row(200)["source_url"])

    def test_audit_only_skips_hentai3(self):
        self.seed(row(100))
        self.assertEqual(c.main(audit_only=True), 0)
        self.fetch.assert_not_called()
        self.assertEqual(c.load_json(c.FILTER_STATE_FILE, {})["records"]["3hentai:100"]["status"], "pending")


if __name__ == "__main__":
    unittest.main()
