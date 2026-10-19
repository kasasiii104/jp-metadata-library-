#!/usr/bin/env python3
"""Remove saved R-18G metadata offline without advancing any source cursor."""
import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import crawler as c
from tools.purge_ehentai_non_h import read_object, retained_digest


def purge(*, dry_run=False):
    data = read_object(c.DATA_FILE)
    store = read_object(c.FILTER_STATE_FILE)
    if not isinstance(data.get("items"), list) or not isinstance(store.get("records"), dict):
        raise ValueError("Invalid catalog or quarantine schema; nothing changed")
    items, records = data["items"], store["records"]
    if any(not isinstance(x, dict) or not isinstance(x.get("uid"), str) or not x["uid"] for x in items):
        raise ValueError("Invalid catalog identity; nothing changed")
    if len({x["uid"] for x in items}) != len(items):
        raise ValueError("Duplicate catalog identities; nothing changed")
    if any(not isinstance(rec, dict) or not isinstance(rec.get("item"), dict) for rec in records.values()):
        raise ValueError("Invalid quarantine record; nothing changed")
    checkpoints = store.get("verified_checkpoints", {})
    if not isinstance(checkpoints, dict) or any(not isinstance(x, dict) for x in checkpoints.values()):
        raise ValueError("Invalid checkpoint schema; nothing changed")
    protected = {path: hashlib.sha256(path.read_bytes()).hexdigest()
                 for path in (c.STATE_FILE, c.STATUS_FILE)}
    matches = {x["uid"]: c.r18g_match_fields(x) for x in items}
    removed = [x for x in items if matches[x["uid"]]]
    retained = [x for x in items if not matches[x["uid"]]]
    # Also lock previously held/checkpointed warnings before a later response
    # drops the label. Already confirmed exclusions remain untouched.
    held = {uid: rec["item"] for uid, rec in records.items()
            if rec.get("status") != "blocked" and c.r18g_match_fields(rec["item"])}
    held.update({uid: item for uid, item in checkpoints.items()
                 if c.r18g_match_fields(item) and records.get(uid, {}).get("status") != "blocked"})
    if any(item.get("uid") != uid for uid, item in held.items()):
        raise ValueError("Quarantine identity mismatch; nothing changed")
    # A published row with a known warning in saved evidence is excluded too.
    evidence_ids = set(held) | {uid for uid, rec in records.items() if c.r18g_match_fields(rec["item"])}
    evidence_removed = [x for x in retained if x["uid"] in evidence_ids]
    removed.extend(evidence_removed)
    retained = [x for x in retained if x["uid"] not in evidence_ids]
    before_digest = retained_digest(retained)
    report = {
        "policy": c.R18G_REASON, "checked_at": c.now_iso(), "dry_run": dry_run,
        "before": len(items), "removed": len(removed), "after": len(retained),
        "matched_fields": dict(sorted(Counter(field for x in removed for field in matches[x["uid"]]).items())),
        "removed_from_saved_evidence": len(evidence_removed),
        "held_records_blocked": len(held),
        "removed_by_source": dict(sorted(Counter(x.get("source", "") for x in removed).items())),
        "before_by_source": dict(sorted(Counter(x.get("source", "") for x in items).items())),
        "after_by_source": dict(sorted(Counter(x.get("source", "") for x in retained).items())),
        "remaining_r18g": sum(bool(c.r18g_match_fields(x)) for x in retained),
        "retained_metadata_sha256": before_digest,
        "crawl_state_sha256": protected[c.STATE_FILE],
        "source_status_sha256": protected[c.STATUS_FILE],
    }
    if dry_run:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return report
    for item in held.values():
        c.quarantine_item(store, item, c.R18G_REASON, report["checked_at"])
    for item in removed:
        c.quarantine_item(store, item, c.R18G_REASON, report["checked_at"])
    if removed:
        duplicate_groups = c.apply_duplicate_groups(retained)
        if retained_digest(retained) != before_digest:
            raise ValueError("Unexpected retained metadata change; nothing changed")
        ids = {x["uid"] for x in retained}
        if any(set(x.get("duplicate_uids", [])) - ids for x in retained):
            raise ValueError("Dangling duplicate link; nothing changed")
        data.update({"updated_at": report["checked_at"], "item_count": len(retained),
                     "duplicate_group_count": duplicate_groups,
                     "duplicate_item_count": sum(bool(x.get("duplicate_group")) for x in retained),
                     "items": retained})
    if removed or held:
        store["updated_at"] = report["checked_at"]
        store["r18g_purge"] = report
        # Recovery evidence is saved outside Pages before the public removal.
        c.save_json(c.FILTER_STATE_FILE, store)
    if removed:
        c.save_json(c.DATA_FILE, data)
    saved = read_object(c.DATA_FILE)["items"]
    if len(saved) != report["after"] or retained_digest(saved) != before_digest:
        raise ValueError("Saved catalog verification failed")
    if any(c.r18g_match_fields(x) or x["uid"] in evidence_ids for x in saved):
        raise ValueError("R-18G exclusion verification failed")
    if any(hashlib.sha256(path.read_bytes()).hexdigest() != digest for path, digest in protected.items()):
        raise ValueError("Crawl cursor or source status unexpectedly changed")
    report["verified"] = True
    report_path = c.DOCS_DIR / "r18g_filter_audit.json"
    if removed or held or not report_path.exists():
        c.save_json(report_path, report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    purge(dry_run=parser.parse_args().dry_run)
