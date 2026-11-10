import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sources import hitomi


class HitomiIndexResponseTests(unittest.TestCase):
    def fetch(self, status, data, content_range=None, start=1, count=3):
        headers = {} if content_range is None else {"Content-Range": content_range}
        response = SimpleNamespace(status_code=status, content=data, headers=headers)
        with patch.object(hitomi, "safe_get", return_value=response):
            return hitomi._fetch_range_from_base(object(), "https://example.invalid", start, count)

    def test_short_200_slices_from_requested_start(self):
        data = b"".join(value.to_bytes(4, "big") for value in (10, 20, 30))
        self.assertEqual(self.fetch(200, data), ([20, 30], None))

    def test_valid_206(self):
        data = b"".join(value.to_bytes(4, "big") for value in (20, 30, 40))
        self.assertEqual(self.fetch(206, data, "bytes 4-15/40"), ([20, 30, 40], 10))

    def test_invalid_206_content_range(self):
        for content_range in (None, "bytes 0-11/40", "bytes 4-19/40", "invalid", "bytes 4-3/40"):
            with self.subTest(content_range=content_range):
                with self.assertRaises(ValueError):
                    self.fetch(206, b"\x00" * 12, content_range)

    def test_invalid_206_total_raises(self):
        for content_range in ("bytes 0-11/4", "bytes 0-11/14"):
            with self.subTest(content_range=content_range):
                with self.assertRaises(ValueError):
                    self.fetch(206, b"\x00" * 12, content_range, start=0)

    def test_short_terminal_206_is_accepted(self):
        data = b"".join(value.to_bytes(4, "big") for value in (10, 20))
        self.assertEqual(self.fetch(206, data, "bytes 0-7/8", start=0, count=4),
                         ([10, 20], 2))

    def test_partial_id_body_raises(self):
        for status, content_range in ((200, None), (206, "bytes 4-8/40")):
            with self.subTest(status=status):
                with self.assertRaises(ValueError):
                    self.fetch(status, b"\x00" * 5, content_range)
