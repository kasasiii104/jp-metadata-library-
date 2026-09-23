import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DOCS_DIR = ROOT / "docs"
DATA_FILE = DOCS_DIR / "data.json"
STATE_FILE = DOCS_DIR / "crawl_state.json"
STATUS_FILE = DOCS_DIR / "source_status.json"

REQUEST_TIMEOUT = 30
USER_AGENT = "Japanese-Metadata-Library/1.2 (+GitHub Actions; metadata-only)"

# E-Hentai collection budgets.
EH_LATEST_PAGES = int(os.environ.get("EH_LATEST_PAGES", "2"))
EH_BACKFILL_PAGES = int(os.environ.get("EH_BACKFILL_PAGES", "4"))
EH_MAX_GALLERIES_PER_RUN = int(os.environ.get("EH_MAX_GALLERIES_PER_RUN", "100"))

# Hitomi uses a binary Nozomi index. Only required byte ranges are fetched.
HITOMI_LATEST_LIMIT = int(os.environ.get("HITOMI_LATEST_LIMIT", "30"))
HITOMI_BACKFILL_LIMIT = int(os.environ.get("HITOMI_BACKFILL_LIMIT", "60"))

# Pururin: latest page is checked every run and historical pages are backfilled.
PURURIN_LATEST_PAGES = int(os.environ.get("PURURIN_LATEST_PAGES", "2"))
PURURIN_BACKFILL_PAGES = int(os.environ.get("PURURIN_BACKFILL_PAGES", "2"))
PURURIN_MAX_GALLERIES_PER_RUN = int(os.environ.get("PURURIN_MAX_GALLERIES_PER_RUN", "50"))
PURURIN_DETAIL_SLEEP_SEC = float(os.environ.get("PURURIN_DETAIL_SLEEP_SEC", "1.0"))

# Items are retained indefinitely unless manually removed.
KEEP_ITEMS = 0

# Exact normalized tag names to exclude. Keep this list conservative.
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
}

ALLOWED_LANGUAGES = {"japanese", "ja", "日本語"}

INITIAL_RENDER_COUNT = 40
LOAD_MORE_COUNT = 40
