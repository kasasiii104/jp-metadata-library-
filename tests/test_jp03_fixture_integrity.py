"""Hashes of the unmodified JP-03 captured HTML (see jp03-spec.md)."""
import hashlib
import unittest
from pathlib import Path


class JP03FixtureIntegrityTests(unittest.TestCase):
    def test_captured_html_is_byte_identical(self):
        expected = {
            'html_list_ehentai_latest.html': '241a843275084f630a4e3cdacbbfb65479dee8f14c93e27f9853fd7d0fcfad3c',
            'html_list_ehentai_japanese.html': '6c05ed7d022b60a41e209063fb0828ccd3d133d58c40da271ded9d61af75d42a',
            'html_list_3hentai_search.html': 'e5c2c1d8b0abe4d534ada40f4d644cfca5ba4ab1ecdeaef1f5788f8a3b40bfca',
            'html_list_ehentai_empty.html': '74bb2d7e6d21c73eefb27665f342628aaf26558f835f33e6fa2a462e33eb0161',
            'html_list_3hentai_empty.html': 'def048e538414937769b571ea58ec4c5fc04038587249ec49fb141c4dc746903',
        }
        for name, digest in expected.items():
            with self.subTest(name=name):
                data = (Path(__file__).parent / 'fixtures' / 'jp03' / name).read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(), digest)
