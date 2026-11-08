#!/usr/bin/env python3
import io, os, time
from pathlib import Path
import requests
from PIL import Image, ImageOps
if __package__:
    from .json_storage import load_json as load, save_json as save, validate_catalog, validate_source_mapping
else:
    from json_storage import load_json as load, save_json as save, validate_catalog, validate_source_mapping

DATA=Path("docs/data.json"); STATUS=Path("docs/source_status.json"); OUT=Path("docs/thumbs/asmhentai")
LIMIT=int(os.environ.get("ASMHENTAI_THUMB_LIMIT","80")); DELAY=float(os.environ.get("ASMHENTAI_THUMB_DELAY_SEC","0.4"))
TIMEOUT=30; MAX_BYTES=15*1024*1024


def cache_destination(sid: str) -> Path:
    """Resolve the fixed WebP destination and reject paths outside the cache."""
    root = OUT.resolve()
    dest = (root / f"{sid}.webp").resolve()
    if not dest.is_relative_to(root):
        raise ValueError(f"thumbnail destination outside cache: {sid!r}")
    return dest


def download(sess,url):
    try:
        r=sess.get(url,timeout=TIMEOUT,stream=True,headers={"Referer":"https://asmhentai.com/"})
        r.raise_for_status(); raw=r.raw.read(MAX_BYTES+1,decode_content=True)
        if not raw or len(raw)>MAX_BYTES:return None
        with Image.open(io.BytesIO(raw)) as im:
            im=ImageOps.exif_transpose(im)
            if im.mode!="RGB":
                if im.mode=="RGBA":
                    bg=Image.new("RGB",im.size,"white");bg.paste(im,mask=im.getchannel("A"));im=bg
                else: im=im.convert("RGB")
            im.thumbnail((520,760),Image.Resampling.LANCZOS)
            b=io.BytesIO();im.save(b,"WEBP",quality=78,method=4);return b.getvalue()
    except Exception:return None

def main():
    data=load(DATA,{"items":[]},validate_catalog)
    st=load(STATUS,{},validate_source_mapping)
    items=[x for x in data.get("items",[]) if x.get("source")=="asmhentai"]
    OUT.mkdir(parents=True,exist_ok=True); sess=requests.Session()
    sess.headers.update({"User-Agent":"Mozilla/5.0 (compatible; Japanese-Metadata-Library/2.0)"})
    attempted=cached=reused=failed=0
    for x in items:
        sid=str(x.get("source_id") or "").strip()
        if not sid:continue
        rel=f"thumbs/asmhentai/{sid}.webp"
        try:
            dest=cache_destination(sid)
        except (ValueError, OSError, RuntimeError) as e:
            failed+=1
            print(f"[asmhentai-thumbs] skipping {sid!r}: {e}")
            continue
        if dest.exists() and dest.stat().st_size>512:x["thumbnail"]=rel;reused+=1;continue
        if attempted>=LIMIT:continue
        url=str(x.get("thumbnail") or "")
        if not url.startswith(("http://","https://")):failed+=1;continue
        attempted+=1; raw=download(sess,url)
        if raw and len(raw)>512:dest.write_bytes(raw);x["thumbnail"]=rel;cached+=1
        else:failed+=1
        time.sleep(DELAY)
    save(DATA,data); src=st.setdefault("asmhentai",{})
    src["thumbnail_cache"]={"attempted":attempted,"cached":cached,"reused":reused,"failed":failed,"pending":max(0,len(items)-cached-reused)}
    save(STATUS,st); print(f"[asmhentai-thumbs] total={len(items)} attempted={attempted} cached={cached} reused={reused} failed={failed}")
if __name__=="__main__":main()
