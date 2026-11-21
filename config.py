import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DOCS_DIR = ROOT / "docs"
HENTAI3_ID_MAP_FILE = DOCS_DIR / "3hentai_id_map.json"
DATA_FILE = DOCS_DIR / "data.json"
STATE_FILE = DOCS_DIR / "crawl_state.json"
STATUS_FILE = DOCS_DIR / "source_status.json"
# Kept outside docs: excluded/unverified metadata is recoverable, but is not
# shipped in the public catalog or Pages artifact.
FILTER_STATE_FILE = ROOT / "state" / "filter_audit.json"

REQUEST_TIMEOUT = 30
USER_AGENT = "Japanese-Metadata-Library/1.6 (+GitHub Actions; metadata-only)"

# E-Hentai: latest + historical backfill every run.
EH_LATEST_PAGES = int(os.environ.get("EH_LATEST_PAGES", "2"))
EH_BACKFILL_PAGES = int(os.environ.get("EH_BACKFILL_PAGES", "4"))
EH_MAX_GALLERIES_PER_RUN = int(os.environ.get("EH_MAX_GALLERIES_PER_RUN", "150"))
EH_RETRY_LIMIT = int(os.environ.get("EH_RETRY_LIMIT", "25"))
EH_RETRY_MAX_FAILURES = int(os.environ.get("EH_RETRY_MAX_FAILURES", "5"))
EH_RETRY_QUEUE_MAX = int(os.environ.get("EH_RETRY_QUEUE_MAX", "2000"))

# Hitomi: binary Japanese Nozomi index, fetched by byte range.
HITOMI_LATEST_LIMIT = int(os.environ.get("HITOMI_LATEST_LIMIT", "30"))
HITOMI_BACKFILL_LIMIT = int(os.environ.get("HITOMI_BACKFILL_LIMIT", "60"))
HITOMI_RETRY_LIMIT = int(os.environ.get("HITOMI_RETRY_LIMIT", "30"))
HITOMI_RETRY_MAX_FAILURES = int(os.environ.get("HITOMI_RETRY_MAX_FAILURES", "5"))
HITOMI_RETRY_QUEUE_MAX = int(os.environ.get("HITOMI_RETRY_QUEUE_MAX", "2000"))
HITOMI_FILTER_AUDIT_LIMIT = int(os.environ.get("HITOMI_FILTER_AUDIT_LIMIT", "200"))
HITOMI_FILTER_AUDIT_DELAY_SEC = float(os.environ.get("HITOMI_FILTER_AUDIT_DELAY_SEC", "0.2"))
HITOMI_FILTER_AUDIT_REFRESH_SEC = 7 * 86400
HITOMI_FILTER_RETRY_SEC = 12 * 3600

# 3Hentai: latest pages + historical backfill every run.
HENTAI3_LATEST_PAGES = int(os.environ.get("HENTAI3_LATEST_PAGES", "2"))
HENTAI3_BACKFILL_PAGES = int(os.environ.get("HENTAI3_BACKFILL_PAGES", "3"))
HENTAI3_MAX_GALLERIES_PER_RUN = int(os.environ.get("HENTAI3_MAX_GALLERIES_PER_RUN", "60"))
HENTAI3_DETAIL_SLEEP_SEC = float(os.environ.get("HENTAI3_DETAIL_SLEEP_SEC", "0.8"))
HENTAI3_GALLERY_METADATA_LIMIT = int(os.environ.get("HENTAI3_GALLERY_METADATA_LIMIT", "80"))
HENTAI3_GALLERY_METADATA_DELAY_SEC = float(os.environ.get("HENTAI3_GALLERY_METADATA_DELAY_SEC", "0.25"))
HENTAI3_EXISTING_FILTER_AUDIT_LIMIT = int(os.environ.get("HENTAI3_EXISTING_FILTER_AUDIT_LIMIT", "60"))
HENTAI3_EXISTING_FILTER_AUDIT_DELAY_SEC = float(os.environ.get("HENTAI3_EXISTING_FILTER_AUDIT_DELAY_SEC", "0.35"))
HENTAI3_GET_DEBUG_LIMIT = int(os.environ.get("HENTAI3_GET_DEBUG_LIMIT", "0"))
HENTAI3_GET_DEBUG_DELAY_SEC = float(os.environ.get("HENTAI3_GET_DEBUG_DELAY_SEC", "1.5"))

# Items are retained indefinitely unless explicitly retired/filtered.
KEEP_ITEMS = 0

# Conservative exclusion set requested by the user.
BLOCK_TAGS = {
    "yaoi",
    "bestiality",
    "shotacon",
    "males only",
    "boys love",
    "boys' love",
    "bl",
    "guro",
    "snuff",
    "ryona",
    "torture",
    "amputee",
    "decapitation",
    "corpse",
    "blood",
    "farting",
    "miniguy",
    "vore",
    "scat",
}

# Aliases of already requested exclusions, not additional content categories.
BLOCK_TAG_ALIASES = {
    "やおい": "yaoi", "ヤオイ": "yaoi", "ボーイズラブ": "yaoi",
    "gore": "guro", "グロ": "guro", "グロテスク": "guro",
    "スナッフ": "snuff", "リョナ": "ryona", "拷問": "torture",
    "四肢欠損": "amputee", "手足欠損": "amputee", "斬首": "decapitation",
    "死体": "corpse", "流血": "blood",
    "fart": "farting", "farts": "farting", "おなら": "farting",
    "オナラ": "farting", "放屁": "farting", "ミニガイ": "miniguy",
    "丸呑み": "vore", "丸飲み": "vore", "捕食": "vore",
    "scatology": "scat", "スカトロ": "scat", "スカトロジー": "scat",
    "排便": "scat", "脱糞": "scat",
    "獣姦": "bestiality", "兽交": "bestiality", "獸交": "bestiality",
    "獸姦": "bestiality", "zoophilia": "bestiality", "animal on animal": "bestiality",
    "ショタコン": "shotacon",
}

BLOCK_TAG_ONLY_TAGS = {"bara", "gay"}

CHINESE_TITLE_MARKERS = (
    "汉化", "漢化", "中国翻訳", "中国翻译", "中國翻譯", "中文翻译", "中文翻譯",
    "中文翻訳", "个人翻译", "個人翻譯", "机翻", "机器翻译", "機械翻譯", "中译",
    "中譯", "简体中文", "簡體中文", "繁體中文", "繁体中文", "简中", "簡中",
    "繁中", "中文版",
)
CHINESE_TITLE_LABELS = {"chinese", "中国語", "中国语", "中國語", "中文"}
# Apply the Chinese title filter even when a title label explicitly lists Japanese.
CHINESE_BLOCK_BILINGUAL = False
JAPANESE_TITLE_LABELS = {
    "japanese", "日本語", "日本语", "日语", "日文", "日文版", "日本語版",
    "日本語翻訳", "日本翻訳", "中日雙語", "中日双语", "日中雙語", "日中双语",
}
CHINESE_SIMPLIFIED_CHARS = frozenset(
    "东书买亿们动卖发员图妈娇孙张总恶欢汉浑终给继绿缠脸舰见观让语说读谁谢贝财赏赚赢"
    "车转轻进远选钱铁铃银销错键长门闪问间闷闹题饥饭饮饿馆马骂验骗鸡鸣鹅齿龄龙"
)
CHINESE_SIMPLIFIED_MIN_DISTINCT = 2

# Insect / bug-like content requested to be excluded from the library.
# Matching is applied to normalized content tags and selected Japanese/English
# keywords before an item is saved. Existing matching rows are purged too.
BLOCK_INSECT_TAGS = {
    "insect", "insects", "insect girl", "insect girls", "insectoid",
    "bug", "bugs", "bug girl", "bug girls", "arthropod", "arthropods",
    "spider", "spiders", "cockroach", "cockroaches", "roach", "roaches",
    "centipede", "centipedes", "millipede", "millipedes",
    "maggot", "maggots", "larva", "larvae", "caterpillar", "caterpillars",
    "beetle", "beetles", "moth", "moths", "mosquito", "mosquitoes",
    "parasite", "parasites", "parasitism", "worm", "worms",
    "昆虫", "虫", "蟲", "虫系", "蟲系", "昆虫系", "蜘蛛", "クモ",
    "ゴキブリ", "ムカデ", "ヤスデ", "蛆", "ウジ", "幼虫", "芋虫",
    "寄生虫", "寄生",
}

# male:shota・male:dog・male:horse は BLOCK_FULL_TAGS に追記すれば有効化。
BLOCK_FULL_TAGS = {
    "male:male",
    "male:yaoi",
    "male:boys love",
    "male:boys' love",
    "female:guro",
    "male:guro",
    "other:guro",
    "other:snuff",
    "other:ryona",
    "male:miniguy",
    "male:vore",
    "female:scat",
    "male:scat",
    "other:scat",
}

ALLOWED_LANGUAGES = {"japanese", "ja", "日本語"}

INITIAL_RENDER_COUNT = 40
LOAD_MORE_COUNT = 40

# Independent corroboration budget for existing-row URL repairs.
HENTAI3_REPAIR_GET_LIMIT = max(0, int(os.environ.get("HENTAI3_REPAIR_GET_LIMIT", "10")))

# nHentai: v2 latest and ID-aligned historical backfill.
# Maximum pages to follow until reaching latest_id.
NH_LATEST_PAGES = int(os.environ.get("NH_LATEST_PAGES", "40"))
NH_EXISTING_TAG_AUDIT_LIMIT = int(os.environ.get("NH_EXISTING_TAG_AUDIT_LIMIT", "15"))
NH_READER_BACKFILL_LIMIT = int(os.environ.get("NH_READER_BACKFILL_LIMIT", "15"))
NH_MAX_DETAILS_PER_RUN = int(os.environ.get("NH_MAX_DETAILS_PER_RUN", "45"))
NH_INITIAL_BACKFILL_ITEMS = int(os.environ.get("NH_INITIAL_BACKFILL_ITEMS", "2500"))
NH_BACKFILL_SEEK_PAGES = int(os.environ.get("NH_BACKFILL_SEEK_PAGES", "6"))
NH_SEARCH_DELAY_SEC = float(os.environ.get("NH_SEARCH_DELAY_SEC", "6.5"))
NH_DETAIL_DELAY_SEC = float(os.environ.get("NH_DETAIL_DELAY_SEC", "3.2"))
NH_DETAIL_MAX_FAILURES = int(os.environ.get("NH_DETAIL_MAX_FAILURES", "3"))

NH_RUN_DEADLINE_SEC = float(os.environ.get("NH_RUN_DEADLINE_SEC", "420"))
NH_REQUEST_TIMEOUT = float(os.environ.get("NH_REQUEST_TIMEOUT", "15"))
