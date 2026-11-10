import unittest

from sources.hentai3 import _href_namespace


class HrefNamespaceTests(unittest.TestCase):
    def test_path_namespace_preserves_value_case(self):
        cases = (
            ("/Tag/AbC", ("tag", "AbC")),
            ("/ARTIST/AbC", ("artist", "AbC")),
            ("/tag/abc", ("tag", "abc")),
            ("/artist/abc", ("artist", "abc")),
            ("/tag/AbC", ("tag", "AbC")),
            ("/artist/AbC", ("artist", "AbC")),
            ("/tags/foo-bar_baz/", ("tag", "foo bar baz")),
        )
        for href, expected in cases:
            with self.subTest(href=href):
                self.assertEqual(_href_namespace(href), expected)
