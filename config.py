import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DOCS_DIR = ROOT / "docs"
DATA_FILE = DOCS_DIR / "data.json"
STATE_FILE = DOCS_DIR / "crawl_state.json"
STATUS_FILE = DOCS_DIR / "source_status.json"

REQUEST_TIMEOUT = 30
USER_AGENT = "Japanese-Metadata-Library/1.6 (+GitHub Actions; metadata-only)"

# E-Hentai: latest + historical backfill every run.
EH_LATEST_PAGES = int(os.environ.get("EH_LATEST_PAGES", "2"))
EH_BACKFILL_PAGES = int(os.environ.get("EH_BACKFILL_PAGES", "4"))
EH_MAX_GALLERIES_PER_RUN = int(os.environ.get("EH_MAX_GALLERIES_PER_RUN", "150"))

# Hitomi: binary Japanese Nozomi index, fetched by byte range.
HITOMI_LATEST_LIMIT = int(os.environ.get("HITOMI_LATEST_LIMIT", "30"))
HITOMI_BACKFILL_LIMIT = int(os.environ.get("HITOMI_BACKFILL_LIMIT", "60"))

# 3Hentai: latest pages + historical backfill every run.
HENTAI3_LATEST_PAGES = int(os.environ.get("HENTAI3_LATEST_PAGES", "2"))
HENTAI3_BACKFILL_PAGES = int(os.environ.get("HENTAI3_BACKFILL_PAGES", "3"))
HENTAI3_MAX_GALLERIES_PER_RUN = int(os.environ.get("HENTAI3_MAX_GALLERIES_PER_RUN", "60"))
HENTAI3_DETAIL_SLEEP_SEC = float(os.environ.get("HENTAI3_DETAIL_SLEEP_SEC", "0.8"))
HENTAI3_GALLERY_METADATA_LIMIT = int(os.environ.get("HENTAI3_GALLERY_METADATA_LIMIT", "60"))
HENTAI3_GALLERY_METADATA_DELAY_SEC = float(os.environ.get("HENTAI3_GALLERY_METADATA_DELAY_SEC", "0.25"))
HENTAI3_EXISTING_FILTER_AUDIT_LIMIT = int(os.environ.get("HENTAI3_EXISTING_FILTER_AUDIT_LIMIT", "24"))
HENTAI3_EXISTING_FILTER_AUDIT_DELAY_SEC = float(os.environ.get("HENTAI3_EXISTING_FILTER_AUDIT_DELAY_SEC", "0.35"))
HENTAI3_GET_DEBUG_LIMIT = int(os.environ.get("HENTAI3_GET_DEBUG_LIMIT", "0"))
HENTAI3_GET_DEBUG_DELAY_SEC = float(os.environ.get("HENTAI3_GET_DEBUG_DELAY_SEC", "1.5"))

# Items are retained indefinitely unless explicitly retired/filtered.
KEEP_ITEMS = 0

# Conservative exclusion set requested by the user.
BLOCK_TAGS = {
    "yaoi",
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
    "farting",
    "miniguy",
    "vore",
    "scat",
}

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

BLOCK_FULL_TAGS = {
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
