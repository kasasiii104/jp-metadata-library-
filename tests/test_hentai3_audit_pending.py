import unittest
from unittest.mock import Mock, patch

from sources import hentai3 as h


class AuditPendingTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(h.requests, "Session", return_value=Mock()))
        self.enterContext(patch.object(h.time, "sleep"))
        self.fetch = self.enterContext(patch.object(h, "_fetch_gallery_metadata"))
        self.items = [
            {"uid": f"3hentai:{n}", "source": "3hentai", "source_id": str(n),
             "source_url": f"https://3hentai.net/d/{n}"}
            for n in range(1, 5)
        ]
        self.success = ({"evidence_count": 1, "filter_evidence_count": 1, "tag_section_found": True, "content_tag_count": 1,
                         "tags": ["sample"]}, {"http_status": 200})

    def test_pending_includes_failed_and_unattempted_targets(self):
        self.fetch.side_effect = [self.success, (None, {"http_status": 200})]
        result = h.enrich_existing_for_filter(self.items, limit=2)
        self.assertEqual(result["existing_filter_audit_attempted"], 2)
        self.assertEqual(result["existing_filter_audit_failed"], 1)
        self.assertEqual(result["existing_filter_audit_pending"], 3)
        self.assertEqual([call.args[1] for call in self.fetch.call_args_list],
                         [item["source_url"] for item in self.items[:2]])

    def test_pending_is_zero_when_all_targets_are_checked(self):
        self.fetch.return_value = self.success
        result = h.enrich_existing_for_filter(self.items, limit=4)
        self.assertEqual(result["existing_filter_audit_pending"], 0)
        self.assertEqual(result["existing_filter_audit_failed"], 0)

    def test_pending_includes_enriched_but_unchecked_targets(self):
        self.fetch.return_value = ({"evidence_count": 1, "filter_evidence_count": 0,
                                    "artists": ["sample"]}, {"http_status": 200})
        result = h.enrich_existing_for_filter(self.items, limit=1)
        self.assertEqual(result["existing_filter_audit_enriched"], 1)
        self.assertEqual(result["existing_filter_audit_pending"], 4)

    def test_pending_includes_stopped_attempt(self):
        self.fetch.side_effect = [self.success, (None, {"http_status": 429})]
        result = h.enrich_existing_for_filter(self.items, limit=4)
        self.assertTrue(result["existing_filter_audit_stopped"])
        self.assertEqual(result["existing_filter_audit_attempted"], 2)
        self.assertEqual(result["existing_filter_audit_pending"], 3)

    def test_pending_excludes_previously_checked_and_other_sources(self):
        self.items[0]["filter_metadata_checked"] = "3hentai-gallery-v1"
        self.items[1]["source"] = "other"
        result = h.enrich_existing_for_filter(self.items, limit=0)
        self.assertEqual(result["existing_filter_audit_pending"], 2)
        self.fetch.assert_not_called()

    def test_reason_vocabulary_and_failure_samples(self):
        cases = [
            ((None, {"http_status": 200}), "gallery_no_evidence"),
            ((None, {"http_status": 404}), "gallery_http_404"),
            ((None, {"http_status": 401}), "gallery_http_401"),
            ((None, {"http_status": 0, "error": "timeout"}), "transport"),
            ((None, {"http_status": 200, "error": "timeout"}), "transport"),
            ((None, {"http_status": 500}), "stopped_http_500"),
            (self.success, "ok"),
        ]
        for response, reason in cases:
            with self.subTest(reason=reason, response=response):
                item = dict(self.items[0])
                self.fetch.return_value = response
                result = h.enrich_existing_for_filter([item], limit=1)
                self.assertEqual(result["existing_filter_audit_results"][item["uid"]]["reason"], reason)
                self.assertEqual(result["existing_filter_audit_reason_counts"], {reason: 1})
        item = dict(self.items[0], source_url="", source_id="g1")
        self.fetch.reset_mock()
        result = h.enrich_existing_for_filter([item], limit=1)
        self.fetch.assert_not_called()
        self.assertEqual(result["existing_filter_audit_failures"], ["g1: empty_url"])

    def test_search_without_numeric_internal_id_fails_closed(self):
        item = dict(self.items[0], source_id="g1", source_url="https://3hentai.net/search?q=Sample")
        with patch.object(h, "_resolve_public_search_exact") as search:
            result = h.enrich_existing_for_filter([item], limit=1)
            search.assert_not_called()
        self.assertEqual(result["existing_filter_audit_results"][item["uid"]]["reason"],
                         "repair_invalid_internal_id")
        self.fetch.assert_not_called()
