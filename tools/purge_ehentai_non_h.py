#!/usr/bin/env python3
"""Offline, source-scoped cleanup. No discovery, metadata or image requests."""
import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import crawler as c


DUPLICATE_FIELDS = {"duplicate_group", "duplicate_count", "duplicate_sources", "duplicate_uids"}


def read_object(path):
    # A missing/corrupt file must abort a destructive operation, not become [].
    with path.open(encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path.name}")
    return value


def retained_digest(items):
    rows = [{k: v for k, v in item.items() if k not in DUPLICATE_FIELDS} for item in items]
    payload = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def purge(*, dry_run=False):
    data = read_object(c.DATA_FILE)
    store = read_object(c.FILTER_STATE_FILE)
    if not isinstance(data.get("items"), list) or not isinstance(store.get("records"), dict):
        raise ValueError("Invalid catalog or quarantine schema; nothing changed")
    items = data["items"]
    if any(not isinstance(x, dict) or not isinstance(x.get("uid"), str) or not x["uid"] for x in items):
        raise ValueError("Invalid catalog identity; nothing changed")
    if len({x["uid"] for x in items}) != len(items):
        raise ValueError("Duplicate catalog identities; nothing changed")
    if any(not isinstance(rec, dict) for rec in store["records"].values()):
        raise ValueError("Invalid quarantine record; nothing changed")
    state_digest = hashlib.sha256(c.STATE_FILE.read_bytes()).hexdigest()
    status_digest = hashlib.sha256(c.STATUS_FILE.read_bytes()).hexdigest()
    removed = [x for x in items if c.is_ehentai_non_h(x)]
    retained = [x for x in items if not c.is_ehentai_non_h(x)]
    before_digest = retained_digest(retained)
    report = {
        "policy": c.EHENTAI_NON_H_REASON, "checked_at": c.now_iso(),
        "dry_run": dry_run, "before": len(items), "removed": len(removed),
        "after": len(retained), "remaining_ehentai_non_h": 0,
        "before_by_source": dict(sorted(Counter(x.get("source", "") for x in items).items())),
        "after_by_source": dict(sorted(Counter(x.get("source", "") for x in retained).items())),
        "retained_metadata_sha256": before_digest,
        "crawl_state_sha256": state_digest,
        "source_status_sha256": status_digest,
    }
    if not dry_run and removed:
        for item in removed:
            c.quarantine_item(store, item, c.EHENTAI_NON_H_REASON, report["checked_at"])
        duplicate_groups = c.apply_duplicate_groups(retained)
        if retained_digest(retained) != before_digest:
            raise ValueError("Unexpected retained metadata change; nothing changed")
        retained_ids = {x["uid"] for x in retained}
        if any(set(x.get("duplicate_uids", [])) - retained_ids for x in retained):
            raise ValueError("Dangling duplicate link; nothing changed")
        if any(c.is_ehentai_non_h(x) for x in retained):
            raise ValueError("Category exclusion failed; nothing changed")
        data.update({
            "updated_at": report["checked_at"], "item_count": len(retained),
            "duplicate_group_count": duplicate_groups,
            "duplicate_item_count": sum(bool(x.get("duplicate_group")) for x in retained),
            "items": retained,
        })
        store["updated_at"] = report["checked_at"]
        store["ehentai_non_h_purge"] = report
        # Keep recovery evidence outside Pages before removing public rows.
        c.save_json(c.FILTER_STATE_FILE, store)
        c.save_json(c.DATA_FILE, data)
        saved = read_object(c.DATA_FILE)["items"]
        if len(saved) != report["after"] or retained_digest(saved) != before_digest:
            raise ValueError("Saved catalog verification failed")
        if hashlib.sha256(c.STATE_FILE.read_bytes()).hexdigest() != state_digest:
            raise ValueError("Crawl cursor unexpectedly changed")
        if hashlib.sha256(c.STATUS_FILE.read_bytes()).hexdigest() != status_digest:
            raise ValueError("Source status unexpectedly changed")
        report["verified"] = True
        c.save_json(c.DOCS_DIR / "category_filter_audit.json", report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    purge(dry_run=parser.parse_args().dry_run)
