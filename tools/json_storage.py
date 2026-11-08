"""Fail-closed JSON loading and atomic catalog persistence."""
import json
import os
import tempfile
from pathlib import Path


def validate_catalog(value):
    if not isinstance(value, dict) or not isinstance(value.get("items"), list):
        raise ValueError("catalog must be an object with an items array")
    seen_uids = set()
    for item in value["items"]:
        if not isinstance(item, dict):
            raise ValueError("catalog items must be objects")
        for key in ("uid", "source"):
            if not isinstance(item.get(key), str) or not item[key]:
                raise ValueError(f"catalog item requires a nonempty {key}")
        if item["uid"] in seen_uids:
            raise ValueError(f"catalog contains duplicate uid: {item['uid']}")
        seen_uids.add(item["uid"])
        # Schema upgrades preserve nullable scalar metadata from older rows.
        for key in ("language", "title", "title_jp", "category", "source_token",
                    "source_url", "source_url_kind", "resolved_gallery_id",
                    "posted_at", "thumbnail", "first_seen"):
            if key in item and item[key] is not None and not isinstance(item[key], str):
                raise ValueError(f"catalog item {key} must be a string or null")
        if "source_id" in item and item["source_id"] is not None:
            if isinstance(item["source_id"], bool) or not isinstance(item["source_id"], (str, int)):
                raise ValueError("catalog item source_id must be a string, integer or null")
        if "works" in item and not isinstance(item["works"], list):
            raise ValueError("catalog item works must be an array")
        for key in ("tags", "artists", "groups", "parodies", "characters"):
            if key in item and (not isinstance(item[key], list)
                                or any(not isinstance(x, str) for x in item[key])):
                raise ValueError(f"catalog item {key} must be a string array")


def validate_mapping(value):
    if not isinstance(value, dict):
        raise ValueError("JSON root must be an object")


def validate_source_mapping(value):
    validate_mapping(value)
    if any(not isinstance(v, dict) for k, v in value.items() if k != "updated_at"):
        raise ValueError("source entries must be objects")


def validate_filter_store(value):
    validate_mapping(value)
    for key in ("records", "hitomi_attempts", "verified_checkpoints"):
        entries = value.get(key, {})
        validate_mapping(entries)
        if any(not isinstance(entry, dict) for entry in entries.values()):
            raise ValueError(f"{key} entries must be objects")
    for key, entry in value.get("records", {}).items():
        validate_catalog({"items": [entry.get("item")]})
        if entry["item"]["uid"] != key:
            raise ValueError(f"records key does not match item uid: {key}")
    validate_catalog({"items": list(value.get("verified_checkpoints", {}).values())})
    for key, entry in value.get("verified_checkpoints", {}).items():
        if entry["uid"] != key:
            raise ValueError(f"verified_checkpoints key does not match uid: {key}")


def load_json(path, default, validator=None):
    path = Path(path)
    try:
        with path.open("r", encoding="utf-8") as stream:
            value = json.load(stream)
    except FileNotFoundError:
        # A dangling symlink is an unreadable existing input, not a new dataset.
        if path.is_symlink():
            raise
        value = default
    (validator or validate_mapping)(value)
    return value


def save_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Unique files in the destination directory keep replacement on one filesystem.
    # On failure retain the temporary file for diagnosis; the old file stays intact.
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=path.name + ".", suffix=".tmp", delete=False) as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(stream.name, path)
