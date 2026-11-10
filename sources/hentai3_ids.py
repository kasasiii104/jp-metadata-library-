"""Pure 3Hentai identity migration; title matches are never identity evidence."""
import copy
import re


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
            value = copy.deepcopy(value)
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
        elif store == "hentai3_attempts":
            if target:
                failures = max(target.get("failures", 0), value.get("failures", 0))
                value = dict(max((target, value), key=lambda x: x.get("last_attempt", 0)))
                value["failures"] = failures
        else:
            value = _merge(target, _identity(value, new, public), stamp, merge)
        entries[new] = value
        if not any((entry["old_uid"], entry["new_uid"], entry["reason"], entry["store"])
                   == (old, new, reason, store) for entry in id_map["migrations"]):
            id_map["migrations"].append(dict(old_uid=old, new_uid=new, reason=reason,
                                             store=store, migrated_at=stamp))
        stats["total"] += 1
        stats["by_store"][store] = stats["by_store"].get(store, 0) + 1


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
    # Determine ownership before removing rows from any item store.
    protected_attempts = set()
    for store in ("catalog", "records", "verified_checkpoints"):
        for uid, value in stores[store].items():
            if uid in moves:
                item = value["item"] if store == "records" else value
                if _legacy(item) != moves[uid][1]:
                    protected_attempts.add(uid)
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
                item = value["item"] if store == "records" else value
                eligible = _legacy(item) == moves[uid][1]
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
