import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import crawler
from tools import build_public_catalog as catalog


FIRST_SEEN = "2026-11-16T00:00:00+00:00"
STAMP = "2026-11-17T00:00:00+00:00"


class PublicFirstSeenTests(unittest.TestCase):
    def test_public_fields_include_first_seen(self):
        self.assertIn("first_seen", catalog.PUBLIC_FIELDS)

    def test_compact_item_preserves_first_seen(self):
        self.assertEqual(catalog.compact_item({"first_seen": FIRST_SEEN})["first_seen"],
                         FIRST_SEEN)

    def test_compact_item_omits_empty_or_missing_first_seen(self):
        for fields in ({"first_seen": ""}, {"first_seen": None}, {}):
            with self.subTest(fields=fields):
                item = {"posted_at": STAMP, **fields}
                self.assertNotIn("first_seen", catalog.compact_item(item))

    def test_main_publishes_first_seen_in_chunk(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "data.json"
            out = root / "catalog"
            index = root / "index.html"
            item = {"uid": "item:1", "title": "Title", "first_seen": FIRST_SEEN}
            data.write_text(json.dumps({"items": [item], "updated_at": STAMP}),
                            encoding="utf-8")
            index.write_text(
                catalog.FALLBACK_START + "old cards" + catalog.FALLBACK_END
                + catalog.STATUS_START + "old status" + catalog.STATUS_END,
                encoding="utf-8",
            )
            with patch.multiple(catalog, DATA=data, OUT=out, INDEX=index):
                self.assertEqual(catalog.main(), 0)
            chunk = json.loads((out / "chunk-000.json").read_text(encoding="utf-8"))
            self.assertEqual(chunk["items"], [item])

    def test_merge_item_preserves_existing_first_seen(self):
        old = {"uid": "item:1", "first_seen": FIRST_SEEN}
        new = {"uid": "item:1", "first_seen": STAMP}
        self.assertEqual(crawler.merge_item(old, new, STAMP)["first_seen"], FIRST_SEEN)

    def test_merge_item_sets_first_seen_to_stamp_for_new_item(self):
        new = {"uid": "item:1"}
        self.assertEqual(crawler.merge_item(None, new, STAMP)["first_seen"], STAMP)
