#!/usr/bin/env python3
import hashlib
import json
import re
import sys
import unicodedata
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from config import (
    ALLOWED_LANGUAGES,
    BLOCK_FULL_TAGS,
    BLOCK_INSECT_TAGS,
    BLOCK_TAGS,
    BLOCK_TAG_ALIASES,
    DATA_FILE,
    DOCS_DIR,
    KEEP_ITEMS,
    STATE_FILE,
    STATUS_FILE,
    FILTER_STATE_FILE,
    HITOMI_FILTER_AUDIT_LIMIT,
    HITOMI_FILTER_AUDIT_DELAY_SEC,
    HITOMI_FILTER_AUDIT_REFRESH_SEC,
    HITOMI_FILTER_RETRY_SEC,
)
from sources import ehentai, hitomi, hentai3, asmhentai
from sources.common import normalize_full_tag, normalize_tag, unique_strings, polite_sleep

SOURCES = {
    "ehentai": ehentai.collect,
    "hitomi": hitomi.collect,
    "3hentai": hentai3.collect,
    "asmhentai": asmhentai.collect,
}
RETIRED_SOURCES = {"pururin", "nharchive", "nhentai"}

NORMALIZED_BLOCK_TAGS = {normalize_tag(x) for x in BLOCK_TAGS}
NORMALIZED_BLOCK_FULL_TAGS = {normalize_full_tag(x) for x in BLOCK_FULL_TAGS}
NORMALIZED_BLOCK_ALIASES = {normalize_tag(k): normalize_tag(v) for k, v in BLOCK_TAG_ALIASES.items()}
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
        canonical = NORMALIZED_BLOCK_ALIASES.get(base, base)
        if canonical in NORMALIZED_BLOCK_TAGS:
            return f"blocked:{canonical}"

    # Hitomi occasionally exposes a content warning/descriptor only in the
    # gallery title while its structured tag list omits the same term. Scan
    # titles as a second line of defence. English block terms use token
    # boundaries so short terms such as "bl" cannot match ordinary words.
    title_text = unicodedata.normalize(
        "NFKC", f"{item.get('title', '')} {item.get('title_jp', '')}"
    ).lower().replace("’", "'").replace("‘", "'")
    for term in sorted(NORMALIZED_BLOCK_TAGS | NORMALIZED_BLOCK_ALIASES.keys(), key=lambda x: (-len(x), x)):
        if not term:
            continue
        if term == "グロ":
            # Do not mistake マグロ / グローバル for a content warning.
            matched = bool(re.search(r"(?<![ァ-ヶー])グロ(?![ァ-ヶー])", title_text))
        elif re.search(r"[ぁ-んァ-ン一-龯々〆ヵヶ蟲]", term):
            matched = term in title_text
        else:
            matched = bool(re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", title_text))
        if matched:
            return f"blocked:title:{NORMALIZED_BLOCK_ALIASES.get(term, term)}"

    # Hitomi occasionally exposes a content warning/descriptor only in the
    # gallery title while its structured tag list omits the same term. Scan
    # titles as a second line of defence. English block terms use token
    # boundaries so short terms such as "bl" cannot match ordinary words.
    title_text = unicodedata.normalize(
        "NFKC", f"{item.get('title', '')} {item.get('title_jp', '')}"
    ).lower()
    for term in sorted(NORMALIZED_BLOCK_TAGS, key=len, reverse=True):
        if not term:
            continue
        if re.search(r"[ぁ-んァ-ン一-龯々〆ヵヶ蟲]", term):
            matched = term in title_text
        else:
            matched = bool(re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", title_text))
        if matched:
            return f"blocked:title:{term}"

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
        "source_url_kind": str(item.get("source_url_kind") or ""),
        "resolved_gallery_id": str(item.get("resolved_gallery_id") or ""),
        "filter_metadata_checked": str(item.get("filter_metadata_checked") or ""),
        "filter_metadata_checked_at": str(item.get("filter_metadata_checked_at") or ""),
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


def unverified_hitomi(item: dict[str, Any], *, fresh: bool = False) -> bool:
    if item.get("source") != "hitomi":
        return False
    if not item.get("tags") or not (item.get("title") or item.get("title_jp")) or not item.get("language"):
        return True
    return fresh and item.get("filter_metadata_checked") != hitomi.FILTER_METADATA_VERSION


def publication_reason(item: dict, *, fresh: bool = False) -> str:
    if item.get("source") == "hitomi" and (
        not item.get("language") or not (item.get("title") or item.get("title_jp"))
    ):
        return "metadata_unverified"
    return blocked_reason(item) or ("metadata_unverified" if unverified_hitomi(item, fresh=fresh) else "")


def quarantine_item(store: dict, item: dict, reason: str, stamp: str) -> None:
    uid = item.get("uid")
    if not uid:
        return
    records = store.setdefault("records", {})
    old = records.get(uid) or {}
    # Missing tags on a later response must not erase a confirmed exclusion.
    if old.get("status") == "blocked":
        return
    records[uid] = {"item": dict(item), "reason": reason,
                    "status": "pending" if reason == "metadata_unverified" else "blocked",
                    "first_quarantined_at": old.get("first_quarantined_at") or stamp,
                    "updated_at": stamp}


def _timestamp(value: str) -> float:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return 0


def audit_existing_hitomi(existing: dict, store: dict, stamp: str, preferred_base: str = "") -> dict:
    """Bounded, resumable audit; missing tags are checked before older records."""
    records = store.setdefault("records", {})
    attempts = store.setdefault("hitomi_attempts", {})
    now = _timestamp(stamp)
    pool = {uid: item for uid, item in existing.items() if item.get("source") == "hitomi"}
    pool.update({uid: rec["item"] for uid, rec in records.items()
                 if rec.get("status") == "pending" and rec.get("item", {}).get("source") == "hitomi"})
    candidates = []
    for uid, item in pool.items():
        if records.get(uid, {}).get("status") == "blocked":
            continue
        checked = _timestamp(item.get("filter_metadata_checked_at", ""))
        if (records.get(uid, {}).get("status") != "pending"
                and item.get("filter_metadata_checked") == hitomi.FILTER_METADATA_VERSION
                and checked and now - checked < HITOMI_FILTER_AUDIT_REFRESH_SEC):
            continue
        if float(attempts.get(uid, {}).get("retry_at") or 0) > now:
            continue
        candidates.append(item)
    candidates.sort(key=lambda x: (
        0 if records.get(x["uid"], {}).get("status") == "pending" or not x.get("tags") else 1,
        float(attempts.get(x["uid"], {}).get("last_attempt") or 0),
        _timestamp(x.get("filter_metadata_checked_at", "")),
        x.get("first_seen") or "", x["uid"],
    ))
    stats = {"last_run": stamp, "eligible": len(candidates), "attempted": 0,
             "verified": 0, "restored": 0, "blocked": 0, "unverified": 0,
             "failed": 0, "failed_reasons": {}, "stopped": False}
    if float(store.get("hitomi_host_retry_at") or 0) > now:
        stats["stopped"] = True
        stats["host_cooldown"] = True
        return stats
    consecutive_errors = 0
    with hitomi.requests.Session() as session:
        for item in candidates[:max(0, HITOMI_FILTER_AUDIT_LIMIT)]:
            uid = item["uid"]
            stats["attempted"] += 1
            prior = attempts.get(uid) or {}
            try:
                fresh = hitomi.fetch_filter_metadata(session, int(item["source_id"]), preferred_base)
                if fresh.get("uid") != uid:
                    raise ValueError("Hitomi audit identity mismatch")
                consecutive_errors = 0
                merged = merge_item(item, fresh, stamp)
                reason = publication_reason(fresh, fresh=True)
                if reason:
                    existing.pop(uid, None)
                    quarantine_item(store, merged, reason, stamp)
                    stats["unverified" if reason == "metadata_unverified" else "blocked"] += 1
                    attempts[uid] = {"last_attempt": now, "retry_at": now + HITOMI_FILTER_RETRY_SEC}
                else:
                    # Set the audit's own clock; fixtures and source timestamps
                    # must not make a successful row immediately stale.
                    merged["filter_metadata_checked_at"] = stamp
                    stats["restored"] += uid not in existing
                    existing[uid] = merged
                    records.pop(uid, None)
                    attempts.pop(uid, None)
                    stats["verified"] += 1
            except Exception as exc:
                match = re.search(r"HTTP (\d{3})\b", str(exc))
                status = int(match.group(1)) if match else None
                transport_error = (isinstance(exc, (hitomi.requests.Timeout, hitomi.requests.ConnectionError, TimeoutError))
                                   or str(exc).startswith("request failed url="))
                reason = f"http_{status}" if status else ("transport" if transport_error else "metadata_invalid")
                host_failure = transport_error or (status is not None and status >= 500)
                consecutive_errors = consecutive_errors + 1 if host_failure else 0
                failures = int(prior.get("failures") or 0) + 1
                attempts[uid] = {"last_attempt": now, "failures": failures,
                                 "reason": reason,
                                 "retry_at": now + min(HITOMI_FILTER_RETRY_SEC * 2 ** min(failures - 1, 3), 3 * 86400)}
                stats["failed"] += 1
                stats["failed_reasons"][reason] = stats["failed_reasons"].get(reason, 0) + 1
                # Respect access/rate limits, and stop an unavailable host
                # before spending a timeout on every saved work. A removed
                # gallery (404/410) or malformed record is not a host outage.
                if status in (403, 429) or consecutive_errors >= 3:
                    store["hitomi_host_retry_at"] = now + HITOMI_FILTER_RETRY_SEC
                    store["hitomi_host_retry_reason"] = reason
                    stats["stopped"] = True
                    break
            if stats["attempted"] % 25 == 0:
                # Checkpoint source results, including safe restorations. The
                # final public catalog is published only at the end of main().
                store["verified_checkpoints"] = {uid: value for uid, value in existing.items()
                    if value.get("source") == "hitomi" and value.get("filter_metadata_checked_at") == stamp}
                save_json(FILTER_STATE_FILE, store)
            polite_sleep(HITOMI_FILTER_AUDIT_DELAY_SEC)
    return stats


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


def main(*, audit_only: bool = False) -> int:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = now_iso()

    data = load_json(DATA_FILE, {"items": []})
    state = load_json(STATE_FILE, {})
    filter_store = load_json(FILTER_STATE_FILE, {})
    for key in ("records", "hitomi_attempts", "verified_checkpoints"):
        if not isinstance(filter_store.get(key), dict):
            filter_store[key] = {}
    records = filter_store["records"]
    existing_items = [
        upgrade_item_schema(x) for x in data.get("items", [])
        if isinstance(x, dict) and x.get("uid") and x.get("source") not in RETIRED_SOURCES
    ]
    # Recover verified audit results if a previous job stopped after a
    # checkpoint but before writing the final catalog.
    existing_map = {x["uid"]: x for x in existing_items}
    for uid, saved in filter_store["verified_checkpoints"].items():
        if (records.get(uid, {}).get("status") != "blocked"
                and saved.get("filter_metadata_checked") == hitomi.FILTER_METADATA_VERSION
                and not unverified_hitomi(saved, fresh=True) and not blocked_reason(saved)):
            existing_map[uid] = merge_item(existing_map.get(uid), saved, stamp)
            records.pop(uid, None)
    existing_items = list(existing_map.values())
    # Revision 19: 3Hentai historically had little/no tag metadata, so old
    # visible rows could not be evaluated by the common BL/guro/ryona/insect
    # filters.  Audit a bounded number of saved 3Hentai galleries every run,
    # then apply the same common filter to *all* retained rows.  This gradually
    # cleans the existing site without a one-off destructive reset.
    try:
        h3_existing_audit = ({"skipped": "hitomi-filter-audit-only"} if audit_only else
                             hentai3.enrich_existing_for_filter(existing_items))
    except Exception as e:
        h3_existing_audit = {
            "existing_filter_audit_mode": "public-gallery-metadata",
            "existing_filter_audit_attempted": 0,
            "existing_filter_audit_enriched": 0,
            "existing_filter_audit_resolved_search": 0,
            "existing_filter_audit_failed": 0,
            "existing_filter_audit_pending": sum(1 for x in existing_items if x.get("source") == "3hentai"),
            "existing_filter_audit_stopped": True,
            "existing_filter_audit_samples": [],
            "existing_filter_audit_failures": [str(e)[:500]],
        }

    retained_items: list[dict[str, Any]] = []
    purged_existing_blocked = 0
    purged_existing_reasons: dict[str, int] = {}
    purged_existing_by_source: dict[str, int] = {}
    purged_existing_insect = 0
    purged_existing_3hentai = 0
    held_existing_unverified = 0
    for item in existing_items:
        rec = records.get(item["uid"], {})
        reason = (rec.get("reason") if rec.get("status") == "blocked" else publication_reason(item))
        if not reason and (unverified_hitomi(item) or rec.get("status") == "pending"):
            reason = "metadata_unverified"
        if reason:
            quarantine_item(filter_store, item, reason, stamp)
            if reason == "metadata_unverified":
                held_existing_unverified += 1
                continue
            purged_existing_blocked += 1
            purged_existing_reasons[reason] = purged_existing_reasons.get(reason, 0) + 1
            src = str(item.get("source") or "unknown")
            purged_existing_by_source[src] = purged_existing_by_source.get(src, 0) + 1
            if reason.startswith("blocked:insect:"):
                purged_existing_insect += 1
            if src == "3hentai":
                purged_existing_3hentai += 1
            continue
        retained_items.append(item)
    existing = {x["uid"]: x for x in retained_items}

    hitomi_audit = audit_existing_hitomi(existing, filter_store, stamp,
                                        (state.get("hitomi") or {}).get("resource_base", ""))
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

    for name, collector in ([] if audit_only else SOURCES.items()):
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
            # 3Hentai is fail-closed for newly discovered rows: if the public
            # gallery page did not yield filter-relevant metadata, do not add
            # the item yet. It can be discovered again on a later healthy run.
            uid = raw["uid"]
            saved_block = records.get(uid, {})
            if saved_block.get("status") == "blocked":
                reason = saved_block["reason"]
            elif name == "3hentai" and raw.get("filter_metadata_checked") != "3hentai-gallery-v1":
                reason = "metadata_unverified"
            else:
                reason = publication_reason(raw, fresh=True)
            if reason:
                blocked += 1
                blocked_reasons[reason] = blocked_reasons.get(reason, 0) + 1
                # Rejecting this response alone leaves the old catalog row
                # visible. Remove it too and retain evidence outside docs.
                prior = existing.pop(uid, None) or records.get(uid, {}).get("item")
                quarantine_item(filter_store, merge_item(prior, raw, stamp), reason, stamp)
                continue
            prior = existing.get(uid) or records.get(uid, {}).get("item")
            merged = merge_item(prior, raw, stamp)
            reason = blocked_reason(merged)
            if reason:
                existing.pop(uid, None)
                quarantine_item(filter_store, merged, reason, stamp)
                blocked += 1
                blocked_reasons[reason] = blocked_reasons.get(reason, 0) + 1
                continue
            existing[uid] = merged
            records.pop(uid, None)
            filter_store["hitomi_attempts"].pop(uid, None)
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

    if isinstance(status_store.get("3hentai"), dict):
        status_store["3hentai"].update(h3_existing_audit)
        status_store["3hentai"]["purged_existing_this_run"] = purged_existing_3hentai

    items = sorted(existing.values(), key=sort_key, reverse=True)
    if KEEP_ITEMS > 0:
        items = items[:KEEP_ITEMS]

    duplicate_group_count = apply_duplicate_groups(items)
    duplicate_item_count = sum(1 for x in items if x.get("duplicate_group"))

    # Final defense applies after every merge, not just to incoming responses.
    assert not any(blocked_reason(x) or unverified_hitomi(x) for x in items), "Unsafe catalog row"
    pending_counts: dict[str, int] = {}
    blocked_counts: dict[str, int] = {}
    for rec in records.values():
        counts = pending_counts if rec.get("status") == "pending" else blocked_counts
        source = rec.get("item", {}).get("source") or "unknown"
        counts[source] = counts.get(source, 0) + 1
    hitomi_audit["remaining_source_rechecks"] = sum(
        x.get("source") == "hitomi" and (
            x.get("filter_metadata_checked") != hitomi.FILTER_METADATA_VERSION
            or _timestamp(stamp) - _timestamp(x.get("filter_metadata_checked_at", "")) >= HITOMI_FILTER_AUDIT_REFRESH_SEC
        ) for x in items
    ) + pending_counts.get("hitomi", 0)
    filter_store["updated_at"] = stamp
    filter_store["last_audit"] = hitomi_audit
    filter_store["verified_checkpoints"] = {x["uid"]: x for x in items
        if x.get("source") == "hitomi" and x.get("filter_metadata_checked_at") == stamp}
    save_json(FILTER_STATE_FILE, filter_store)

    save_json(DATA_FILE, {
        "updated_at": stamp,
        "item_count": len(items),
        "duplicate_group_count": duplicate_group_count,
        "duplicate_item_count": duplicate_item_count,
        "purged_existing_insect": purged_existing_insect,
        "purged_existing_blocked": purged_existing_blocked,
        "purged_existing_reasons": purged_existing_reasons,
        "purged_existing_by_source": purged_existing_by_source,
        "purged_existing_3hentai": purged_existing_3hentai,
        "held_existing_unverified": held_existing_unverified,
        "items": items,
    })
    save_json(STATE_FILE, state)
    status_store["filters"] = {
        "content_filters": "enabled",
        "insect_filter": "enabled",
        "insect_terms": len(NORMALIZED_INSECT_TAGS),
        "purged_existing_blocked": purged_existing_blocked,
        "purged_existing_insect": purged_existing_insect,
        "purged_existing_3hentai": purged_existing_3hentai,
        "purged_existing_reasons": purged_existing_reasons,
        "purged_existing_by_source": purged_existing_by_source,
        "3hentai_existing_audit": h3_existing_audit,
        "hitomi_existing_audit": hitomi_audit,
        "quarantined_pending_by_source": pending_counts,
        "quarantined_blocked_by_source": blocked_counts,
        "held_existing_unverified": held_existing_unverified,
    }
    save_json(STATUS_FILE, {**status_store, "updated_at": stamp})
    filter_store["verified_checkpoints"] = {}
    save_json(FILTER_STATE_FILE, filter_store)

    print(f"done: raw={total_raw}, accepted={total_accepted}, total={len(items)}, duplicate_groups={duplicate_group_count}")
    print(f"[Hitomi filter audit] {hitomi_audit}")
    print(f"[Filter quarantine] pending={pending_counts} blocked={blocked_counts}")
    if not audit_only and successful_sources == 0 and not items:
        print("All sources failed and there is no retained dataset.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(audit_only="--audit-filters-only" in sys.argv))
