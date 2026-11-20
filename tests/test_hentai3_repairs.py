import copy
import json
from pathlib import Path
import unittest
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock, patch

from net_test_helpers import recording_session
from sources import hentai3 as h
import crawler as c
import test_hentai3_backfill_pending as base


def observation(status=400, link="", ident=""):
    return dict(http_status=status, attempted=True, link=link, id_value=ident)


class RepairTests(unittest.TestCase):
    def setUp(self):
        self.item = dict(uid="3hentai:3000123", source="3hentai", source_id="3000123",
                         title="Sample", source_url="", language="japanese", tags=["language:japanese"])
        self.meta = h._gallery_metadata_from_html(
            '<div id="main-info"><h1>Sample</h1><div class="tag-container">'
            '<a href="/tag/sample">sample</a></div></div>', 'https://3hentai.net/d/123')
        self.fetch = self.enterContext(patch.object(h, "_fetch_gallery_metadata", return_value=(self.meta, {"http_status": 200})))
        self.enterContext(patch.object(h.time, "sleep"))
        self.enterContext(patch('requests.adapters.HTTPAdapter.send', side_effect=AssertionError("No network")))
        self.repairs = {}

    def run_repair(self, observations, **kwargs):
        with patch.object(h._RepairGetBudget, "get", side_effect=observations) as get:
            status = h.enrich_existing_for_filter([self.item], repairs=self.repairs, **kwargs)
        return status, get

    def test_payload_link_and_detail_id(self):
        for evidence, observations in [("payload_link", [observation(200, "https://3hentai.net/d/123")]),
                                       ("detail_id", [observation(), observation(200, ident="3000123")])]:
            with self.subTest(evidence=evidence):
                self.item.pop("filter_metadata_checked", None)
                self.item["source_url"] = ""
                status, _ = self.run_repair(observations)
                self.assertEqual(self.item["repair_evidence"], evidence)
                self.assertEqual(self.item["source_url"], "https://3hentai.net/d/123")
                self.assertEqual(self.item["filter_metadata_checked"], "3hentai-gallery-v1")
                self.assertEqual("public_gallery_id" in self.item, evidence == "payload_link")
                self.assertNotIn("resolved_gallery_id", self.item)
                self.assertEqual(status["existing_filter_audit_results"][self.item["uid"]]["reason"], "ok")

    def test_uncorroborated_and_conflict_leave_item_unchanged(self):
        for observations, conclusion in [([observation(), observation()], "repair_uncorroborated"),
            ([observation(), observation(200, ident="123")], "repair_uncorroborated"),
            ([observation(200, "https://3hentai.net/d/999")], "repair_payload_link_conflict")]:
            with self.subTest(conclusion=conclusion):
                original = copy.deepcopy(self.item)
                self.run_repair(observations)
                self.assertEqual(self.item, original)
                self.assertEqual(self.repairs[self.item["uid"]]["conclusion"], conclusion)

    def test_title_failure_claim_and_404_do_not_call_get(self):
        for changes, kwargs, conclusion in [({"page_title": "Different"}, {}, "repair_title_mismatch"),
            ({"page_title": ""}, {}, "repair_no_page_title"),
            ({}, {"claimed_gallery_ids": {"123": {"other"}}}, "repair_candidate_claimed")]:
            with self.subTest(conclusion=conclusion):
                self.fetch.return_value = (dict(self.meta, **changes), {"http_status": 200})
                _, get = self.run_repair([], **kwargs)
                get.assert_not_called()
                self.assertEqual(self.repairs[self.item["uid"]]["conclusion"], conclusion)
        self.fetch.return_value = (None, {"http_status": 404})
        self.run_repair([])
        self.assertEqual(self.repairs[self.item["uid"]]["conclusion"], "repair_candidate_http_404")

    def test_get_limit_resume_transport_and_429(self):
        session, transport = recording_session()
        transport.return_value = Mock(raw=None, status_code=400)
        with patch.object(h.requests, "Session", return_value=session), patch.object(h, "HENTAI3_REPAIR_GET_LIMIT", 1):
            for expected in ("repair_corroboration_deferred", "repair_uncorroborated"):
                h.enrich_existing_for_filter([self.item], repairs=self.repairs)
                self.assertEqual(self.repairs[self.item["uid"]]["conclusion"], expected)
            self.assertEqual(transport.call_count, 2)
            self.assertEqual(parse_qs(urlsplit(transport.call_args.args[0].url).query), {"book": ["123"]})
        for status in (0, 429):
            self.repairs.clear()
            transport.side_effect = ConnectionError() if status == 0 else None
            transport.return_value = Mock(raw=None, status_code=status)
            with patch.object(h.requests, "Session", return_value=session), patch.object(h, "HENTAI3_REPAIR_GET_LIMIT", 10):
                result = h.enrich_existing_for_filter([self.item], repairs=self.repairs)
            self.assertEqual(self.repairs[self.item["uid"]]["conclusion"], "repair_corroboration_deferred")
            self.assertEqual(result["repair_get_stopped"], status == 429)

    def test_budget_reads_raw_payload_first_id_and_stops_shared_run(self):
        session, transport = recording_session()
        transport.side_effect = [
            Mock(raw=None, status_code=200, json=Mock(return_value={"data": {"id": "123", "gallery_id": "3000123", "url": "/d/123"}})),
            Mock(raw=None, status_code=429)]
        with patch.object(h, "HENTAI3_REPAIR_GET_LIMIT", 10):
            budget = h._RepairGetBudget(session)
            observed = budget.get("3000123")
            self.assertEqual(observed["id_value"], "3000123")
            self.assertEqual(observed["link"], "https://3hentai.net/d/123")
            self.assertEqual(budget.get("123")["http_status"], 429)
            self.assertFalse(budget.get("456")["attempted"])
            self.assertEqual(transport.call_count, 2)
        with patch.object(h, "HENTAI3_REPAIR_GET_LIMIT", 0):
            budget = h._RepairGetBudget(session)
            self.assertFalse(budget.get("123")["attempted"])

    def test_candidate_stop_and_zero_get_budget_leave_item_unchanged(self):
        original = copy.deepcopy(self.item)
        for status in (403, 429, 500, 503):
            self.fetch.return_value = (None, {"http_status": status})
            result, get = self.run_repair([])
            self.assertTrue(result["existing_filter_audit_stopped"])
            self.assertEqual(self.item, original)
            get.assert_not_called()
        self.fetch.return_value = (self.meta, {"http_status": 200})
        with patch.object(h, "HENTAI3_REPAIR_GET_LIMIT", 0):
            result = h.enrich_existing_for_filter([self.item], repairs=self.repairs)
        self.assertEqual(self.repairs[self.item["uid"]]["conclusion"], "repair_corroboration_deferred")
        self.assertEqual(self.item, original)

    def test_stale_checked_repair_requires_current_content_tags(self):
        for html, reason in [('<div id="main-info"><h1>Sample</h1></div>', "gallery_no_tag_section"),
                             ('<div id="main-info"><h1>Sample</h1><div class="tag-container">'
                              '<a href="/language/japanese">japanese</a></div></div>', "gallery_no_tags")]:
            with self.subTest(reason=reason):
                self.item.update(source_url="https://3hentai.net/search?q=Sample",
                                 filter_metadata_checked="3hentai-gallery-v1", filter_metadata_checked_at="old")
                self.fetch.return_value = (h._gallery_metadata_from_html(html, ""), {"http_status": 200})
                result, _ = self.run_repair([observation(200, "https://3hentai.net/d/123")])
                self.assertEqual(result["existing_filter_audit_results"][self.item["uid"]]["reason"], reason)
                self.assertEqual(self.item["source_url"], "https://3hentai.net/d/123")
                self.assertNotIn("filter_metadata_checked", self.item)
                self.assertNotIn("filter_metadata_checked_at", self.item)

    def test_existing_audit_drops_stale_checked_on_tagless_page(self):
        self.item.update(source_url="https://3hentai.net/d/123",
                         filter_metadata_checked="3hentai-gallery-v1", filter_metadata_checked_at="old")
        # A pending direct row is audited even if it arrived with an old marker.
        self.fetch.return_value = (h._gallery_metadata_from_html('<div id="main-info"><h1>Sample</h1></div>', ""),
                                   {"http_status": 200})
        h._merge_gallery_metadata(self.item, self.fetch.return_value[0])
        self.assertNotIn("filter_metadata_checked", self.item)
        self.assertNotIn("filter_metadata_checked_at", self.item)

    def test_fixture_titles_come_from_main_info_h1(self):
        from bs4 import BeautifulSoup
        for path in (Path(__file__).parent / "fixtures/3hentai").glob("*.html"):
            html = path.read_text()
            title = BeautifulSoup(html, "html.parser").select_one("#main-info h1").get_text(" ", strip=True)
            self.assertEqual(h._gallery_metadata_from_html(html, "")["page_title"], h._clean(title))

    def test_repair_evidence_survives_clean_and_checkpoint_merge(self):
        repaired = dict(self.item, source_url="https://3hentai.net/d/123", repair_evidence="detail_id",
                        repair_evidence_at="2026-11-12T00:00:00+00:00")
        old = dict(self.item, resolved_gallery_id="999", public_gallery_id="999")
        for merged in (c.clean_item(repaired, "stamp"), c.merge_item(old, repaired, "stamp")):
            self.assertEqual(merged["repair_evidence"], "detail_id")
            self.assertEqual(merged["repair_evidence_at"], repaired["repair_evidence_at"])
            self.assertNotIn("public_gallery_id", merged)
            self.assertNotIn("resolved_gallery_id", merged)

    def test_split_detection_is_pure_and_unknown_group_untouched(self):
        owner = dict(self.item, source_url="https://3hentai.net/d/123")
        excess = dict(owner, uid="3hentai:3000124", source_id="3000124")
        unknown = [dict(owner, uid="3hentai:9", source_id="9", source_url="https://3hentai.net/d/12"),
                   dict(owner, uid="3hentai:8", source_id="8", source_url="https://3hentai.net/d/12")]
        rows = [owner, excess] + unknown
        original = copy.deepcopy(rows)
        self.assertEqual(h.split_mislinks(rows), ({excess["uid"]}, 1))
        self.assertEqual(rows, original)
        excess.pop("source_url")
        self.assertEqual(h.split_mislinks(rows), (set(), 1))


class RepairIntegrationTests(unittest.TestCase):
    setUp = base.PendingAuditTests.setUp
    legacy_row = base.PendingAuditTests.legacy_row
    seed = base.PendingAuditTests.seed

    def test_old_pending_restore_without_new_hold_fields(self):
        for url in ("", "https://3hentai.net/search?q=Sample", "https://3hentai.net/d/123"):
            for detail in (None, "3hentai_unaudited"):
                for entry in ("checkpoint", "collect"):
                    for evidence in (None, "detail_id"):
                        with self.subTest(url=url, detail=detail, entry=entry, evidence=evidence):
                            item = self.legacy_row(3000123, url)
                            saved = dict(item, source_url="https://3hentai.net/d/123", tags=["sample"],
                                         filter_metadata_checked="3hentai-gallery-v1")
                            if evidence:
                                saved["repair_evidence"] = evidence
                            rec = {"status": "pending", "reason": "metadata_unverified", "item": item}
                            if detail is not None:
                                rec["hold_detail"] = detail
                            # Both legacy layouts omit hold_source_url; the oldest also omits hold_detail.
                            expected = bool(h.direct_gallery_id(url)) or bool(evidence)
                            c.save_json(c.DATA_FILE, {"items": []})
                            c.save_json(c.FILTER_STATE_FILE, {"records": {item["uid"]: rec},
                                "verified_checkpoints": {item["uid"]: saved} if entry == "checkpoint" else {}})
                            with patch.object(h, "HENTAI3_EXISTING_FILTER_AUDIT_LIMIT", 0), patch.dict(c.SOURCES, {
                                    "3hentai": lambda state: ([saved] if entry == "collect" else [], state, {"status": "ok"})}, clear=True):
                                self.assertEqual(c.main(), 0)
                            public = c.load_json(c.DATA_FILE, {})["items"]
                            self.assertEqual([row["uid"] for row in public], [item["uid"]] if expected else [])
                            self.assertEqual(c.hentai3_can_restore(saved, rec), expected)
                            if not expected:
                                record = c.load_json(c.FILTER_STATE_FILE, {})["records"][item["uid"]]
                                self.assertEqual(record["status"], "pending")
                                self.assertEqual(record["hold_source_url"], url)
                                record["item"].update(saved)
                                self.assertFalse(c.hentai3_can_restore(saved, record))

    def test_checkpoint_and_collect_require_hold_time_repair_evidence(self):
        for kind in ("legacy", "split", "direct"):
            for entry in ("checkpoint", "collect"):
                for evidence in ("", "detail_id", "payload_link"):
                    with self.subTest(kind=kind, entry=entry, evidence=evidence):
                        item = self.legacy_row(3000123, "https://3hentai.net/d/123" if kind == "direct" else "")
                        saved = dict(item, source_url="https://3hentai.net/d/123", tags=["sample"],
                                     filter_metadata_checked="3hentai-gallery-v1")
                        if evidence:
                            saved["repair_evidence"] = evidence
                        rec = {"status": "pending", "reason": "metadata_unverified", "item": item,
                               "hold_detail": "3hentai_mislinked_split" if kind == "split" else "3hentai_unaudited"}
                        c.save_json(c.DATA_FILE, {"items": []})
                        c.save_json(c.FILTER_STATE_FILE, {"records": {item["uid"]: rec},
                            "verified_checkpoints": {item["uid"]: saved} if entry == "checkpoint" else {}})
                        with patch.object(h, "HENTAI3_EXISTING_FILTER_AUDIT_LIMIT", 0), patch.dict(c.SOURCES, {
                                "3hentai": lambda state: ([saved] if entry == "collect" else [], state, {"status": "ok"})}, clear=True):
                            self.assertEqual(c.main(), 0)
                        public = c.load_json(c.DATA_FILE, {})["items"]
                        self.assertEqual(bool(public), kind == "direct" or bool(evidence))
                        if not public:
                            record = c.load_json(c.FILTER_STATE_FILE, {})["records"][item["uid"]]
                            # Mutating the stored item must not erase the original hold shape.
                            record["item"].update(saved)
                            self.assertFalse(c.hentai3_can_restore(saved, record))

    def test_stale_checked_repair_tagless_stays_pending_and_preserves_marker(self):
        item = dict(self.legacy_row(3000123, "https://3hentai.net/search?q=Sample"),
                    filter_metadata_checked="3hentai-gallery-v1", filter_metadata_checked_at="old")
        self.seed(item)
        meta = h._gallery_metadata_from_html('<div id="main-info"><h1>' + item["title"] + '</h1></div>', "")
        self.fetch.return_value = (meta, {"http_status": 200})
        with patch.object(h._RepairGetBudget, "get", return_value=observation(200, "https://3hentai.net/d/123")):
            self.assertEqual(c.main(), 0)
        rec = c.load_json(c.FILTER_STATE_FILE, {})["records"][item["uid"]]
        self.assertEqual(rec["status"], "pending")
        self.assertEqual(rec["repair_original"]["filter_metadata_checked_at"], "old")
        self.assertEqual(rec["item"]["source_url"], "https://3hentai.net/d/123")
        self.assertFalse(rec["item"].get("filter_metadata_checked"))
        self.assertEqual(c.load_json(c.DATA_FILE, {})["items"], [])

    def test_candidate_http_retry_and_terminal_404(self):
        for status in (401, 408, 500, 404):
            with self.subTest(status=status):
                item = self.legacy_row(3000123)
                c.save_json(c.DATA_FILE, {"items": []})
                c.save_json(c.FILTER_STATE_FILE, {})
                self.seed(item)
                self.fetch.return_value = (None, {"http_status": status})
                for _ in range(2):
                    self.assertEqual(c.main(), 0)
                store = c.load_json(c.FILTER_STATE_FILE, {})
                self.assertEqual(bool(store["hentai3_attempts"][item["uid"]].get("gave_up")), status == 404)
                self.assertEqual(store["hentai3_repairs"][item["uid"]]["attempts"], 1 if status == 404 else 2)

    def test_candidate_claimed_resumes_after_owner_disappears(self):
        item = self.legacy_row(3000123)
        # A separate UID with the same internal ID owns the offset candidate.
        owner = dict(item, uid="3hentai:owner", source_url="https://3hentai.net/d/123",
                     filter_metadata_checked="3hentai-gallery-v1")
        self.seed(item)
        c.save_json(c.DATA_FILE, {"items": [owner]})
        self.assertEqual(c.main(), 0)
        store = c.load_json(c.FILTER_STATE_FILE, {})
        self.assertEqual(store["hentai3_attempts"][item["uid"]]["gave_up_reason"], "repair_candidate_claimed")
        c.save_json(c.DATA_FILE, {"items": []})
        meta, info = base.metadata()
        meta["page_title"] = item["title"]
        self.fetch.return_value = (meta, info)
        with patch.object(h._RepairGetBudget, "get", return_value=observation(200, "https://3hentai.net/d/123")):
            self.assertEqual(c.main(), 0)
        self.assertEqual([row["uid"] for row in c.load_json(c.DATA_FILE, {})["items"]], [item["uid"]])

    def test_restore_both_evidence_paths_and_blocked_tags(self):
        for evidence, tags in [("payload_link", ["sample"]), ("detail_id", ["sample"]), ("payload_link", ["guro"])]:
            with self.subTest(evidence=evidence, tags=tags):
                item = self.legacy_row(3000123)
                self.seed(item)
                meta, info = base.metadata(tags)
                meta["page_title"] = item["title"]
                self.fetch.return_value = (meta, info)
                observations = [observation(200, "https://3hentai.net/d/123")] if evidence == "payload_link" else [observation(), observation(200, ident="3000123")]
                with patch.object(h._RepairGetBudget, "get", side_effect=observations):
                    self.assertEqual(c.main(), 0)
                store = c.load_json(c.FILTER_STATE_FILE, {}, c.validate_filter_store)
                self.assertEqual(store["hentai3_repairs"][item["uid"]]["evidence"], evidence)
                public = c.load_json(c.DATA_FILE, {})["items"]
                if tags == ["guro"]:
                    self.assertEqual(public, [])
                    self.assertEqual(store["records"][item["uid"]]["status"], "blocked")
                else:
                    self.assertEqual(public[0]["uid"], item["uid"])
                # Isolate the next subcase, including verified checkpoints.
                c.save_json(c.FILTER_STATE_FILE, {})
                c.save_json(c.DATA_FILE, {"items": []})

    def test_uncorroborated_gives_up_records_and_search_cannot_bypass(self):
        item = self.legacy_row(3000123, "https://3hentai.net/search?q=Sample")
        self.seed(item)
        meta, info = base.metadata()
        meta["page_title"] = item["title"]
        self.fetch.return_value = (meta, info)
        with patch.object(h._RepairGetBudget, "get", side_effect=[observation(), observation()]), patch.object(h, "_resolve_public_search_exact") as search:
            self.assertEqual(c.main(), 0)
            search.assert_not_called()
        store = c.load_json(c.FILTER_STATE_FILE, {})
        self.assertTrue(store["hentai3_attempts"][item["uid"]]["gave_up"])
        self.assertEqual(store["records"][item["uid"]]["item"]["source_url"], item["source_url"])
        incoming = dict(item, source_url="https://3hentai.net/d/123", resolved_gallery_id="123",
                        filter_metadata_checked="3hentai-gallery-v1", tags=["sample"])
        with patch.dict(c.SOURCES, {"3hentai": lambda state: ([incoming], state, {"status": "ok"})}, clear=True):
            self.assertEqual(c.main(), 0)
        self.assertEqual(c.load_json(c.DATA_FILE, {})["items"], [])

    def test_mislink_quarantine_original_kind_normalization_and_idempotence(self):
        owner = dict(self.legacy_row(3000123, "https://3hentai.net/d/123"), filter_metadata_checked="3hentai-gallery-v1")
        excess = dict(owner, uid="3hentai:3000124", source_id="3000124", thumbnail="wrong.webp", tags=["language:japanese", "wrong"], resolved_gallery_id="123")
        c.save_json(c.DATA_FILE, {"items": [owner, excess]})
        with patch.object(h, "HENTAI3_EXISTING_FILTER_AUDIT_LIMIT", 0):
            for n in range(2):
                self.assertEqual(c.main(), 0)
                public = c.load_json(c.DATA_FILE, {})["items"]
                self.assertEqual([x["uid"] for x in public], [owner["uid"]])
                self.assertEqual(public[0]["source_url_kind"], "direct")
                store = c.load_json(c.FILTER_STATE_FILE, {}, c.validate_filter_store)
                rec = store["records"][excess["uid"]]
                self.assertEqual(rec["repair_original"]["thumbnail"], "wrong.webp")
                self.assertEqual(rec["hold_detail"], "3hentai_mislinked_split")
                self.assertEqual(rec["item"]["tags"], ["language:japanese"])
                self.assertNotIn("source_url", rec["item"])
                self.assertNotIn("filter_metadata_checked", rec["item"])
                self.assertEqual(c.load_json(c.STATUS_FILE, {})["3hentai"]["mislinked_split_held"], 1 if n == 0 else 0)

    def test_deferred_repair_is_not_gave_up_and_checked_search_stays_pending(self):
        item = dict(self.legacy_row(3000123, "https://3hentai.net/search?q=Sample"),
                    filter_metadata_checked="3hentai-gallery-v1")
        # Seed a pending record directly: its old checked marker is not corroboration.
        c.save_json(c.FILTER_STATE_FILE, {"records": {item["uid"]: {
            "item": item, "status": "pending", "reason": "metadata_unverified"}}})
        meta, info = base.metadata()
        meta["page_title"] = item["title"]
        self.fetch.return_value = (meta, info)
        with patch.object(h, "HENTAI3_REPAIR_GET_LIMIT", 0):
            for _ in range(2):
                self.assertEqual(c.main(), 0)
        store = c.load_json(c.FILTER_STATE_FILE, {})
        self.assertFalse(store["hentai3_attempts"][item["uid"]].get("gave_up", False))
        self.assertEqual(store["hentai3_repairs"][item["uid"]]["attempts"], 2)
        self.assertEqual(store["records"][item["uid"]]["status"], "pending")
        self.assertEqual(c.load_json(c.DATA_FILE, {})["items"], [])

    def test_all_collectors_failed_preserves_repair_hold_and_crawl_state(self):
        item = self.legacy_row(3000123)
        other = dict(uid="ehentai:123", source="ehentai", source_id="123",
                     language="japanese", title="Sample", tags=["guro"])
        owner = dict(self.legacy_row(3000456, "https://3hentai.net/d/456"),
                     filter_metadata_checked="3hentai-gallery-v1")
        excess = dict(owner, uid="3hentai:3000457", source_id="3000457")
        c.save_json(c.DATA_FILE, {"items": [item, other, owner, excess]})
        c.save_json(c.STATE_FILE, {"3hentai": {"backfill_page": 7}})
        c.save_json(c.FILTER_STATE_FILE, {"records": {}})
        c.save_json(c.STATUS_FILE, {"3hentai": {"last_success": "old"}})
        paths = [c.DATA_FILE, c.STATE_FILE, c.FILTER_STATE_FILE, c.STATUS_FILE]
        before = {path: path.read_bytes() for path in paths}
        meta, info = base.metadata()
        meta["page_title"] = item["title"]
        self.fetch.return_value = (meta, info)
        with patch.object(h, "HENTAI3_REPAIR_GET_LIMIT", 0), patch.dict(c.SOURCES, {
                "3hentai": lambda state: ([], {"backfill_page": 99}, {"status": "error"})}, clear=True):
            self.assertEqual(c.main(), 1)
        self.assertEqual({path: path.read_bytes() for path in paths}, before)

    def test_title_matched_collection_cannot_overwrite_repaired_url(self):
        item = self.legacy_row(3000123)
        self.seed(item)
        meta, info = base.metadata()
        meta["page_title"] = item["title"]
        self.fetch.return_value = (meta, info)
        incoming = dict(item, source_url="https://3hentai.net/d/999", resolved_gallery_id="999",
                        filter_metadata_checked="3hentai-gallery-v1", tags=["guro"])
        with patch.object(h._RepairGetBudget, "get", side_effect=[observation(), observation(200, ident="3000123")]), patch.dict(c.SOURCES, {
                "3hentai": lambda state: ([incoming], state, {"status": "ok"})}, clear=True):
            self.assertEqual(c.main(), 0)
        public = c.load_json(c.DATA_FILE, {})["items"]
        self.assertEqual(len(public), 1)
        self.assertEqual(public[0]["source_url"], "https://3hentai.net/d/123")
        self.assertEqual(public[0]["repair_evidence"], "detail_id")
        self.assertNotIn("resolved_gallery_id", public[0])
        self.assertNotIn("public_gallery_id", public[0])
        self.assertNotIn("guro", public[0]["tags"])

    def test_real_catalog_dry_run_255_legacy_and_58_excess(self):
        # The supplied snapshot is read only; all crawler writes use setUp's temp directory.
        root = Path(__file__).resolve().parents[3]
        snapshot = root / "tasks/jp-3hentai-legacy-rows/scan-data"
        if not (snapshot / "data.json").exists():
            self.skipTest("HQ snapshot is not shipped with the repository")
        rows = json.loads((snapshot / "data.json").read_text())["items"]
        rows = [row for row in rows if row.get("source") == "3hentai"]
        excess, unresolved = h.split_mislinks(rows)
        self.assertEqual((len(excess), unresolved), (58, 0))
        legacy = {row["uid"] for row in rows if h.repair_target(row)}
        self.assertEqual(len(legacy), 255)
        c.save_json(c.DATA_FILE, {"items": rows})
        c.save_json(c.FILTER_STATE_FILE, json.loads((snapshot / "filter_audit.json").read_text()))
        with patch.object(h, "HENTAI3_EXISTING_FILTER_AUDIT_LIMIT", 0):
            self.assertEqual(c.main(), 0)
        store = c.load_json(c.FILTER_STATE_FILE, {}, c.validate_filter_store)
        public = {row["uid"] for row in c.load_json(c.DATA_FILE, {})["items"]}
        # Content exclusions and identity holds can both affect this snapshot.
        held = legacy | excess
        self.assertEqual(len(held), 313)
        excluded = {row["uid"]: reason for row in rows
                    if (reason := c.blocked_reason(row))}
        self.assertEqual(len(excluded.keys() - held), 290)
        self.assertEqual(public, {row["uid"] for row in rows} - held - excluded.keys())
        for uid in excluded.keys() - held:
            self.assertEqual(store["records"][uid]["status"], "blocked")
            self.assertEqual(store["records"][uid]["reason"], excluded[uid])
        self.assertEqual(c.load_json(c.STATUS_FILE, {})["3hentai"]["held_identity_gate"], 0)
        self.assertFalse(any(rec.get("hold_detail") == "3hentai_url_conflict"
                             for rec in store["records"].values()))
        overlapping_statuses = {
            "3hentai:3603074": "pending",
            "3hentai:3705834": "pending",
            "3hentai:3602988": "blocked",
        }
        self.assertEqual(held & excluded.keys(), overlapping_statuses.keys())
        for uid in held:
            with self.subTest(uid=uid):
                record = store["records"][uid]
                status = overlapping_statuses.get(uid, "pending")
                self.assertEqual(record["status"], status)
                expected_reason = excluded[uid] if status == "blocked" else "metadata_unverified"
                self.assertEqual(record["reason"], expected_reason)
        for uid in excess:
            rec = store["records"][uid]
            self.assertEqual(rec["hold_detail"], "3hentai_mislinked_split")
            self.assertIn("source_url", rec["repair_original"])
            self.assertNotIn("source_url", rec["item"])
