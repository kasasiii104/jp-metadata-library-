import copy
import re
import time
from typing import Any

import requests

from config import (NH_LATEST_PAGES, NH_MAX_DETAILS_PER_RUN, NH_INITIAL_BACKFILL_ITEMS,
                    NH_BACKFILL_SEEK_PAGES, NH_SEARCH_DELAY_SEC, NH_DETAIL_DELAY_SEC,
                    NH_DETAIL_MAX_FAILURES, NH_REQUEST_TIMEOUT, NH_RUN_DEADLINE_SEC)
from sources.common import HEADERS, is_valid_source_id, iso_from_unix, polite_sleep, unique_strings
from sources.net_policy import URLPolicyError, guarded_request

SEARCH_URL = "https://nhentai.net/api/v2/search?query=language%3Ajapanese&sort=date&page={page}"
DETAIL_URL = "https://nhentai.net/api/v2/galleries/{gid}"


class DeadlineReached(Exception):
    pass


class HTTPError(RuntimeError):
    def __init__(self, status_code: int):
        self.status_code = status_code
        super().__init__(f"HTTP {status_code}")


def _get_json(session: requests.Session, url: str) -> dict:
    response = guarded_request(
        session, "GET", url, purpose="nhentai_api",
        headers={**HEADERS, "Accept": "application/json"},
        timeout=NH_REQUEST_TIMEOUT, follow_redirects=False,
    )
    if response.status_code != 200:
        response.close()
        raise HTTPError(response.status_code)
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("expected JSON object")
    return payload


def _valid_id(gid: str) -> bool:
    return is_valid_source_id(gid, source="nhentai") and gid.isdigit()


def _search_page(session: requests.Session, page: int) -> list[dict]:
    payload = _get_json(session, SEARCH_URL.format(page=int(page)))
    rows = payload.get("result")
    if not isinstance(rows, list) or any(not isinstance(row, dict) or
                                            not _valid_id(str(row.get("id", ""))) for row in rows):
        raise ValueError("invalid search result")
    return rows


def _detail(session: requests.Session, gid: str) -> dict:
    if not _valid_id(gid):
        raise ValueError("invalid gallery ID")
    return _get_json(session, DETAIL_URL.format(gid=gid))


def _thumb_url(path: str) -> str:
    if isinstance(path, str) and re.fullmatch(r"galleries/[0-9]+/thumb\.[A-Za-z0-9.]+", path):
        return "https://t.nhentai.net/" + path
    return ""


def _normalize(raw: dict[str, Any], fallback_thumb: str = "") -> dict[str, Any] | None:
    gid = str(raw.get("id") or "")
    if not _valid_id(gid):
        return None
    titles = raw.get("title") if isinstance(raw.get("title"), dict) else {}
    tags: dict[str, list[str]] = {}
    for tag in raw.get("tags") or []:
        if isinstance(tag, dict):
            tags.setdefault(tag.get("type", ""), []).append(str(tag.get("name") or ""))
    thumbnail = raw.get("thumbnail") or {}
    path = thumbnail.get("path") if isinstance(thumbnail, dict) else ""
    return {
        "uid": f"nhentai:{gid}", "source": "nhentai", "source_id": gid,
        "source_url": f"https://nhentai.net/g/{gid}/",
        "title": str(titles.get("japanese") or titles.get("pretty") or titles.get("english") or "").strip(),
        "title_jp": str(titles.get("japanese") or "").strip(),
        "language": "japanese" if any(name.strip().lower() == "japanese" for name in tags.get("language", [])) else "",
        "category": (tags.get("category") or [""])[0],
        "artists": unique_strings(tags.get("artist", [])),
        "groups": unique_strings(tags.get("group", [])),
        "parodies": unique_strings(tags.get("parody", [])),
        "characters": unique_strings(tags.get("character", [])),
        "tags": unique_strings(tags.get("tag", [])),
        "pages": int(raw.get("num_pages") or 0), "rating": None,
        "popularity": raw.get("num_favorites") if type(raw.get("num_favorites")) is int else None,
        "posted_at": iso_from_unix(raw.get("upload_date")) if raw.get("upload_date") else "",
        "thumbnail": _thumb_url(path or fallback_thumb),
    }


class DetailRun:
    def __init__(self):
        self.deadline = time.monotonic() + NH_RUN_DEADLINE_SEC
        self.audited_ids: set[str] = set()

    def expired(self):
        return time.monotonic() >= self.deadline


def audit_existing_tags(gallery_ids: list[str], *, run: DetailRun | None = None) -> tuple[dict[str, list[str]], dict]:
    """Fetch content tags for saved galleries through the normal detail API."""
    tags_by_id = {}
    status = {"attempted": 0, "tagged": 0, "confirmed_empty": 0, "excluded": 0,
              "stopped": False, "errors": []}
    if not gallery_ids:
        return tags_by_id, status

    run = run if run is not None else DetailRun()
    consecutive_failures = 0
    with requests.Session() as session:
        for gid in gallery_ids[:max(0, NH_MAX_DETAILS_PER_RUN)]:
            if status["attempted"]:
                polite_sleep(NH_DETAIL_DELAY_SEC)
            if run.expired():
                break
            run.audited_ids.add(gid)
            status["attempted"] += 1
            status["last_attempted_id"] = gid
            try:
                raw = _detail(session, gid)
                if str(raw.get("id")) != gid:
                    raise ValueError("detail ID mismatch")
                if not isinstance(raw.get("tags"), list) or any(
                    not isinstance(tag, dict) or not isinstance(tag.get("type"), str)
                    or not isinstance(tag.get("name"), str) or not tag["name"].strip()
                    for tag in raw["tags"]
                ):
                    raise ValueError("invalid detail tags")
                tags = unique_strings(tag["name"] for tag in raw["tags"] if tag["type"] == "tag")
            except (URLPolicyError, HTTPError, requests.RequestException, ValueError) as exc:
                status["errors"].append(f"{gid}: {str(exc)[:200]}")
                consecutive_failures += 1
                if (isinstance(exc, URLPolicyError)
                        or getattr(exc, "status_code", None) in (401, 403, 429)):
                    status["stopped"] = True
                    break
                if consecutive_failures >= NH_DETAIL_MAX_FAILURES:
                    break
                continue
            consecutive_failures = 0
            tags_by_id[gid] = tags
            status["tagged" if tags else "confirmed_empty"] += 1
    return tags_by_id, status


def collect(state: dict | None = None, *, run: DetailRun | None = None) -> tuple[list[dict], dict, dict]:
    shared_run = run is not None
    run = run if run is not None else DetailRun()
    original = copy.deepcopy(state or {})
    new = copy.deepcopy(original)
    for key in ("latest_id", "backfill_before_id", "backfill_page", "backfill_remaining"):
        try:
            value = int(new.get(key))
        except (ValueError, TypeError, OverflowError):
            value = -1
        new.pop(key, None)
        if value >= 0:
            new[key] = value
    if "latest_id" in new and "backfill_before_id" not in new:
        new.update(backfill_complete=True, backfill_remaining=0)
    failures = {}
    raw_failures = new.get("detail_failures")
    if isinstance(raw_failures, dict):
        for gid, value in raw_failures.items():
            try:
                failures[str(gid)] = int(value)
            except (ValueError, TypeError, OverflowError):
                pass
    if "detail_failures" in new:
        new["detail_failures"] = dict(failures)
    items, errors = [], []
    # Audit candidates already have a saved row. Count every attempt against
    # the run budget and leave failed audits for the next run.
    seen, discovered = set(run.audited_ids), set()
    calls = {"search": 0, "detail": 0}
    halted = False
    policy_denied = False
    budget_limited = False
    confirmation_used = False
    budget = NH_MAX_DETAILS_PER_RUN - len(run.audited_ids)
    latest_before = int(new.get("latest_id") or 0)
    back_before = new.get("backfill_before_id")
    counts = dict(latest_new=0, backfill_processed=0, skipped_non_japanese=0,
                  detail_errors=0, detail_successes=0, skipped_failed=0, rate_limited=False,
                  latest_gap=False, deadline_reached=False)

    def request(kind, fn, *args):
        nonlocal budget_limited
        # Standalone collection retains its first-page probe. A shared run
        # may already have exhausted its deadline during the audit.
        check_deadline = shared_run or kind != "search" or calls["search"] > 0
        delayed = calls[kind] > 0 or (kind == "detail" and bool(run.audited_ids))
        expired = check_deadline and run.expired()
        if delayed and not expired:
            polite_sleep(NH_SEARCH_DELAY_SEC if kind == "search" else NH_DETAIL_DELAY_SEC)
            expired = check_deadline and run.expired()
        if expired:
            counts["deadline_reached"] = True
            budget_limited = True
            raise DeadlineReached()
        calls[kind] += 1
        return fn(session, *args)

    def error(context, exc):
        nonlocal halted, policy_denied
        errors.append(f"{context}: {str(exc)[:200]}")
        if isinstance(exc, URLPolicyError):
            policy_denied = halted = True
        if getattr(exc, "status_code", None) == 429:
            counts["rate_limited"] = True
            halted = True

    def process(row, next_row=None, confirmation=False):
        nonlocal budget, budget_limited, confirmation_used
        gid = str(row["id"])
        discovered.add(gid)
        if gid in seen:
            return True
        if not confirmation and failures.get(gid, 0) >= NH_DETAIL_MAX_FAILURES:
            failures.pop(gid, None)
            counts["skipped_failed"] += 1
            seen.add(gid)
            return True
        if budget <= 0 or halted:
            budget_limited = budget <= 0
            return False
        seen.add(gid)
        budget -= 1
        try:
            raw = request("detail", _detail, gid)
            if str(raw.get("id")) != gid:
                raise ValueError("detail ID mismatch")
            item = _normalize(raw, row.get("thumbnail", ""))
            if item is None:
                raise ValueError("invalid detail")
            counts["detail_successes"] += 1
            if item["language"] == "japanese":
                items.append(item)
            else:
                counts["skipped_non_japanese"] += 1
            failures.pop(gid, None)
            return True
        except URLPolicyError as exc:
            counts["detail_errors"] += 1
            error(gid, exc)
            return False
        except DeadlineReached:
            return False
        except Exception as exc:
            if getattr(exc, "status_code", None) == 404:
                failures.pop(gid, None)
                return True
            if not confirmation and getattr(exc, "status_code", None) != 429:
                failures[gid] = failures.get(gid, 0) + 1
            counts["detail_errors"] += 1
            error(gid, exc)
            if next_row is None:
                next_row = next((candidate for candidate in first
                                 if str(candidate["id"]) != gid and str(candidate["id"]) not in seen), None)
            if (not confirmation and not confirmation_used and not halted and
                    not counts["detail_successes"] and next_row is not None and budget > 0):
                confirmation_used = True
                process(next_row, confirmation=True)
            return False

    def finish(page_one_ok=True):
        progressed = (new.get("latest_id", 0) != latest_before or
                      new.get("backfill_before_id") != back_before)
        ok = (page_one_ok and not policy_denied
              and (not errors or counts["detail_successes"] > 0 or progressed))
        if policy_denied:
            items.clear()
        if counts["detail_successes"]:
            new["detail_failures"] = dict(list(failures.items())[-50:])
        result_state = new if ok else original
        status = {"status": "ok" if ok else "error", "discovered": len(discovered),
                  "accepted_raw": len(items), "message": " | ".join(errors[:3]),
                  **counts, "partial": bool(errors) or budget_limited,
                  "latest_id_before": latest_before,
                  "latest_id_after": result_state.get("latest_id", 0),
                  "backfill_before_id_before": back_before,
                  "backfill_before_id_after": result_state.get("backfill_before_id"),
                  "backfill_remaining": result_state.get("backfill_remaining", 0)}
        return items, result_state, status

    with requests.Session() as session:
        try:
            first = request("search", _search_page, 1)
        except DeadlineReached:
            return finish()
        except Exception as exc:
            error("page 1", exc)
            return finish(False)
        initial = not latest_before
        rows = list(first)
        if not initial:
            for page in range(2, NH_LATEST_PAGES + 1):
                if not rows or any(int(row["id"]) <= latest_before for row in rows):
                    break
                try:
                    rows.extend(request("search", _search_page, page))
                except DeadlineReached:
                    return finish()
                except Exception as exc:
                    error(f"page {page}", exc)
                    # A missing older page prevents a contiguous latest advance.
                    return finish()
        counts["latest_gap"] = (not initial and
                                not any(int(row["id"]) <= latest_before for row in rows))
        latest = []
        ids = set()
        for row in rows:
            if int(row["id"]) <= latest_before:
                continue
            if int(row["id"]) not in ids:
                latest.append(row)
                ids.add(int(row["id"]))
        latest_done = True
        ordered_latest = list(reversed(latest))
        for index, row in enumerate(ordered_latest):
            next_row = ordered_latest[index + 1] if index + 1 < len(ordered_latest) else None
            if not process(row, next_row):
                latest_done = False
                break
            new["latest_id"] = max(new.get("latest_id", 0), int(row["id"]))
            counts["latest_new"] += 1
        if initial and first and counts["latest_new"]:
            new.update(backfill_before_id=int(first[-1]["id"]), backfill_page=2,
                       backfill_remaining=NH_INITIAL_BACKFILL_ITEMS,
                       backfill_complete=NH_INITIAL_BACKFILL_ITEMS <= 0)
        remaining = int(new.get("backfill_remaining") or 0)
        if remaining <= 0 and "backfill_remaining" in new:
            new["backfill_complete"] = True
        if (latest_done and remaining > 0 and budget > 0 and not halted
                and "backfill_before_id" in new):
            boundary = int(new["backfill_before_id"])
            page = max(1, int(new.get("backfill_page") or 2))
            newer_pages = {1} if initial else set()
            for _ in range(NH_BACKFILL_SEEK_PAGES):
                try:
                    rows = request("search", _search_page, page)
                except DeadlineReached:
                    break
                except Exception as exc:
                    error(f"backfill page {page}", exc)
                    break
                new["backfill_page"] = page
                if not rows:
                    if page > 1 and page - 1 not in newer_pages:
                        page -= 1
                        new["backfill_page"] = page
                        continue
                    new.update(backfill_complete=True, backfill_remaining=0)
                    break
                older = [row for row in rows if int(row["id"]) < boundary]
                if not older:
                    newer_pages.add(page)
                    page += 1
                    new["backfill_page"] = page
                    continue
                if len(older) == len(rows) and page > 1 and page - 1 not in newer_pages:
                    page -= 1
                    new["backfill_page"] = page
                    continue
                newer_pages.discard(page)
                processed_page = True
                for index, row in enumerate(older):
                    next_row = older[index + 1] if index + 1 < len(older) else None
                    if remaining <= 0 or not process(row, next_row):
                        processed_page = False
                        break
                    new["backfill_before_id"] = int(row["id"])
                    counts["backfill_processed"] += 1
                    remaining -= 1
                    new["backfill_remaining"] = remaining
                if remaining <= 0:
                    new["backfill_complete"] = True
                if not processed_page or remaining <= 0 or budget <= 0 or halted:
                    break
                boundary = int(new["backfill_before_id"])
                page += 1
                new["backfill_page"] = page
    return finish()
