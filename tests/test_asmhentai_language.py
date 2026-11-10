import unittest

from sources import asmhentai


class ASMHentaiLanguageTests(unittest.TestCase):
    def test_japanese_values_are_recognized(self):
        values = (
            "japanese", "Japanese", "ja", "日本語", "jp",
            "language:japanese", "lang:japanese", "language:ja",
            "  JAPANESE  ", "  Language: JA  ", "lang:日本語",
        )
        for value in values:
            with self.subTest(value=value):
                self.assertTrue(asmhentai.is_japanese({"language": value}, []))
                self.assertTrue(asmhentai.is_japanese({}, [value]))

    def test_other_values_are_not_japanese(self):
        values = (
            "non-japanese", "japanese-translated", "english", "", "  ",
            "language:non-japanese", "language:japanese-translated",
            "language:javascript", "lang:japanese-translated",
        )
        for value in values:
            with self.subTest(value=value):
                self.assertFalse(asmhentai.is_japanese({"language": value}, []))
                self.assertFalse(asmhentai.is_japanese({}, [value]))

    def test_language_field_aliases_are_recognized(self):
        for key in ("language", "lang", "locale"):
            with self.subTest(key=key):
                self.assertTrue(asmhentai.is_japanese({key: "ja"}, []))

    def test_separate_values_do_not_form_a_language_name(self):
        self.assertFalse(asmhentai.is_japanese({"language": "japa"}, ["nese"]))

    def test_japanese_tag_is_recognized_among_other_values(self):
        self.assertTrue(asmhentai.is_japanese(
            {"language": "english"}, ["non-japanese", "language:ja"]
        ))
