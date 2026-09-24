#!/usr/bin/env python3
import hashlib
import json
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import (
    ALLOWED_LANGUAGES,
    BLOCK_FULL_TAGS,
    BLOCK_INSECT_TAGS,
    BLOCK_TAGS,
    DATA_FILE,
    DOCS_DIR,
    KEEP_ITEMS,
    STATE_FILE,
    STATUS_FILE,
)
from sources import ehentai, hitomi, hentai3
from sources.common import normalize_full_tag, normalize_tag, unique_strings

SOURCES = {
    "ehentai": ehentai.collect,
    "hitomi": hitomi.collect,
    "3hentai": hentai3.collect,
}
RETIRED_SOURCES = {"pururin", "nharchive", "nhentai"}

NORMALIZED_BLOCK_TAGS = {normalize_tag(x) for x in BLOCK_TAGS}
NORMALIZED_BLOCK_FULL_TAGS = {normalize_full_tag(x) for x in BLOCK_FULL_TAGS}
NORMALIZED_INSECT_TAGS = {normalize_tag(x) for x in BLOCK_INSECT_TAGS}
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


def _insect_tag_match(value: str) -> str:
    full = normalize_full_tag(value)
    base = normalize_tag(full.split(":", 1)[-1])
    if not base:
        return ""
    if base in NORMALIZED_INSECT_TAGS:
        return base
    # E-Hentai/Hitomi occasionally use compound labels such as
    # "insect impregnation" or "giant spider". Match whole English tokens
    # and Japanese substrings, but never scan artist/group names.
    for term in NORMALIZED_INSECT_TAGS:
        if not term:
            continue
        if re.search(r"[ぁ-んァ-ン一-龯々〆ヵヶ蟲]", term):
            if term in base:
                return term
        elif re.search(rf"(?:^|\s){re.escape(term)}(?:$|\s)", base):
            return term
    return ""


def insect_block_reason(item: dict[str, Any]) -> str:
    candidates: list[str] = list(item.get("tags") or [])
    if item.get("category"):
        candidates.append(str(item.get("category") or ""))
    for raw in candidates:
        match = _insect_tag_match(str(raw))
        if match:
            return f"blocked:insect:{match}"
    return ""


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

    insect = insect_block_reason(item)
    if insect:
        return insect
    return ""


def _meta_tags(artists: list[str], groups: list[str], works: list[str], characters: list[str]) -> list[str]:
    return unique_strings(
        [f"artist:{x}" for x in artists]
        + [f"group:{x}" for x in groups]
        + [f"work:{x}" for x in works]
        + [f"character:{x}" for x in characters]
    )


def clean_item(item: dict[str, Any], stamp: str) -> dict[str, Any]:
    artists = unique_strings(item.get("artists") or [])
    groups = unique_strings(item.get("groups") or [])
    parodies = unique_strings(item.get("parodies") or item.get("works") or [])
    works = unique_strings(item.get("works") or parodies)
    characters = unique_strings(item.get("characters") or [])
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
        "artists": artists,
        "groups": groups,
        "parodies": parodies,
        "works": works,
        "characters": characters,
        "tags": unique_strings(item.get("tags") or []),
        "meta_tags": _meta_tags(artists, groups, works, characters),
        "pages": int(item.get("pages") or 0),
        "rating": item.get("rating") if isinstance(item.get("rating"), (int, float)) else None,
        "popularity": item.get("popularity") if isinstance(item.get("popularity"), (int, float)) else None,
        "posted_at": str(item.get("posted_at") or ""),
        "thumbnail": str(item.get("thumbnail") or ""),
        "first_seen": str(item.get("first_seen") or stamp),
        "last_seen": stamp,
    }




def upgrade_item_schema(item: dict[str, Any]) -> dict[str, Any]:
    """Backfill structured metadata tags for rows saved by older revisions."""
    out = dict(item)
    artists = unique_strings(out.get("artists") or [])
    groups = unique_strings(out.get("groups") or [])
    parodies = unique_strings(out.get("parodies") or out.get("works") or [])
    works = unique_strings(out.get("works") or parodies)
    characters = unique_strings(out.get("characters") or [])
    out["artists"] = artists
    out["groups"] = groups
    out["parodies"] = parodies
    out["works"] = works
    out["characters"] = characters
    out["tags"] = unique_strings(out.get("tags") or [])
    out["meta_tags"] = _meta_tags(artists, groups, works, characters)
    return out

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


def _dedupe_text(value: str, *, loose: bool = False) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).lower().strip()
    # Translation/language suffixes differ across mirrors and should not split
    # an otherwise identical Japanese work. Keep ordinary title brackets.
    text = re.sub(r"\[[^\]]*(?:english|chinese|japanese|translated|translation|digital|英語|中国語|日本語)[^\]]*\]", " ", text, flags=re.I)
    text = re.sub(r"\([^)]*(?:english|chinese|japanese|translated|translation|英語|中国語|日本語)[^)]*\)", " ", text, flags=re.I)
    if loose:
        text = re.sub(r"^(?:\([^)]{1,60}\)|\[[^\]]{1,60}\])\s*", "", text)
    return "".join(ch for ch in text if ch.isalnum())


def _creator_keys(item: dict[str, Any]) -> set[str]:
    vals = list(item.get("artists") or []) + list(item.get("groups") or [])
    return {_dedupe_text(v) for v in vals if len(_dedupe_text(v)) >= 2}


def _same_work(a: dict[str, Any], b: dict[str, Any]) -> bool:
    if a.get("source") == b.get("source"):
        return False
    ta = _dedupe_text(a.get("title_jp") or a.get("title") or "")
    tb = _dedupe_text(b.get("title_jp") or b.get("title") or "")
    la = _dedupe_text(a.get("title_jp") or a.get("title") or "", loose=True)
    lb = _dedupe_text(b.get("title_jp") or b.get("title") or "", loose=True)
    has_jp = bool(re.search(r"[ぁ-んァ-ン一-龯々〆ヵヶ]", str(a.get("title_jp") or a.get("title") or "") + str(b.get("title_jp") or b.get("title") or "")))
    exact_min = 4 if has_jp else 8
    loose_min = 6 if has_jp else 14
    exact = bool(ta and ta == tb and len(ta) >= exact_min)
    loose = bool(la and la == lb and len(la) >= loose_min)
    if not (exact or loose):
        return False

    creators_overlap = bool(_creator_keys(a) & _creator_keys(b))
    pa, pb = int(a.get("pages") or 0), int(b.get("pages") or 0)
    pages_close = bool(pa and pb and abs(pa - pb) <= 2)
    # Long exact titles are already a strong signal; shorter/common titles need
    # creator or page corroboration to avoid false merges.
    strong_exact = exact and len(ta) >= (10 if has_jp else 22)
    return creators_overlap or pages_close or strong_exact


def apply_duplicate_groups(items: list[dict[str, Any]]) -> int:
    """Annotate cross-source duplicates without destructively merging rows.

    The raw records remain intact. The frontend can collapse members into one
    card and still expose every original source link.
    """
    for item in items:
        for k in ("duplicate_group", "duplicate_count", "duplicate_sources", "duplicate_uids"):
            item.pop(k, None)

    n = len(items)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    # Compare only inside exact/loose normalized title buckets, avoiding O(n^2)
    # across the full library.
    buckets: dict[str, list[int]] = {}
    for i, item in enumerate(items):
        title = item.get("title_jp") or item.get("title") or ""
        for prefix, key in (("s:", _dedupe_text(title)), ("l:", _dedupe_text(title, loose=True))):
            min_len = 4 if re.search(r"[ぁ-んァ-ン一-龯々〆ヵヶ]", str(title)) else 8
            if len(key) >= min_len:
                buckets.setdefault(prefix + key, []).append(i)

    for ids in buckets.values():
        if len(ids) < 2:
            continue
        # Buckets are normally tiny; cap pathological generic-title buckets.
        ids = ids[:40]
        for pos, a in enumerate(ids):
            for b in ids[pos + 1:]:
                if _same_work(items[a], items[b]):
                    union(a, b)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)

    duplicate_groups = 0
    for member_ids in groups.values():
        if len(member_ids) < 2:
            continue
        # Only call it a duplicate group if at least two different sources exist.
        sources = sorted({str(items[i].get("source") or "") for i in member_ids if items[i].get("source")})
        if len(sources) < 2:
            continue
        duplicate_groups += 1
        members = [items[i] for i in member_ids]
        title_key = _dedupe_text(members[0].get("title_jp") or members[0].get("title") or "")
        creator_pool = sorted(set().union(*(_creator_keys(x) for x in members)))
        nonzero_pages = sorted(int(x.get("pages") or 0) for x in members if int(x.get("pages") or 0) > 0)
        discriminator = creator_pool[0] if creator_pool else (f"p{nonzero_pages[0]}" if nonzero_pages else "")
        gid = "dup:" + hashlib.sha1(f"{title_key}|{discriminator}".encode("utf-8")).hexdigest()[:16]
        uids = sorted(str(x.get("uid") or "") for x in members if x.get("uid"))
        for item in members:
            item["duplicate_group"] = gid
            item["duplicate_count"] = len(members)
            item["duplicate_sources"] = sources
            item["duplicate_uids"] = uids
    return duplicate_groups


def main() -> int:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = now_iso()

    data = load_json(DATA_FILE, {"items": []})
    existing_items = [
        upgrade_item_schema(x) for x in data.get("items", [])
        if isinstance(x, dict) and x.get("uid") and x.get("source") not in RETIRED_SOURCES
    ]
    # New exclusion rules also apply to retained data, so old insect/bug rows
    # disappear on the first run after this revision rather than waiting until
    # they happen to be rediscovered.
    retained_items: list[dict[str, Any]] = []
    purged_existing_insect = 0
    for item in existing_items:
        if insect_block_reason(item):
            purged_existing_insect += 1
            continue
        retained_items.append(item)
    existing = {x["uid"]: x for x in retained_items}

    state = load_json(STATE_FILE, {})
    status_store = load_json(STATUS_FILE, {})
    if not isinstance(status_store, dict):
        status_store = {}
    status_store.pop("updated_at", None)

    for retired in RETIRED_SOURCES:
        state.pop(retired, None)
        status_store.pop(retired, None)

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

    duplicate_group_count = apply_duplicate_groups(items)
    duplicate_item_count = sum(1 for x in items if x.get("duplicate_group"))

    save_json(DATA_FILE, {
        "updated_at": stamp,
        "item_count": len(items),
        "duplicate_group_count": duplicate_group_count,
        "duplicate_item_count": duplicate_item_count,
        "purged_existing_insect": purged_existing_insect,
        "items": items,
    })
    save_json(STATE_FILE, state)
    status_store["filters"] = {
        "insect_filter": "enabled",
        "insect_terms": len(NORMALIZED_INSECT_TAGS),
        "purged_existing_insect": purged_existing_insect,
    }
    save_json(STATUS_FILE, {**status_store, "updated_at": stamp})

    print(f"done: raw={total_raw}, accepted={total_accepted}, total={len(items)}, duplicate_groups={duplicate_group_count}")
    if successful_sources == 0 and not items:
        print("All sources failed and there is no retained dataset.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
