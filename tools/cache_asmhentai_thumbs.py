#!/usr/bin/env python3
import io, json, os, time
from pathlib import Path
import requests
from PIL import Image, ImageOps

DATA=Path("docs/data.json"); STATUS=Path("docs/source_status.json"); OUT=Path("docs/thumbs/asmhentai")
LIMIT=int(os.environ.get("ASMHENTAI_THUMB_LIMIT","80")); DELAY=float(os.environ.get("ASMHENTAI_THUMB_DELAY_SEC","0.4"))
TIMEOUT=30; MAX_BYTES=15*1024*1024

def load(p,d):
    try:return json.loads(p.read_text(encoding="utf-8"))
    except Exception:return d
def save(p,o):p.write_text(json.dumps(o,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")

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
    data=load(DATA,{"items":[]}); items=[x for x in data.get("items",[]) if x.get("source")=="asmhentai"]
    OUT.mkdir(parents=True,exist_ok=True); sess=requests.Session()
    sess.headers.update({"User-Agent":"Mozilla/5.0 (compatible; Japanese-Metadata-Library/2.0)"})
    attempted=cached=reused=failed=0
    for x in items:
        sid=str(x.get("source_id") or "").strip()
        if not sid:continue
        rel=f"thumbs/asmhentai/{sid}.webp"; dest=Path("docs")/rel
        if dest.exists() and dest.stat().st_size>512:x["thumbnail"]=rel;reused+=1;continue
        if attempted>=LIMIT:continue
        url=str(x.get("thumbnail") or "")
        if not url.startswith(("http://","https://")):failed+=1;continue
        attempted+=1; raw=download(sess,url)
        if raw and len(raw)>512:dest.write_bytes(raw);x["thumbnail"]=rel;cached+=1
        else:failed+=1
        time.sleep(DELAY)
    save(DATA,data); st=load(STATUS,{}); src=st.setdefault("asmhentai",{})
    src["thumbnail_cache"]={"attempted":attempted,"cached":cached,"reused":reused,"failed":failed,"pending":max(0,len(items)-cached-reused)}
    save(STATUS,st); print(f"[asmhentai-thumbs] total={len(items)} attempted={attempted} cached={cached} reused={reused} failed={failed}")
if __name__=="__main__":main()
