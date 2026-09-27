import os
from typing import Any
from urllib.parse import quote_plus

import requests

API_BASE = os.environ.get("JANDAPRESS_URL", "http://127.0.0.1:3000").rstrip("/")
SEARCH_KEY = os.environ.get("ASMHENTAI_SEARCH_KEY", "japanese")
PAGES = int(os.environ.get("ASMHENTAI_LATEST_PAGES", "4"))
LIMIT = int(os.environ.get("ASMHENTAI_MAX_GALLERIES_PER_RUN", "80"))
TIMEOUT = 30


def clean(v: Any) -> str:
    return " ".join(str(v or "").split()).strip()


def first(d: dict, *keys):
    for k in keys:
        if d.get(k) not in (None, "", [], {}):
            return d[k]
    return None


def walk(obj: Any) -> list[dict]:
    out = []
    if isinstance(obj, dict):
        ident = first(obj, "id", "gallery_id", "galleryId", "book", "source_id", "gid")
        title = first(obj, "title", "name", "pretty", "english", "japanese")
        if ident is not None and title:
            out.append(obj)
        for v in obj.values():
            if isinstance(v, (dict, list)):
                out.extend(walk(v))
    elif isinstance(obj, list):
        for v in obj:
            out.extend(walk(v))
    return out


def names(v: Any) -> list[str]:
    out = []
    if isinstance(v, str):
        if clean(v): out.append(clean(v))
    elif isinstance(v, list):
        for x in v: out.extend(names(x))
    elif isinstance(v, dict):
        n = first(v, "name", "value", "label", "title", "tag")
        if n: out.append(clean(n))
    return list(dict.fromkeys(out))


def tags(raw: Any) -> list[str]:
    out = []
    if isinstance(raw, str):
        out.append(clean(raw))
    elif isinstance(raw, list):
        for x in raw: out.extend(tags(x))
    elif isinstance(raw, dict):
        n = first(raw, "name", "tag", "value", "label", "title")
        ns = first(raw, "namespace", "type", "category")
        if n:
            n = clean(n); out.append(f"{clean(ns)}:{n}" if ns and ":" not in n else n)
        for k, v in raw.items():
            if k not in {"name","tag","value","label","title","namespace","type","category"} and isinstance(v,(dict,list)):
                out.extend(tags(v))
    return list(dict.fromkeys(x for x in out if x))


def is_japanese(raw: dict, ts: list[str]) -> bool:
    vals = [
        clean(first(raw, "language", "lang", "locale")),
        *ts,
    ]
    text = " ".join(vals).casefold()
    return any(x in text for x in ("japanese", "日本語", "language:japanese", "lang:japanese", "language:ja"))


def thumbnail_of(raw: dict) -> str:
    for key in ("thumbnail", "thumb", "cover", "poster", "image"):
        v = raw.get(key)
        if isinstance(v, str) and v.startswith(("http://","https://","//")):
            return ("https:"+v) if v.startswith("//") else v
        if isinstance(v, dict):
            for kk in ("url","src","source","thumbnail"):
                vv = v.get(kk)
                if isinstance(vv,str) and vv.startswith(("http://","https://","//")):
                    return ("https:"+vv) if vv.startswith("//") else vv
    return ""


def normalize(raw: dict) -> dict | None:
    sid = clean(first(raw, "id", "gallery_id", "galleryId", "book", "source_id", "gid"))
    title = clean(first(raw, "title", "name", "pretty", "japanese", "english"))
    if not sid or not title: return None
    ts = tags(first(raw, "tags", "tag", "metadata"))
    if not is_japanese(raw, ts): return None
    try: pages = int(first(raw, "total", "pages", "page_count", "num_pages") or 0)
    except Exception: pages = 0
    def field(*ks):
        out=[]
        for k in ks: out.extend(names(raw.get(k)))
        return list(dict.fromkeys(out))
    return {
        "uid": f"asmhentai:{sid}", "source":"asmhentai", "source_id":sid,
        "source_url": f"https://asmhentai.com/g/{sid}/", "source_url_kind":"direct",
        "title":title, "title_jp":clean(first(raw,"japanese","title_jp")),
        "language":"japanese", "category":clean(first(raw,"category","type")),
        "artists":field("artists","artist"), "groups":field("groups","group","circles","circle"),
        "parodies":field("parodies","parody","series"), "works":field("parodies","parody","series"),
        "characters":field("characters","character"), "tags":ts, "pages":pages,
        "rating":None, "popularity":None, "posted_at":"",
        "thumbnail":thumbnail_of(raw),
        "fallback_search_url":f"https://asmhentai.com/search/?q={quote_plus(title)}",
    }


def collect(state: dict | None = None):
    state=dict(state or {}); sess=requests.Session()
    found={}; errors=[]; rejected_non_japanese=0
    for page in range(1,PAGES+1):
        try:
            r=sess.get(f"{API_BASE}/asmhentai/search",params={"key":SEARCH_KEY,"page":page,"sort":"latest"},timeout=TIMEOUT)
            r.raise_for_status()
            rows=walk(r.json())
            for raw in rows:
                item=normalize(raw)
                if item: found[item["source_id"]]=item
                else: rejected_non_japanese+=1
                if len(found)>=LIMIT: break
        except Exception as e:
            errors.append(f"page={page}: {str(e)[:300]}")
        if len(found)>=LIMIT: break
    items=list(found.values())[:LIMIT]
    return items,state,{"status":"ok" if items else "error","discovered":len(found),"accepted_raw":len(items),
        "rejected_non_japanese":rejected_non_japanese,"tagged_items":sum(bool(x["tags"]) for x in items),
        "thumbnail_urls":sum(bool(x["thumbnail"]) for x in items),"message":" | ".join(errors[:4])}
