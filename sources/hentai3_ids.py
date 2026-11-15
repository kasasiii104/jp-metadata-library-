"""Pure 3Hentai identity migration; title matches are never identity evidence."""
import copy
import re

from sources.hentai3 import direct_gallery_id


def needs_repair(record):
    """Shared hold predicate for restoration and identity merges."""
    detail = record.get("hold_detail", "")
    original_url = record.get("hold_source_url", record.get("item", {}).get("source_url", ""))
    return (detail in {"3hentai_mislinked_split", "3hentai_url_conflict"}
            or ((detail.startswith("3hentai_unaudited")
                 or (record.get("status") == "pending"
                     and record.get("item", {}).get("source") == "3hentai"))
                and not direct_gallery_id(original_url)))


def reconcile_record(item, record):
    """Freeze and reconcile co-located URL provenance, idempotently."""
    if (item.get("source") != "3hentai" or not record
            or record.get("status") == "blocked"):
        return
    held_url = record.get("hold_source_url", record.get("item", {}).get("source_url", ""))
    held_gid = direct_gallery_id(held_url)
    item_url = item.get("source_url", "")
    item_gid = direct_gallery_id(item_url)
    if held_gid and not item_gid:
        record["hold_source_url"] = item_url
    elif held_gid and item_gid and held_gid != item_gid and not needs_repair(record):
        record["hold_source_url"] = held_url
        record["hold_detail"] = "3hentai_url_conflict"


def _freeze_hold(record):
    record = copy.deepcopy(record)
    record.setdefault("hold_source_url", record.get("item", {}).get("source_url", ""))
    return record


def namespace(uid):
    if re.fullmatch(r"3hentai:[0-9]+", uid or ""):
        return "internal"
    if re.fullmatch(r"3hentai:g[0-9]+", uid or ""):
        return "public"
    return None


def empty_id_map():
    return {"version": 1, "updated_at": "", "namespaces": {
        "3hentai:<digits>": "Jandapress internal ID",
        "3hentai:g<digits>": "Public /d/ gallery ID"},
        "public_to_internal": {}, "migrations": []}


def _legacy(item):
    uid = item.get("uid", "")
    if item.get("source") != "3hentai" or namespace(uid) != "internal":
        return None
    number = uid.split(":")[1]
    match = re.search(r"/d/([0-9]+)(?:/|$|[?#])", item.get("source_url") or "")
    return number if match and match[1] == number == str(item.get("source_id")) else None


def _identity(item, uid, public):
    out = dict(item)
    out.update(uid=uid, source_id=uid.split(":")[1], public_gallery_id=public)
    return out


def _merge(old, new, stamp, merge):
    result = merge(old, new, stamp)
    dates = [x.get("first_seen") for x in (old or {}, new) if x.get("first_seen")]
    if dates:
        result["first_seen"] = min(dates)
    return result


def _move(stores, old, new, public, id_map, stamp, merge, reason, stats, sources=None):
    for store, entries in stores.items():
        source = entries if sources is None else sources[store]
        if old not in source:
            continue
        value = source.pop(old)
        target = entries.get(new)
        if store == "records":
            value = _freeze_hold(value)
            target = _freeze_hold(target) if target else None
            holds = [rec for rec in (target, value) if rec is not None]
            value["item"] = _identity(value["item"], new, public)
            if target:
                # Keep the blocked record's reason and evidence on either side.
                winner = (target if target.get("status") == "blocked" else value
                          if value.get("status") == "blocked" else
                          max((target, value), key=lambda rec: (
                              rec.get("item", {}).get("last_seen") or rec.get("last_seen") or "")))
                other = value if winner is target else target
                value = dict(winner)
                value["item"] = _merge(other["item"], winner["item"], stamp, merge)
            if target:
                # Apply the same URL rule to both frozen holds before choosing
                # repair provenance; item URLs may already have been stripped.
                for held, other_hold in ((target, holds[-1]), (holds[-1], target)):
                    reconcile_record(dict(other_hold["item"], source_url=other_hold["hold_source_url"]), held)
            # Destination wins conflicting originals; source fills missing keys.
            original = {}
            for rec in reversed(holds):
                original.update(rec.get("repair_original") or {})
            if any("repair_original" in rec for rec in holds):
                value["repair_original"] = copy.deepcopy(original)
            repair_holds = [rec for rec in holds if needs_repair(rec)]
            split = next((rec for rec in holds
                          if rec.get("hold_detail") == "3hentai_mislinked_split"), None)
            selected = split or next(iter(repair_holds), None)
            if selected is not None:
                value["hold_source_url"] = selected["hold_source_url"]
                value["hold_detail"] = selected.get("hold_detail") or "3hentai_unaudited"
        elif store == "hentai3_attempts":
            if target:
                failures = max(target.get("failures", 0), value.get("failures", 0))
                value = dict(max((target, value), key=lambda x: x.get("last_attempt", 0)))
                value["failures"] = failures
        else:
            if store == "catalog" and target:
                # A restored checkpoint may now be a catalog destination.
                # Preserve both URLs before the catalog merge discards one.
                records = stores["records"]
                candidate = copy.deepcopy(records.get(new) or {
                    "item": target, "status": "pending", "reason": "metadata_unverified"})
                reconcile_record(value, candidate)
                if needs_repair(candidate):
                    records[new] = candidate
            value = _merge(target, _identity(value, new, public), stamp, merge)
        entries[new] = value
        if not any((entry["old_uid"], entry["new_uid"], entry["reason"], entry["store"])
                   == (old, new, reason, store) for entry in id_map["migrations"]):
            id_map["migrations"].append(dict(old_uid=old, new_uid=new, reason=reason,
                                             store=store, migrated_at=stamp))
        stats["total"] += 1
        stats["by_store"][store] = stats["by_store"].get(store, 0) + 1
    # All stores have moved: either catalog or record may have been the source.
    reconcile_record(stores.get("catalog", {}).get(new, {}),
                     stores.get("records", {}).get(new, {}))


def migration_stats():
    return {"total": 0, "by_store": {}, "conflicts": 0}


def migrate(items, filter_store, id_map, stamp, merge):
    catalog = {x["uid"]: x for x in items}
    stores = {"catalog": catalog, **{key: filter_store.setdefault(key, {}) for key in
              ("records", "verified_checkpoints", "hentai3_attempts")}}
    identities = {}
    for store in ("catalog", "records", "verified_checkpoints"):
        for value in stores[store].values():
            item = value["item"] if store == "records" else value
            public = _legacy(item)
            if public:
                identities[item["uid"]] = public
    stats = migration_stats()
    moves = {}
    for old, public in identities.items():
        new = id_map["public_to_internal"].get(public, "3hentai:g" + public)
        if new != old:
            moves[old] = (new, public)
    # A missing URL is not a different identity. Move the whole UID cohort.
    # Explicitly different public identities can occur after a partial save;
    # retain their ownership (and attempts) rather than relabeling them.
    protected_attempts = set()
    ownership = {}
    for old, (_, public) in moves.items():
        for store in ("catalog", "records", "verified_checkpoints"):
            value = stores[store].get(old)
            if value is None:
                continue
            item = value["item"] if store == "records" else value
            declared = str(item.get("public_gallery_id") or "")
            direct = direct_gallery_id(item.get("source_url", ""))
            different = (item.get("source") != "3hentai"
                         or item.get("uid") != old
                         or (declared and declared != public)
                         or (direct and direct != public))
            ownership[store, old] = not different
            if different:
                protected_attempts.add(old)
        if old in protected_attempts:
            stats["conflicts"] += 1
            # main must hold both candidates before applying checkpoints.
            # Return identities separately: item cleanup must not erase this
            # signal, and distinct persisted records retain their ownership.
            stats.setdefault("held_uids", []).extend((old, moves[old][0]))
    # Remove every moving source before inserting destinations: a destination
    # may itself be an unrelated legacy source that still needs to move.
    sources = {}
    for store, entries in stores.items():
        sources[store] = {}
        for uid, value in list(entries.items()):
            if uid not in moves:
                continue
            if store == "hentai3_attempts":
                eligible = "id_gen" not in value and uid not in protected_attempts
            else:
                eligible = ownership[store, uid]
            if eligible:
                sources[store][uid] = entries.pop(uid)
    for old, (new, public) in moves.items():
        _move(stores, old, new, public, id_map, stamp, merge, "namespace", stats,
              sources=sources)
    for attempt in stores["hentai3_attempts"].values():
        attempt["id_gen"] = 2
    return list(catalog.values()), stats


def normalize_incoming(raw, catalog, filter_store, id_map, stamp, merge, stats):
    raw = dict(raw)
    public = str(raw.get("public_gallery_id") or "")
    if not re.fullmatch(r"[0-9]+", public):
        return raw
    uid = raw.get("uid", "")
    known = id_map["public_to_internal"].get(public)
    if namespace(uid) == "internal":
        if known and known != uid:
            stats["conflicts"] += 1
            return raw
        id_map["public_to_internal"][public] = uid
        stores = {"catalog": catalog, **{key: filter_store.setdefault(key, {}) for key in
                  ("records", "verified_checkpoints", "hentai3_attempts")}}
        _move(stores, "3hentai:g" + public, uid, public, id_map, stamp, merge, "fold", stats)
    elif namespace(uid) == "public" and known:
        raw = _identity(raw, known, public)
    return raw


def exclude_held_identities(catalog, records, id_map):
    """Remove public candidates connected to any pending/blocked identity.

    Build undirected components so aliases remain related across multiple map
    and item declarations. Equal numbers in different namespaces alone are not
    evidence; only legacy URL shape, explicit public ID and the ID map link them.
    """
    parent = {}

    def root(uid):
        parent.setdefault(uid, uid)
        if parent[uid] != uid:
            parent[uid] = root(parent[uid])
        return parent[uid]

    def link(left, right):
        parent[root(left)] = root(right)

    for public, internal in id_map["public_to_internal"].items():
        link("3hentai:g" + public, internal)
    for uid, item in list(catalog.items()) + [
            (uid, rec.get("item", {})) for uid, rec in records.items()]:
        if item.get("source") != "3hentai":
            continue
        root(uid)
        if namespace(item.get("uid")):
            link(uid, item["uid"])
        public = str(item.get("public_gallery_id") or "")
        if re.fullmatch(r"[0-9]+", public):
            link(uid, "3hentai:g" + public)
        legacy = _legacy(item)
        if legacy:
            link(uid, "3hentai:g" + legacy)
    # A hold may retain the old direct URL after its item URL was stripped.
    for uid, rec in records.items():
        legacy = _legacy(dict(rec.get("item", {}),
                              source_url=rec.get("hold_source_url", "")))
        if legacy:
            link(uid, "3hentai:g" + legacy)
    held = {root(uid) for uid, rec in records.items()
            if rec.get("status") in {"pending", "blocked"}}
    removed = [uid for uid, item in catalog.items()
               if item.get("source") == "3hentai" and root(uid) in held]
    for uid in removed:
        catalog.pop(uid)
    return len(removed)
