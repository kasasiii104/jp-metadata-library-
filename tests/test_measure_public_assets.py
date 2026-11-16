import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('measure_public_assets', Path(__file__).resolve().parents[1] / 'tools/measure_public_assets.py')
measure = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(measure)


class MeasurePublicAssetsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.docs = self.root / 'docs'
        (self.docs / 'thumbs/a').mkdir(parents=True)
        (self.docs / 'catalog').mkdir()
        for name, content in [('thumbs/a/used.webp', b'123'), ('thumbs/a/orphan.jpg', b'12345'), ('index.html', b'ab')]:
            (self.docs / name).write_bytes(content)
        self.rows = [{'thumbnail': p} for p in ['thumbs/a/used.webp', './thumbs/a/used.webp?x=1', 'thumbs/a/missing.webp', 'https://example.org/a.jpg', '../thumbs/a/out.webp', '/thumbs/a/absolute.webp', 'thumbs/../escape.webp']]
        (self.docs / 'data.json').write_text(json.dumps({'items': self.rows}))
        (self.docs / 'catalog/chunk-000.json').write_text(json.dumps({'items': self.rows}))

    def tree(self, truncated=False, size=None):
        entries = [{'path': 'docs/' + p.relative_to(self.docs).as_posix(), 'type': 'blob', 'size': p.stat().st_size if size is None else size} for p in self.docs.rglob('*') if p.is_file()]
        path = self.root / 'tree.json'
        path.write_text(json.dumps({'sha': 'abc', 'tree': entries, 'truncated': truncated}))
        return path

    def snapshot(self):
        return {str(p.relative_to(self.docs)): (p.stat().st_size, p.stat().st_mtime_ns, p.read_bytes()) for p in self.docs.rglob('*') if p.is_file()}

    def test_classification_sizes_and_no_changes(self):
        before = self.snapshot()
        result = measure.measure(self.docs)
        jp = result['jp07']
        self.assertEqual(jp['paths'], ['thumbs/a/orphan.jpg'])
        self.assertEqual(jp['unreferenced']['a'], {'files': 1, 'bytes': 5, 'mib': 0.0})
        self.assertEqual(jp['missing_count'], 1)
        self.assertEqual(jp['nonlocal_count'], 4)
        self.assertEqual(result['thumbs']['a']['bytes'], 8)
        self.assertEqual(result['data_json']['items'], 7)
        self.assertEqual(result['catalog']['max_chunk_bytes'], (self.docs / 'catalog/chunk-000.json').stat().st_size)
        self.assertEqual(result['docs']['bytes'], sum(v[0] for v in before.values()))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(measure.main(['--docs', str(self.docs), '--json', str(self.root / 'out.json'), '--list', str(self.root / 'out.txt')]), 0)
        self.assertEqual(json.loads((self.root / 'out.json').read_text())['jp07'], jp)
        self.assertEqual((self.root / 'out.txt').read_text(), 'docs/thumbs/a/orphan.jpg\n')
        self.assertEqual(before, self.snapshot())

    def test_tree_parity_and_unassessed(self):
        tree = self.tree()
        local = measure.measure(self.docs)
        remote = measure.measure(self.docs, tree, self.docs / 'data.json')
        for key in ('data_json', 'catalog', 'thumbs', 'docs', 'other_root_files', 'jp07'):
            self.assertEqual(local[key], remote[key], key)
        unknown = measure.measure(self.docs, tree)
        self.assertIsNone(unknown['jp07']['unreferenced'])
        self.assertIsNone(unknown['jp07']['missing_count'])
        self.assertIsNone(unknown['data_json']['items'])
        self.assertIn('カタログ未指定のため未判定', measure.summary(unknown))
        self.assertNotIn('unreferenced/', measure.summary(unknown))
        self.assertNotIn('missing references: 0', measure.summary(unknown))
        partial = measure.measure(self.docs, self.tree(True))
        self.assertTrue(partial['truncated'])
        self.assertIn('incomplete (truncated=true)', measure.summary(partial))

    def test_chunk_fallback(self):
        # Move the fixture input out of docs; the tool itself never mutates it.
        (self.docs / 'data.json').rename(self.root / 'saved.json')
        (self.docs / 'catalog/chunk-001.json').write_text(json.dumps({'items': [{'thumbnail': 'thumbs/a/orphan.jpg'}]}))
        result = measure.measure(self.docs)
        self.assertEqual(result['data_json']['files'], 0)
        self.assertEqual(result['data_json']['items'], 8)
        self.assertEqual(result['jp07']['paths'], [])

    def test_large_capacity_succeeds(self):
        tree = self.tree(size=10**18)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(measure.main(['--tree-json', str(tree)]), 0)
        self.assertIn(str(5 * 10**18), output.getvalue())

    def test_output_cannot_overwrite_inputs(self):
        before = self.snapshot()
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            measure.main(['--docs', str(self.docs), '--json', str(self.docs / 'data.json')])
        self.assertEqual(error.exception.code, 2)
        self.assertEqual(before, self.snapshot())

    def test_invalid_inputs(self):
        path = self.root / 'invalid.json'
        path.write_text('{}')
        with self.assertRaises(ValueError):
            measure.measure(self.docs, path)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            measure.main(['--catalog', str(path), '--docs', str(self.docs)])
        self.assertEqual(error.exception.code, 2)


if __name__ == '__main__':
    unittest.main()
