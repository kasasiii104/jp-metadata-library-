import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from tools import build_public_catalog as catalog


class PublicCatalogSwapTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.out = self.root / "catalog"
        self.out.mkdir()
        (self.out / "old.json").write_text("old catalog", encoding="utf-8")
        self.index = self.root / "index.html"
        self.original_html = (
            catalog.FALLBACK_START + "old cards" + catalog.FALLBACK_END
            + catalog.STATUS_START + "old status" + catalog.STATUS_END
        )
        self.index.write_text(self.original_html, encoding="utf-8")
        self.data = self.root / "data.json"
        self.items = [{"uid": f"item:{i}", "title": f"Title {i}"} for i in range(3)]
        self.data.write_text(json.dumps({"items": self.items, "updated_at": "today"}),
                             encoding="utf-8")
        for name, value in (("DATA", self.data), ("OUT", self.out),
                            ("INDEX", self.index), ("CHUNK_SIZE", 2)):
            patcher = patch.object(catalog, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def assert_old_preserved(self):
        self.assertEqual({p.name for p in self.out.iterdir()}, {"old.json"})
        self.assertEqual((self.out / "old.json").read_text(), "old catalog")
        self.assertEqual(self.index.read_text(), self.original_html)
        self.assert_no_temporary_directories()

    def assert_no_temporary_directories(self):
        self.assertEqual({p.name for p in self.root.iterdir()},
                         {"catalog", "data.json", "index.html"})

    def test_chunk_write_failure_keeps_catalog_and_index(self):
        original = catalog.write_json

        def failing_write(path, payload):
            if path.name == "chunk-001.json":
                raise OSError("chunk write failed")
            original(path, payload)

        with patch.object(catalog, "write_json", side_effect=failing_write):
            with self.assertRaises(OSError):
                catalog.main()
        self.assert_old_preserved()

    def test_index_replace_failure_restores_catalog_and_index(self):
        original = Path.replace

        def failing_replace(source, destination):
            if destination == self.index:
                raise OSError("index replace failed")
            return original(source, destination)

        with patch.object(Path, "replace", failing_replace):
            with self.assertRaises(OSError):
                catalog.main()
        self.assert_old_preserved()

    def test_empty_items_preserves_catalog_and_index(self):
        self.data.write_text(json.dumps({"items": []}), encoding="utf-8")
        with self.assertRaises(ValueError):
            catalog.main()
        self.assert_old_preserved()

    def test_index_close_failure_cleans_temporary_file(self):
        original = catalog.tempfile.NamedTemporaryFile

        class FailingClose:
            def __init__(self, *args, **kwargs):
                self.file = original(*args, **kwargs)

            def __enter__(self):
                return self.file.__enter__()

            def __exit__(self, *args):
                self.file.__exit__(*args)
                raise OSError("flush failed")

        with patch.object(catalog.tempfile, "NamedTemporaryFile", FailingClose):
            with self.assertRaises(OSError):
                catalog.main()
        self.assert_old_preserved()

    def test_index_write_failure_cleans_temporary_file(self):
        original = catalog.tempfile.NamedTemporaryFile

        def failing_file(*args, **kwargs):
            file = original(*args, **kwargs)
            file.write = lambda text: (_ for _ in ()).throw(OSError("write failed"))
            return file

        with patch.object(catalog.tempfile, "NamedTemporaryFile", failing_file):
            with self.assertRaises(OSError):
                catalog.main()
        self.assert_old_preserved()

    def test_success_replaces_catalog_and_index(self):
        self.assertEqual(catalog.main(), 0)
        manifest = json.loads((self.out / "manifest.json").read_text())
        self.assertEqual(manifest["item_count"], 3)
        self.assertEqual(manifest["chunk_count"], 2)
        rows = []
        for i in range(2):
            rows.extend(json.loads((self.out / f"chunk-{i:03d}.json").read_text())["items"])
        self.assertEqual(rows, self.items)
        self.assertFalse((self.out / "old.json").exists())
        self.assertIn("Title 0", self.index.read_text())
        self.assert_no_temporary_directories()

    def test_published_files_keep_umask_permissions(self):
        # mkdtemp/NamedTemporaryFile default to 0700/0600; Pages output must not.
        previous = os.umask(0o022)
        self.addCleanup(os.umask, previous)
        self.assertEqual(catalog.main(), 0)
        self.assertEqual(stat.S_IMODE(self.out.stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE(self.index.stat().st_mode), 0o644)

    def test_second_rename_failure_restores_catalog(self):
        original = Path.rename

        def failing_rename(source, destination):
            if source.name.startswith(".catalog-tmp-"):
                raise OSError("swap failed")
            return original(source, destination)

        with patch.object(Path, "rename", failing_rename):
            with self.assertRaises(OSError):
                catalog.main()
        self.assert_old_preserved()

    def test_validation_failure_keeps_catalog_and_index(self):
        original = catalog.write_json

        def corrupt_write(path, payload):
            original(path, {} if path.name == "manifest.json" else payload)

        with patch.object(catalog, "write_json", side_effect=corrupt_write):
            with self.assertRaises(ValueError):
                catalog.main()
        self.assert_old_preserved()


if __name__ == "__main__":
    unittest.main()
