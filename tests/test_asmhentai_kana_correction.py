import unittest
from unittest import mock

from sources import asmhentai


def _collect(rows, key="日本語"):
    """Run collect() over one search page of rows with no detail fetches."""
    with mock.patch.object(asmhentai, "SEARCH_KEYS", [key]), \
            mock.patch.object(asmhentai, "PAGES", 1), \
            mock.patch.object(asmhentai, "DETAIL_LIMIT", 0), \
            mock.patch.object(asmhentai, "_search", return_value=rows), \
            mock.patch.object(asmhentai, "_get", side_effect=AssertionError("no detail")):
        return asmhentai.collect({})


class ASMHentaiKanaCorrectionTests(unittest.TestCase):
    def test_kana_titles_are_corrected_to_japanese(self):
        titles = {
            "101": "ひみつの放課後",
            "102": "メイドカフェ",
            "103": "彼女の夏休み",  # kanji + hiragana
            "104": "Summer ノート",
        }
        rows = [{"id": sid, "title": title} for sid, title in titles.items()]
        items, _state, result = _collect(rows)
        self.assertEqual({x["source_id"] for x in items}, set(titles))
        self.assertTrue(all(x["language"] == "japanese" for x in items))
        self.assertEqual(result["rejected_non_japanese"], 0)

    def test_kanji_only_titles_are_not_corrected(self):
        for title in ("我的青春戀愛物語", "催眠調教學園", "漢字 Only Title 2"):
            with self.subTest(title=title):
                items, _state, result = _collect([{"id": "201", "title": title}])
                self.assertEqual(items, [])
                self.assertEqual(result["rejected_non_japanese"], 1)

    def test_kanji_only_title_with_japanese_metadata_still_passes(self):
        rows = [
            {"id": "301", "title": "純愛物語", "language": "japanese"},
            {"id": "302", "title": "学園天国", "tags": ["language:japanese"]},
        ]
        items, _state, _result = _collect(rows)
        self.assertEqual({x["source_id"] for x in items}, {"301", "302"})

    def test_kana_correction_only_applies_to_the_japanese_search_key(self):
        items, _state, result = _collect([{"id": "401", "title": "ひみつの放課後"}], key="japanese")
        self.assertEqual(items, [])
        self.assertEqual(result["rejected_non_japanese"], 1)


if __name__ == "__main__":
    unittest.main()
