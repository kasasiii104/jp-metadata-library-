"""One bounded metadata probe. Does not change or publish the site catalog."""
import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from crawler import blocked_reason
from sources import nhentai


def run_probe():
    items, _, status = nhentai.collect({}, latest_pages=1, backfill_pages=0,
                                      max_details=3, max_items=3)
    rejected = Counter()
    accepted = 0
    for item in items:
        reason = blocked_reason(item)
        if item.get("filter_metadata_checked") != nhentai.FILTER_METADATA_VERSION:
            reason = "metadata_unverified"
        if reason:
            rejected[reason] += 1
        else:
            accepted += 1
    return {
        "checked_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "mode": "metadata-only; one search page; at most three detail lookups",
        "collector": status,
        "eligible_after_filters": accepted,
        "rejected_reasons": dict(rejected),
        "ready_for_integration": status["status"] == "ok" and not status["stopped"] and accepted > 0,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = run_probe()
    encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if report["ready_for_integration"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
