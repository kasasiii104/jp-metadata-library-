#!/usr/bin/env python3
import json
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import (
    ALLOWED_LANGUAGES,
    BLOCK_FULL_TAGS,
    BLOCK_TAGS,
    DATA_FILE,
    DOCS_DIR,
    KEEP_ITEMS,
    STATE_FILE,
    STATUS_FILE,
)
from sources import ehentai, hitomi, pururin
from sources.common import normalize_full_tag, normalize_tag, unique_strings

SOURCES = {
    "ehentai": ehentai.collect,
    "hitomi": hitomi.collect,
    "pururin": pururin.collect,
}

NORMALIZED_BLOCK_TAGS = {normalize_tag(x) for x in BLOCK_TAGS}
NORMALIZED_BLOCK_FULL_TAGS = {normalize_full_tag(x) for x in BLOCK_FULL_TAGS}
ALLOWED_LANGUAGE_SET = {str(x).lower() for x in ALLOWED_LANGUAGES}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    tmp.replace(path)


def normalized_language(value: str) -> str:
    v = unicodedata.normalize("NFKC", str(value or "")).strip().lower()
    return "japanese" if v in ALLOWED_LANGUAGE_SET else v


def blocked_reason(item: dict[str, Any]) -> str:
    if normalized_language(item.get("language", "")) != "japanese":
        return "language"

    candidates: list[str] = []
    candidates.extend(item.get("tags") or [])
    if item.get("category"):
        candidates.append(item["category"])

    for raw in candidates:
        full = normalize_full_tag(raw)
        base = normalize_tag(full.split(":", 1)[-1])
        if full in NORMALIZED_BLOCK_FULL_TAGS:
            return f"blocked:{full}"
        if base in NORMALIZED_BLOCK_TAGS:
            return f"blocked:{base}"
    return ""


def clean_item(item: dict[str, Any], stamp: str) -> dict[str, Any]:
    return {
        "uid": str(item.get("uid") or ""),
        "source": str(item.get("source") or ""),
        "source_id": str(item.get("source_id") or ""),
        "source_token": str(item.get("source_token") or ""),
        "source_url": str(item.get("source_url") or ""),
        "title": str(item.get("title") or "").strip(),
        "title_jp": str(item.get("title_jp") or "").strip(),
        "language": normalized_language(item.get("language", "")),
        "category": str(item.get("category") or "").strip(),
        "artists": unique_strings(item.get("artists") or []),
        "groups": unique_strings(item.get("groups") or []),
        "parodies": unique_strings(item.get("parodies") or []),
        "characters": unique_strings(item.get("characters") or []),
        "tags": unique_strings(item.get("tags") or []),
        "pages": int(item.get("pages") or 0),
        "rating": item.get("rating") if isinstance(item.get("rating"), (int, float)) else None,
        "popularity": item.get("popularity") if isinstance(item.get("popularity"), (int, float)) else None,
        "posted_at": str(item.get("posted_at") or ""),
        "thumbnail": str(item.get("thumbnail") or ""),
        "first_seen": str(item.get("first_seen") or stamp),
        "last_seen": stamp,
    }


def merge_item(old: dict[str, Any] | None, new: dict[str, Any], stamp: str) -> dict[str, Any]:
    if not old:
        return clean_item(new, stamp)
    merged = dict(old)
    refreshed = clean_item(new, stamp)
    for key, value in refreshed.items():
        if key == "first_seen":
            continue
        if value not in ("", None, [], 0) or key in {"pages", "rating", "popularity"}:
            merged[key] = value
    merged["first_seen"] = old.get("first_seen") or stamp
    merged["last_seen"] = stamp
    return merged


def sort_key(item: dict[str, Any]):
    return (
        str(item.get("posted_at") or ""),
        str(item.get("first_seen") or ""),
        str(item.get("uid") or ""),
    )


def main() -> int:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = now_iso()

    data = load_json(DATA_FILE, {"items": []})
    existing_items = [
        x for x in data.get("items", [])
        if isinstance(x, dict) and x.get("uid") and x.get("source") != "nharchive"
    ]
    existing = {x["uid"]: x for x in existing_items}

    state = load_json(STATE_FILE, {})
    status_store = load_json(STATUS_FILE, {})
    if isinstance(status_store, dict):
        status_store.pop("updated_at", None)

    # Remove retired source state/status; existing old records are kept unless the user
    # removes them from data.json manually. New crawling no longer uses NH Archive.
    state.pop("nharchive", None)
    status_store.pop("nharchive", None)

    total_raw = 0
    total_accepted = 0
    successful_sources = 0

    for name, collector in SOURCES.items():
        print(f"[{name}] start")
        src_state = state.get(name) if isinstance(state.get(name), dict) else {}
        try:
            raw_items, new_state, result = collector(src_state)
        except Exception as e:
            raw_items, new_state, result = [], src_state, {"status": "error", "message": str(e)}

        state[name] = new_state
        total_raw += len(raw_items)
        accepted = 0
        blocked = 0
        blocked_reasons: dict[str, int] = {}

        for raw in raw_items:
            if not raw.get("uid"):
                continue
            reason = blocked_reason(raw)
            if reason:
                blocked += 1
                blocked_reasons[reason] = blocked_reasons.get(reason, 0) + 1
                continue
            uid = raw["uid"]
            existing[uid] = merge_item(existing.get(uid), raw, stamp)
            accepted += 1

        if result.get("status") == "ok":
            successful_sources += 1

        total_accepted += accepted
        previous = status_store.get(name) if isinstance(status_store.get(name), dict) else {}
        status_store[name] = {
            **previous,
            **result,
            "last_attempt": stamp,
            "accepted": accepted,
            "blocked": blocked,
            "blocked_reasons": blocked_reasons,
        }
        if result.get("status") == "ok":
            status_store[name]["last_success"] = stamp

        msg = str(result.get("message") or "").strip()
        print(
            f"[{name}] raw={len(raw_items)} accepted={accepted} blocked={blocked} "
            f"status={result.get('status')}" + (f" message={msg}" if msg else "")
        )

    items = sorted(existing.values(), key=sort_key, reverse=True)
    if KEEP_ITEMS > 0:
        items = items[:KEEP_ITEMS]

    save_json(DATA_FILE, {"updated_at": stamp, "item_count": len(items), "items": items})
    save_json(STATE_FILE, state)
    save_json(STATUS_FILE, {**status_store, "updated_at": stamp})

    print(f"done: raw={total_raw}, accepted={total_accepted}, total={len(items)}")
    if successful_sources == 0 and not items:
        print("All sources failed and there is no retained dataset.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
