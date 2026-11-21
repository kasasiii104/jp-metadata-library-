import re
import unicodedata


EXCLUDED_READER_TAGS = {
    "lolicon", "shotacon", "loli", "shota", "oppai loli", "toddlercon",
    "low lolicon", "low shotacon", "low loli", "low shota",
}
READER_MEDIA_ID = re.compile(r"[1-9][0-9]{0,11}")
READER_EXTS = re.compile(r"[wjpg]+")
READER_MAX_PAGES = 2000

# Match the whitespace recognized by JavaScript's \s and trim().
_JS_WHITESPACE = re.compile(r"[\t-\r \u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]+")
_TAG_TRANSLATION = str.maketrans("’‘_-", "''  ")
_GENDER_SUFFIX = re.compile(r" *\( *(?:female|male|other|mixed|f|m) *\)$")


def normalize_reader_tag(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).lower()
    return ":".join(
        _JS_WHITESPACE.sub(" ", part.translate(_TAG_TRANSLATION)).strip(" ")
        for part in normalized.split(":", 1)
    )


def reader_tags_verified(tags: object) -> bool:
    return isinstance(tags, list) and bool(tags) and all(isinstance(tag, str) for tag in tags)


def is_reader_excluded(tags: list[str]) -> bool:
    if not reader_tags_verified(tags):
        return False
    for tag in tags:
        base = normalize_reader_tag(tag).rsplit(":", 1)[-1].strip(" ")
        base = _GENDER_SUFFIX.sub("", base).strip(" ")
        if base in EXCLUDED_READER_TAGS or re.search("ロリコン|ショタコン", unicodedata.normalize("NFKC", tag)):
            return True
    return False
