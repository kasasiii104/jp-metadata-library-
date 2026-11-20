#!/usr/bin/env python3
import copy
import hashlib
import html
import re
import sys
import unicodedata
from datetime import datetime, timezone, timedelta
from typing import Any

from config import (
    ALLOWED_LANGUAGES,
    BLOCK_FULL_TAGS,
    BLOCK_INSECT_TAGS,
    BLOCK_TAGS,
    BLOCK_TAG_ALIASES,
    BLOCK_TAG_ONLY_TAGS,
    CHINESE_BLOCK_BILINGUAL,
    CHINESE_TITLE_MARKERS,
    CHINESE_TITLE_LABELS,
    JAPANESE_TITLE_LABELS,
    CHINESE_SIMPLIFIED_CHARS,
    CHINESE_SIMPLIFIED_MIN_DISTINCT,
    NH_EXISTING_TAG_AUDIT_LIMIT,
    DATA_FILE,
    HENTAI3_ID_MAP_FILE,
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
from sources import ehentai, hitomi, hentai3, asmhentai, hentai3_ids, nhentai
from sources.net_policy import URLPolicyError
from sources.common import is_valid_source_id, normalize_full_tag, normalize_tag, unique_strings, polite_sleep
from tools.json_storage import (load_json, save_json, validate_catalog,
                                validate_source_mapping, validate_filter_store, validate_hentai3_id_map)

SOURCES = {
    "ehentai": ehentai.collect,
    "hitomi": hitomi.collect,
    "3hentai": hentai3.collect,
    "asmhentai": asmhentai.collect,
    "nhentai": nhentai.collect,
}
RETIRED_SOURCES = {"pururin", "nharchive"}

NORMALIZED_BLOCK_TAG_ONLY_TAGS = {normalize_tag(x) for x in BLOCK_TAG_ONLY_TAGS}
NORMALIZED_BLOCK_TAGS = {normalize_tag(x) for x in BLOCK_TAGS}
NORMALIZED_BLOCK_FULL_TAGS = {normalize_full_tag(x) for x in BLOCK_FULL_TAGS}
NORMALIZED_BLOCK_ALIASES = {normalize_tag(k): normalize_tag(v) for k, v in BLOCK_TAG_ALIASES.items()}
NORMALIZED_INSECT_TAGS = {normalize_tag(x) for x in BLOCK_INSECT_TAGS}
ALLOWED_LANGUAGE_SET = {str(x).lower() for x in ALLOWED_LANGUAGES}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


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


EHENTAI_NON_H_REASON = "blocked:category:ehentai:non-h"
R18G_REASON = "blocked:r18g"
R18G_PATTERN = re.compile(r"(?<![a-z0-9])r[\s_\-‐‑‒–—−]*18[\s_\-‐‑‒–—−]*g(?![a-z0-9])")
IDENTITY_TAG_NAMESPACES = {"artist", "group", "parody", "character", "work", "series", "language", "cosplayer", "uploader"}


def _r18g_text(value: Any) -> str:
    value = unicodedata.normalize("NFKC", html.unescape(str(value or ""))).casefold()
    return re.sub(r"[\u200b\u200c\u200d\u2060\ufeff]", "", value)


def r18g_match_fields(item: dict[str, Any]) -> list[str]:
    """Explicit content labels only; no image analysis or creator-name matching."""
    fields = [name for name in ("category", "title", "title_jp")
              if R18G_PATTERN.search(_r18g_text(item.get(name)))]
    tags = item.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]
    for raw in tags:
        tag = _r18g_text(raw)
        namespace, separator, value = tag.partition(":")
        if separator and namespace.strip() in IDENTITY_TAG_NAMESPACES:
            continue
        if R18G_PATTERN.search(value if separator else tag):
            fields.append("tags")
            break
    return fields


def is_ehentai_non_h(item: dict[str, Any]) -> bool:
    """Match only E-Hentai's category, never titles, tags or other sources."""
    source = str(item.get("source") or "").strip().casefold()
    if source != "ehentai":
        return False
    category = unicodedata.normalize("NFKC", str(item.get("category") or "")).casefold()
    category = re.sub(r"[\s_\-‐‑‒–—−]+", "", category)
    return category == "nonh"


TAG_GENDER_SUFFIX = re.compile(r"\s*\((female|male|mixed|other)\)$")
TITLE_BRACKET_ENDS = {"[": "]", "(": ")", "【": "】"}
TITLE_KANA = re.compile(r"[ぁ-ゖァ-ヺゝゞヽヾㇰ-ㇿ]")
CHINESE_MARKER_REASON = "blocked:language:chinese:marker"
CHINESE_SIMPLIFIED_REASON = "blocked:language:chinese:simplified"


def _title_brackets(title):
    stack = []
    spans = {}
    for index, char in enumerate(title):
        if char in TITLE_BRACKET_ENDS:
            stack.append((index, TITLE_BRACKET_ENDS[char]))
        elif stack and char == stack[-1][1]:
            start, _ = stack.pop()
            spans[start] = index + 1
    return spans


TITLE_LABEL_SEPARATOR = re.compile(r"[,、/&・+\s]+|\band\b")
JAPANESE_LABEL_WORD = re.compile(r"\bjapanese\b")
CHINESE_LABEL_WORD = re.compile(r"\bchinese\b")


def _is_japanese_label(label):
    return (label in JAPANESE_TITLE_LABELS
            or any(token in JAPANESE_TITLE_LABELS for token in TITLE_LABEL_SEPARATOR.split(label))
            or bool(JAPANESE_LABEL_WORD.search(label)))


def _is_chinese_label(label):
    if "".join(label.split()) in CHINESE_TITLE_LABELS:
        return True
    return _is_japanese_label(label) and (
        any(token in CHINESE_TITLE_LABELS for token in TITLE_LABEL_SEPARATOR.split(label))
        or bool(CHINESE_LABEL_WORD.search(label))
    )


def _chinese_title_fields(item):
    for name in ("title", "title_jp"):
        title = unicodedata.normalize("NFKC", html.unescape(str(item.get(name) or ""))).strip().casefold()
        brackets = _title_brackets(title)
        labels = [title[start + 1:end - 1] for start, end in brackets.items()]
        yield title, brackets, labels


def _title_body_start(title, brackets):
    start = 0
    if brackets.get(0) != len(title):
        while start in brackets:
            start = brackets[start]
            while start < len(title) and title[start].isspace():
                start += 1
    return start


def chinese_title_reason(item: dict[str, Any]) -> str:
    fields = list(_chinese_title_fields(item))
    if not CHINESE_BLOCK_BILINGUAL and any(
        _is_japanese_label(label) for _, _, labels in fields for label in labels
    ):
        return ""

    simplified = False
    for title, brackets, labels in fields:
        if (any(marker in title for marker in CHINESE_TITLE_MARKERS)
                or any(_is_chinese_label(label) for label in labels)):
            return CHINESE_MARKER_REASON
        if TITLE_KANA.search(title):
            continue

        start = _title_body_start(title, brackets)
        if len(CHINESE_SIMPLIFIED_CHARS.intersection(title[start:])) >= CHINESE_SIMPLIFIED_MIN_DISTINCT:
            simplified = True

    return CHINESE_SIMPLIFIED_REASON if simplified else ""


def blocked_reason(item: dict[str, Any]) -> str:
    if r18g_match_fields(item):
        return R18G_REASON
    if is_ehentai_non_h(item):
        return EHENTAI_NON_H_REASON
    if normalized_language(item.get("language", "")) != "japanese":
        return "language"

    tags = item.get("tags") or []
    tags = [normalize_full_tag(tag) for tag in ([tags] if isinstance(tags, str) else tags)]
    candidates = list(tags)
    if item.get("category"):
        candidates.append(normalize_full_tag(item["category"]))

    hits = []
    for index, full in enumerate(candidates):
        base = normalize_tag(full.split(":", 1)[-1])
        if full in NORMALIZED_BLOCK_FULL_TAGS:
            hits.append((NORMALIZED_BLOCK_ALIASES.get(base, base), full))
        stripped = TAG_GENDER_SUFFIX.sub("", base).removeprefix("low ")
        for variant in (base, stripped):
            canonical = NORMALIZED_BLOCK_ALIASES.get(variant, variant)
            if (canonical in NORMALIZED_BLOCK_TAGS
                    or (index < len(tags) and canonical in NORMALIZED_BLOCK_TAG_ONLY_TAGS)):
                hits.append((canonical, canonical))

    if hits:
        if (any(canonical == "shotacon" for canonical, _ in hits)
                and not any(canonical in {"males only", "yaoi"} | NORMALIZED_BLOCK_TAG_ONLY_TAGS
                            for canonical, _ in hits)
                and any(tag.startswith("female:") or tag.endswith(" (female)")
                        or tag in {"sole female", "ffm threesome", "lolicon"} for tag in tags)):
            hits = [(canonical, reason) for canonical, reason in hits if canonical != "shotacon"]
        if hits:
            return f"blocked:{hits[0][1]}"

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
        if term in {"グロ", "ショタコン"}:
            # Do not mistake マグロ / グローバル for a content warning.
            matched = bool(re.search(rf"(?<![ァ-ヶー]){re.escape(term)}(?![ァ-ヶー])", title_text))
        elif re.search(r"[ぁ-んァ-ン一-龯々〆ヵヶ蟲]", term):
            matched = term in title_text
        else:
            matched = bool(re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", title_text))
        if matched:
            return f"blocked:title:{NORMALIZED_BLOCK_ALIASES.get(term, term)}"

    chinese = chinese_title_reason(item)
    if chinese:
        return chinese

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
        **({"resolved_gallery_id": str(item.get("resolved_gallery_id") or "")}
           if not item.get("repair_evidence") else {}),
        **({"public_gallery_id": str(item.get("public_gallery_id") or "")}
           if not item.get("repair_evidence") or item.get("repair_evidence") == "payload_link" else {}),
        **{key: item[key] for key in ("repair_evidence", "repair_evidence_at") if key in item},
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
    if merged.get("repair_evidence"):
        merged.pop("resolved_gallery_id", None)
        if merged["repair_evidence"] == "detail_id":
            merged.pop("public_gallery_id", None)
    return merged


def unverified_hitomi(item: dict[str, Any], *, fresh: bool = False) -> bool:
    if item.get("source") != "hitomi":
        return False
    if not item.get("tags") or not (item.get("title") or item.get("title_jp")) or not item.get("language"):
        return True
    return fresh and item.get("filter_metadata_checked") != hitomi.FILTER_METADATA_VERSION


def publication_reason(item: dict, *, fresh: bool = False, fetched: dict | None = None) -> str:
    reason = blocked_reason(item)
    # Saved fields may fill gaps for content filtering, but cannot verify a fetch.
    metadata = item if fetched is None else fetched
    # A confirmed content warning must not become a retryable metadata hold.
    if reason == R18G_REASON:
        return reason
    if item.get("source") == "hitomi" and (
        not metadata.get("language") or not (metadata.get("title") or metadata.get("title_jp"))
    ):
        return "metadata_unverified"
    return reason or ("metadata_unverified" if unverified_hitomi(metadata, fresh=fresh) else "")


def quarantine_item(store: dict, item: dict, reason: str, stamp: str) -> None:
    uid = item.get("uid")
    if not uid:
        return
    records = store.setdefault("records", {})
    old = records.get(uid) or {}
    # Missing tags on a later response must not erase a confirmed exclusion.
    if old.get("status") == "blocked":
        return
    records[uid] = {**{k: old[k] for k in ("repair_original", "hold_detail", "hold_source_url") if k in old}, "item": dict(item), "reason": reason,
                    "status": "pending" if reason == "metadata_unverified" else "blocked",
                    "first_quarantined_at": old.get("first_quarantined_at") or stamp,
                    "updated_at": stamp}

    if item.get("source") == "3hentai":
        records[uid]["hold_source_url"] = old.get("hold_source_url", (old.get("item") or item).get("source_url", ""))

    if (reason == "metadata_unverified" and item.get("source") == "3hentai"
            and item.get("filter_metadata_checked") != "3hentai-gallery-v1"):
        detail = store.get("hentai3_attempts", {}).get(uid, {}).get("reason")
        if not old.get("repair_original") and old.get("hold_detail") not in {
                "3hentai_mislinked_split", "3hentai_url_conflict"}:
            records[uid]["hold_detail"] = "3hentai_unaudited" + (":" + detail if detail else "")


def hentai3_can_restore(item: dict, record: dict) -> bool:
    """Every restoration path must honor the original repair hold."""
    held_url = record.get("hold_source_url", record.get("item", {}).get("source_url", ""))
    held_gid = hentai3.direct_gallery_id(held_url)
    same_gallery = (held_gid and held_gid == hentai3.direct_gallery_id(item.get("source_url", "")))
    identity_repair = bool(record) and record.get("item", {}).get("source") == "3hentai" and not same_gallery
    return (record.get("status") != "blocked"
            and item.get("filter_metadata_checked") == "3hentai-gallery-v1"
            and bool(hentai3.direct_gallery_id(item.get("source_url", "")))
            and (not (hentai3_ids.needs_repair(record) or identity_repair) or item.get("repair_evidence") in {"payload_link", "detail_id"}))


def reconcile_hentai3_input(original, incoming, store, stamp, *, retain_original=False):
    """Keep catalog/checkpoint provenance before an input can replace it."""
    if original.get("source") != "3hentai" or incoming.get("source") != "3hentai":
        return True
    candidate = {"item": original, "status": "pending"}
    hentai3_ids.reconcile_record(incoming, candidate)
    if hentai3_ids.needs_repair(candidate):
        if (retain_original and original["uid"] not in store["records"]
                and incoming.get("repair_evidence") not in {"payload_link", "detail_id"}):
            return False
        quarantine_item(store, original, "metadata_unverified", stamp)
        hentai3_ids.reconcile_record(incoming, store["records"][original["uid"]])
    return True


def _timestamp(value: str) -> float:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return 0


def audit_existing_hitomi(existing: dict, store: dict, stamp: str, preferred_base: str = "",
                          restored_h3_uids: set[str] | None = None,
                          shotacon_backups: dict | None = None) -> dict:
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
                reason = publication_reason(merged, fresh=True, fetched=fresh)
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
            except URLPolicyError:
                stats["stopped"] = True
                stats["failed"] += 1
                stats["failed_reasons"]["url_policy"] = 1
                break
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
                    if (value.get("source") == "hitomi" and value.get("filter_metadata_checked_at") == stamp)
                    or uid in (restored_h3_uids or set())}
                save_json(FILTER_STATE_FILE, {
                    **store, "records": {**(shotacon_backups or {}), **records},
                })
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


HENTAI3_TERMINAL_REPAIR_FAILURES = {
    "repair_uncorroborated", "repair_title_mismatch", "repair_no_page_title",
    "repair_payload_link_conflict", "repair_candidate_claimed", "repair_invalid_candidate",
    "repair_invalid_internal_id", "repair_candidate_http_404",
}
HENTAI3_PERMANENT_FAILURE_LIMIT = 3
HENTAI3_PERMANENT_FAILURES = {"search_no_match", "search_ambiguous",
                              "gallery_no_evidence", "gallery_no_tag_section", "gallery_no_tags", "gallery_http_404"}


NH_TAG_AUDIT_VERSION = "nhentai-tags-v1"


def audit_existing_nhentai(items, records, previous, stamp, run):
    candidates = {}
    for item in items:
        gid = str(item.get("source_id") or "")
        if (item.get("source") == "nhentai" and nhentai._valid_id(gid)
                and item.get("uid") == f"nhentai:{gid}" and not item.get("tags")
                and item.get("filter_metadata_checked") != NH_TAG_AUDIT_VERSION
                and records.get(item["uid"], {}).get("status") != "blocked"
                and not publication_reason(item)):
            candidates[gid] = item
    # Rotate past transiently failing IDs so later rows still get audited.
    cursor = str(previous.get("last_attempted_id") or "")
    ids = sorted(candidates, key=lambda gid: (gid <= cursor, gid))
    tags_by_id, status = nhentai.audit_existing_tags(ids[:max(0, NH_EXISTING_TAG_AUDIT_LIMIT)], run=run)
    status.setdefault("last_attempted_id", cursor)
    for gid, tags in tags_by_id.items():
        item = candidates[gid]
        item.update(tags=tags, filter_metadata_checked=NH_TAG_AUDIT_VERSION,
                    filter_metadata_checked_at=stamp)
        status["excluded"] += bool(publication_reason(item))
    return status


def main(*, audit_only: bool = False) -> int:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = now_iso()

    data = load_json(DATA_FILE, {"items": []}, validate_catalog)
    state = load_json(STATE_FILE, {}, validate_source_mapping)
    filter_store = load_json(FILTER_STATE_FILE, {}, validate_filter_store)
    status_store = load_json(STATUS_FILE, {}, validate_source_mapping)
    for key in ("records", "hitomi_attempts", "hentai3_attempts", "hentai3_repairs", "verified_checkpoints"):
        if not isinstance(filter_store.get(key), dict):
            filter_store[key] = {}
    id_map_path = (HENTAI3_ID_MAP_FILE if HENTAI3_ID_MAP_FILE.parent == DOCS_DIR
                   else DOCS_DIR / HENTAI3_ID_MAP_FILE.name)
    id_map = load_json(id_map_path, hentai3_ids.empty_id_map(), validate_hentai3_id_map)
    # Capture catalog provenance before identity merges or checkpoints can fill URLs.
    for item in data["items"]:
        hentai3_ids.reconcile_record(item, filter_store["records"].get(item.get("uid"), {}))
        if (item.get("source") == "3hentai"
                and (item.get("filter_metadata_checked") != "3hentai-gallery-v1"
                     or not hentai3.direct_gallery_id(item.get("source_url", "")))):
            quarantine_item(filter_store, item, "metadata_unverified", stamp)
            record = filter_store["records"][item["uid"]]
            if record.get("status") == "pending":
                record.setdefault("hold_detail", "3hentai_unaudited")
    data["items"], id_migration = hentai3_ids.migrate(
        data["items"], filter_store, id_map, stamp, merge_item)
    if id_migration["total"]:
        id_map["updated_at"] = stamp
        save_json(id_map_path, id_map)
    # Ambiguous cross-store ownership must not be healed by a checkpoint.
    ambiguous_uids = set(id_migration.pop("held_uids", []))
    for item in data["items"] + list(filter_store["verified_checkpoints"].values()):
        if item["uid"] in ambiguous_uids:
            quarantine_item(filter_store, item, "metadata_unverified", stamp)
            rec = filter_store["records"][item["uid"]]
            if rec.get("status") == "pending" and not hentai3_ids.needs_repair(rec):
                rec.update(hold_source_url="", hold_detail="3hentai_url_conflict")
    records = filter_store["records"]
    existing_items = [
        upgrade_item_schema(x) for x in data.get("items", [])
        if isinstance(x, dict) and x.get("uid") and x.get("source") not in RETIRED_SOURCES
    ]
    # Recover verified audit results if a previous job stopped after a
    # checkpoint but before writing the final catalog.
    existing_map = {x["uid"]: x for x in existing_items}
    restored_h3_uids = set()
    # Freeze hold-time URL shape before checkpoints or audits can mutate items.
    for rec in records.values():
        if rec.get("item", {}).get("source") == "3hentai":
            rec.setdefault("hold_source_url", rec["item"].get("source_url", ""))
    for uid, saved in filter_store["verified_checkpoints"].items():
        original = existing_map.get(uid, {})
        if (saved.get("source") == "3hentai"
                and not hentai3.direct_gallery_id(saved.get("source_url", ""))):
            quarantine_item(filter_store, saved, "metadata_unverified", stamp)
        reconcile_hentai3_input(original, saved, filter_store, stamp)
        merged = merge_item(original, saved, stamp)
        if (records.get(uid, {}).get("status") != "blocked"
                and ((saved.get("source") == "hitomi"
                      and saved.get("filter_metadata_checked") == hitomi.FILTER_METADATA_VERSION
                      and not unverified_hitomi(saved, fresh=True))
                     or (saved.get("source") == "3hentai"
                         and hentai3_can_restore(saved, records.get(uid, {}))))
                and not blocked_reason(merged)):
            existing_map[uid] = merged
            records.pop(uid, None)
            if saved.get("source") == "3hentai":
                restored_h3_uids.add(uid)
    existing_items = list(existing_map.values())
    # Keep the old records in disk checkpoints until the catalog write succeeds.
    shotacon_backups = {}
    # Include eligible 3Hentai restorations in split detection before releasing them.
    shotacon_h3_candidates = {}
    for uid, rec in records.items():
        if rec.get("status") != "blocked" or rec.get("reason") != "blocked:shotacon":
            continue
        saved = rec.get("item") or {}
        item = existing_map.get(uid, saved)
        if (item.get("source") != "3hentai"
                or uid in ambiguous_uids or publication_reason(saved) or publication_reason(item)):
            continue
        if not hentai3_can_restore(item, {**rec, "status": ""}):
            continue
        shotacon_h3_candidates[uid] = existing_map.get(uid) or upgrade_item_schema(item)

    # Revision 19: 3Hentai historically had little/no tag metadata, so old
    # visible rows could not be evaluated by the common BL/guro/ryona/insect
    # filters.  Audit a bounded number of saved 3Hentai galleries every run,
    # then apply the same common filter to *all* retained rows.  This gradually
    # cleans the existing site without a one-off destructive reset.
    combined = {x["uid"]: x for x in existing_items}
    combined.update({uid: rec["item"] for uid, rec in records.items()
                     if rec.get("status") == "pending" and rec.get("item", {}).get("source") == "3hentai"})
    combined.update(shotacon_h3_candidates)
    mislinks, unresolved_splits = hentai3.split_mislinks(list(combined.values()))
    rejected_restorations = mislinks.intersection(shotacon_h3_candidates)
    for uid, item in shotacon_h3_candidates.items():
        if uid in rejected_restorations:
            combined.pop(uid)
            existing_map.pop(uid, None)
            continue
        existing_map[uid] = item
        shotacon_backups[uid] = records.pop(uid)
    existing_items = list(existing_map.values())
    for uid in mislinks - rejected_restorations:
        item = combined[uid]
        fields = ("source_url", "source_url_kind", "resolved_gallery_id", "public_gallery_id",
                  "filter_metadata_checked", "filter_metadata_checked_at", "artists", "groups",
                  "parodies", "works", "characters", "tags", "meta_tags", "category", "thumbnail")
        original = {key: item[key] for key in fields if key in item}
        for key in fields:
            item.pop(key, None)
        item["tags"] = [tag for tag in original.get("tags", []) if tag.startswith("language:")]
        quarantine_item(filter_store, item, "metadata_unverified", stamp)
        records[uid].update(repair_original=original, hold_detail="3hentai_mislinked_split")
        filter_store["verified_checkpoints"].pop(uid, None)
        filter_store["hentai3_attempts"].pop(uid, None)
    existing_items = [combined[x["uid"]] if x["uid"] in mislinks else x for x in existing_items]
    for item in combined.values():
        if (item.get("source") == "3hentai" and hentai3.direct_gallery_id(item.get("source_url", ""))
                and not item.get("source_url_kind")):
            item["source_url_kind"] = "direct"
    claimed = {}
    for item in existing_items:
        gid = hentai3.direct_gallery_id(item.get("source_url", ""))
        sid = str(item.get("source_id", ""))
        if item.get("source") == "3hentai" and gid and sid.isdigit() and int(sid) - 3000000 == int(gid):
            claimed.setdefault(gid, set()).add(item["uid"])
    repair_kinds = {uid: "mislinked_split" for uid, rec in records.items() if rec.get("hold_detail") == "3hentai_mislinked_split"}
    repairs = filter_store["hentai3_repairs"]
    h3_pending = {
        uid: rec["item"] for uid, rec in records.items()
        if rec.get("status") == "pending" and rec.get("item", {}).get("source") == "3hentai"
    }
    h3_targets = {x["uid"]: x for x in existing_items
                  if x.get("source") == "3hentai"
                  and x.get("filter_metadata_checked") != "3hentai-gallery-v1"}
    h3_targets.update(h3_pending)
    h3_attempts = filter_store["hentai3_attempts"]
    for uid, item in h3_targets.items():
        if hentai3.repair_target(item) or "/search" in (item.get("source_url") or ""):
            quarantine_item(filter_store, item, "metadata_unverified", stamp)
            rec = records[uid]
            rec.setdefault("hold_detail", "3hentai_unaudited")
            original = rec.setdefault("repair_original", {})
            for key in ("filter_metadata_checked", "filter_metadata_checked_at"):
                if key in item:
                    original.setdefault(key, item[key])
                    item.pop(key)
            rec["item"] = dict(item)
            h3_pending[uid] = item
        previous = h3_attempts.get(uid, {})
        if hentai3.repair_target(item) and previous.get("gave_up_reason") in {"empty_url", "search_no_match", "search_ambiguous"}:
            previous.clear()
        candidate = str(int(item["source_id"]) - 3000000) if str(item.get("source_id", "")).isdigit() else ""
        if (previous.get("gave_up_reason") == "repair_candidate_claimed"
                and not (claimed.get(candidate, set()) - {uid})):
            previous.clear()
        if "source_url" in previous and previous["source_url"] != (item.get("source_url") or ""):
            for key in ("gave_up", "gave_up_reason", "gave_up_at", "consecutive_failures", "reason"):
                previous.pop(key, None)
    h3_ordered = sorted((x for x in h3_targets.values()
                         if not h3_attempts.get(x["uid"], {}).get("gave_up")), key=lambda x:
                        h3_attempts.get(x["uid"], {}).get("last_attempt", 0))
    try:
        h3_existing_audit = ({"skipped": "hitomi-filter-audit-only"} if audit_only else
                             hentai3.enrich_existing_for_filter(h3_ordered, claimed_gallery_ids=claimed,
                                 repair_kinds=repair_kinds, repairs=repairs))
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

    if not audit_only:
        for uid, result in h3_existing_audit.pop("existing_filter_audit_results", {}).items():
            reason = result["reason"]
            if reason == "ok":
                h3_attempts.pop(uid, None)
                continue
            previous = h3_attempts.get(uid, {})
            consecutive = (previous.get("consecutive_failures", 0) + 1
                           if previous.get("reason") == reason
                           and previous.get("source_url") == result["source_url"] else 1)
            attempt = {"last_attempt": _timestamp(stamp), "id_gen": 2,
                       "failures": previous.get("failures", 0) + 1,
                       **result, "consecutive_failures": consecutive}
            if (reason in HENTAI3_TERMINAL_REPAIR_FAILURES
                    or reason in {"empty_url", "url_policy"}
                    or (reason in HENTAI3_PERMANENT_FAILURES
                        and consecutive >= HENTAI3_PERMANENT_FAILURE_LIMIT)):
                attempt.update(gave_up=True, gave_up_reason=reason, gave_up_at=stamp)
            h3_attempts[uid] = attempt
        restored = blocked = 0
        for uid, item in h3_pending.items():
            if not hentai3_can_restore(item, records.get(uid, {})):
                continue
            reason = publication_reason(item, fresh=True)
            if reason:
                quarantine_item(filter_store, item, reason, stamp)
                blocked += 1
            else:
                records.pop(uid, None)
                existing_items = [x for x in existing_items if x["uid"] != uid]
                existing_items.append(item)
                restored_h3_uids.add(uid)
                filter_store["verified_checkpoints"][uid] = dict(item)
                restored += 1
        h3_existing_audit.update({
            "existing_filter_audit_pending_targets": len(h3_pending),
            "existing_filter_audit_pending_restored": restored,
            "existing_filter_audit_pending_blocked": blocked,
        })

    h3_existing_audit.setdefault("repair_attempted", 0)
    h3_existing_audit.setdefault("repair_get_stopped", False)
    h3_existing_audit.setdefault("repair_get_calls", 0)
    conclusions = {}
    for repair in repairs.values():
        key = repair["conclusion"]
        conclusions[key] = conclusions.get(key, 0) + 1
    h3_existing_audit.update(repair_conclusions=conclusions,
        repair_title_matched=sum(bool(r.get("title_match_ever") or r.get("title_match")) for r in repairs.values()),
        repair_corroborated=sum(bool(r.get("evidence")) for r in repairs.values()),
        mislinked_split_held=len(mislinks), unresolved_split_groups=unresolved_splits)

    # Giving up retries does not verify the row; keep it in the pending count.
    if not audit_only:
        h3_existing_audit["existing_filter_audit_pending"] = sum(
            x.get("filter_metadata_checked") != "3hentai-gallery-v1" or hentai3.repair_target(x)
            or "/search" in (x.get("source_url") or "") for x in h3_targets.values())
    h3_existing_audit.setdefault("existing_filter_audit_reason_counts", {})
    gave_up_counts = {}
    for attempt in h3_attempts.values():
        if attempt.get("gave_up"):
            key = attempt["gave_up_reason"]
            gave_up_counts[key] = gave_up_counts.get(key, 0) + 1
    h3_existing_audit.update(existing_filter_audit_gave_up=sum(gave_up_counts.values()),
                             existing_filter_audit_gave_up_reasons=gave_up_counts)
    for uid, item in h3_pending.items():
        if records.get(uid, {}).get("status") == "pending":
            quarantine_item(filter_store, item, "metadata_unverified", stamp)

    # The audit-only command is Hitomi-specific. Like 3hentai's detail audit,
    # nhentai uses its network budget in normal runs; saved rows always get checked.
    previous_nh_audit = status_store.get("nhentai", {}).get("existing_tag_audit")
    if not isinstance(previous_nh_audit, dict):
        previous_nh_audit = {}
    nh_run = None if audit_only else nhentai.DetailRun()
    nh_existing_audit = ({"skipped": "hitomi-filter-audit-only",
                          "last_attempted_id": previous_nh_audit.get("last_attempted_id", "")}
                         if audit_only else audit_existing_nhentai(
                             existing_items, records, previous_nh_audit, stamp, nh_run))

    current_items = {item["uid"]: item for item in existing_items}
    for uid, rec in list(records.items()):
        if rec.get("status") != "blocked" or rec.get("reason") != "blocked:shotacon":
            continue
        saved = rec.get("item") or {}
        item = current_items.get(uid, saved)
        if (item.get("source") == "3hentai" or item.get("source") in RETIRED_SOURCES
                or uid in ambiguous_uids or publication_reason(saved) or publication_reason(item)):
            continue
        if uid not in current_items:
            existing_items.append(upgrade_item_schema(item))
        shotacon_backups[uid] = records.pop(uid)

    retained_items: list[dict[str, Any]] = []
    purged_existing_blocked = 0
    purged_existing_reasons: dict[str, int] = {}
    purged_existing_by_source: dict[str, int] = {}
    purged_existing_insect = 0
    purged_existing_3hentai = 0
    held_existing_unverified = 0
    held_existing_unaudited = 0
    for item in existing_items:
        rec = records.get(item["uid"], {})
        reason = (rec.get("reason") if rec.get("status") == "blocked" else publication_reason(item))
        unaudited_h3 = (item.get("source") == "3hentai"
                        and item.get("filter_metadata_checked") != "3hentai-gallery-v1")
        if not reason and (unverified_hitomi(item) or unaudited_h3 or rec.get("status") == "pending"):
            reason = "metadata_unverified"
        if reason:
            quarantine_item(filter_store, item, reason, stamp)
            if reason == "metadata_unverified":
                held_existing_unverified += 1
                held_existing_unaudited += int(unaudited_h3)
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
                                        (state.get("hitomi") or {}).get("resource_base", ""),
                                        restored_h3_uids, shotacon_backups)
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
            if name == "nhentai" and nh_existing_audit.get("stopped"):
                raw_items, new_state, result = [], src_state, {
                    "status": "error", "message": "Existing tag audit stopped on access denial",
                }
            elif name == "nhentai" and collector is nhentai.collect:
                raw_items, new_state, result = collector(copy.deepcopy(src_state), run=nh_run)
            else:
                raw_items, new_state, result = collector(copy.deepcopy(src_state))
        except Exception as e:
            raw_items, new_state, result = [], src_state, {"status": "error", "message": str(e)}

        if result.get("status") == "ok":
            state[name] = new_state
        else:
            state[name] = src_state
            raw_items = []
            print(f"[{name}] status={result.get('status')} のため結果を破棄")
        total_raw += len(raw_items)
        accepted = 0
        blocked = 0
        blocked_reasons: dict[str, int] = {}

        for raw in raw_items:
            if name == "3hentai":
                raw = hentai3_ids.normalize_incoming(
                    raw, existing, filter_store, id_map, stamp, merge_item, id_migration)
                # Fold can create a hold after the retained catalog was built.
                if records.get(raw.get("uid"), {}).get("status") in {"pending", "blocked"}:
                    existing.pop(raw["uid"], None)
            if not is_valid_source_id(str(raw.get("source_id") or ""), source=name):
                continue
            if not raw.get("uid"):
                continue
            # 3Hentai is fail-closed for newly discovered rows: if the public
            # gallery page did not yield filter-relevant metadata, do not add
            # the item yet. It can be discovered again on a later healthy run.
            uid = raw["uid"]
            if name == "3hentai":
                if not reconcile_hentai3_input(existing.get(uid, {}), raw, filter_store, stamp,
                                               retain_original=True):
                    blocked += 1
                    blocked_reasons["repair_url_conflict"] = blocked_reasons.get("repair_url_conflict", 0) + 1
                    continue
                if records.get(uid, {}).get("status") == "pending":
                    existing.pop(uid, None)
            prior = existing.get(uid) or records.get(uid, {}).get("item")
            if (name == "3hentai" and records.get(uid, {}).get("status") == "pending"
                    and not hentai3_can_restore(raw, records[uid])):
                # A fresh title-derived search result cannot bypass an existing repair hold.
                blocked += 1
                blocked_reasons["metadata_unverified"] = blocked_reasons.get("metadata_unverified", 0) + 1
                continue
            if (name == "3hentai" and (prior or {}).get("repair_evidence")
                    and raw.get("source_url") != prior.get("source_url")):
                # Do not overwrite a corroborated URL with a newly title-matched card.
                blocked += 1
                blocked_reasons["repair_url_conflict"] = blocked_reasons.get("repair_url_conflict", 0) + 1
                continue
            merged = merge_item(prior, raw, stamp)
            saved_block = records.get(uid, {})
            if saved_block.get("status") == "blocked":
                reason = saved_block["reason"]
            elif r18g_match_fields(raw):
                reason = R18G_REASON
            elif name == "3hentai" and merged.get("filter_metadata_checked") != "3hentai-gallery-v1":
                reason = "metadata_unverified"
            else:
                reason = publication_reason(merged, fresh=True, fetched=None if name == "3hentai" else raw)
            if reason:
                blocked += 1
                blocked_reasons[reason] = blocked_reasons.get(reason, 0) + 1
                # Rejecting this response alone leaves the old catalog row
                # visible. Remove it too and retain evidence outside docs.
                existing.pop(uid, None)
                quarantine_item(filter_store, merged, reason, stamp)
                continue
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

    all_sources_failed = not audit_only and successful_sources == 0
    if all_sources_failed:
        print("All sources failed; catalog and crawl state will not be saved.", file=sys.stderr)
        return 1

    status_store.setdefault("nhentai", {})["existing_tag_audit"] = nh_existing_audit
    status_store.setdefault("3hentai", {})["id_migration"] = id_migration
    if isinstance(status_store.get("3hentai"), dict):
        status_store["3hentai"].update(h3_existing_audit)
        status_store["3hentai"]["purged_existing_this_run"] = purged_existing_3hentai

    status_store["3hentai"]["held_existing_unaudited"] = held_existing_unaudited

    # Every successful path, including audit-only and early collector skips,
    # reaches this gate before truncation, grouping or any catalog save.
    status_store["3hentai"]["held_identity_gate"] = hentai3_ids.exclude_held_identities(
        existing, records, id_map)
    items = sorted(existing.values(), key=sort_key, reverse=True)
    if KEEP_ITEMS > 0:
        items = items[:KEEP_ITEMS]

    duplicate_group_count = apply_duplicate_groups(items)
    duplicate_item_count = sum(1 for x in items if x.get("duplicate_group"))

    # Final defense applies after every merge, not just to incoming responses.
    assert not any(blocked_reason(x) or unverified_hitomi(x)
                   or (x.get("source") == "3hentai"
                       and x.get("filter_metadata_checked") != "3hentai-gallery-v1")
                   for x in items), "Unsafe catalog row"
    published_uids = {item["uid"] for item in items}
    restored_existing_shotacon = 0
    for uid, rec in shotacon_backups.items():
        published_uid = uid
        if hentai3_ids.namespace(uid) == "public":
            published_uid = id_map["public_to_internal"].get(uid.removeprefix("3hentai:g"), uid)
        if uid in published_uids or published_uid in published_uids:
            restored_existing_shotacon += 1
        else:
            records.setdefault(uid, rec)
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
        if (x.get("source") == "hitomi" and x.get("filter_metadata_checked_at") == stamp)
        or x["uid"] in restored_h3_uids}
    save_json(FILTER_STATE_FILE, {
        **filter_store, "records": {**shotacon_backups, **records},
    })

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
        "restored_existing_shotacon": restored_existing_shotacon,
        "held_existing_unverified": held_existing_unverified,
        "items": items,
    })
    id_map["updated_at"] = stamp
    save_json(id_map_path, id_map)
    save_json(STATE_FILE, state)
    status_store["filters"] = {
        "id_migration": id_migration,
        "content_filters": "enabled",
        "r18g_filter": "enabled",
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main(audit_only="--audit-filters-only" in sys.argv))
