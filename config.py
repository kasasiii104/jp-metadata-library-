from pathlib import Path

ROOT = Path(__file__).resolve().parent
DOCS_DIR = ROOT / "docs"
DATA_FILE = DOCS_DIR / "data.json"
STATE_FILE = DOCS_DIR / "crawl_state.json"
STATUS_FILE = DOCS_DIR / "source_status.json"

REQUEST_TIMEOUT = 30
USER_AGENT = "Japanese-Metadata-Library/1.0 (+GitHub Actions; metadata-only)"

# Per-run collection budgets. Tune upward only after verifying source stability.
EH_LATEST_PAGES = 1
EH_BACKFILL_PAGES = 1
EH_MAX_GALLERIES_PER_RUN = 50

HITOMI_LATEST_LIMIT = 16
HITOMI_BACKFILL_LIMIT = 16

NH_LATEST_PAGES = 1
NH_BACKFILL_PAGES = 1
NH_MAX_DETAILS_PER_RUN = 40

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

# Namespace-qualified tags are also checked against this set.
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

# Home-page rendering settings.
INITIAL_RENDER_COUNT = 40
LOAD_MORE_COUNT = 40
