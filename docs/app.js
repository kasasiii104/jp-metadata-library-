"use strict";
(() => {
  const $ = (s) => document.querySelector(s);
  const $$ = (s) => [...document.querySelectorAll(s)];
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
  const safeUrl = (u) => { try { const x = new URL(String(u || ""), location.href); return /^https?:$/.test(x.protocol) ? x.href : ""; } catch { return ""; } };
  const uniq = (a) => [...new Set((a || []).filter(Boolean))];
  const sourceLabel = (s) => ({ehentai:"E-Hentai",hitomi:"Hitomi","3hentai":"3Hentai",asmhentai:"ASMHentai"})[s] || s;
  const INITIAL = 40, STEP = 40, INITIAL_CHUNKS = 2;
  const state = { raw:[], items:[], filtered:[], shown:INITIAL, source:"all", favoritesOnly:false, manifest:null, loadedChunks:new Set(), loading:false };

  function readSet(key) {
    try { const v = JSON.parse(localStorage.getItem(key) || "[]"); return new Set(Array.isArray(v) ? v : []); }
    catch { localStorage.removeItem(key); return new Set(); }
  }
  function saveSet(key, set) { try { localStorage.setItem(key, JSON.stringify([...set])); } catch {} }
  const favs = readSet("meta_favorites"), hidden = readSet("meta_hidden");

  const JP = {
    "big breasts":"巨乳","huge breasts":"爆乳","small breasts":"貧乳","sole female":"女性1人","sole male":"男性1人",
    "full color":"フルカラー","schoolgirl uniform":"制服","schoolgirl":"女子学生","swimsuit":"水着","bikini":"ビキニ",
    "glasses":"眼鏡","ponytail":"ポニーテール","twintails":"ツインテール","long hair":"ロングヘア","short hair":"ショートヘア",
    "blowjob":"フェラ","handjob":"手コキ","paizuri":"パイズリ","nakadashi":"中出し","creampie":"中出し","anal":"アナル",
    "masturbation":"オナニー","toys":"玩具","sex toys":"大人のおもちゃ","bondage":"拘束","group sex":"乱交","threesome":"3P",
    "femdom":"女性優位","mind control":"洗脳","pregnant":"妊婦","pregnancy":"妊娠","lactation":"母乳","milf":"熟女","mature":"熟女",
    "teacher":"教師","nurse":"ナース","maid":"メイド","office lady":"OL","gyaru":"ギャル","cosplay":"コスプレ","catgirl":"猫耳",
    "uncensored":"無修正","censored":"修正あり","mosaic censorship":"モザイク","original":"オリジナル","doujinshi":"同人誌","manga":"漫画",
    "artist cg":"CG集","game cg":"ゲームCG","japanese":"日本語","fate grand order":"Fate/Grand Order","kantai collection":"艦隊これくしょん",
    "azur lane":"アズールレーン","blue archive":"ブルーアーカイブ","touhou project":"東方Project","pokemon":"ポケットモンスター",
    "one piece":"ONE PIECE","naruto":"NARUTO","dragon ball":"ドラゴンボール","evangelion":"新世紀エヴァンゲリオン",
    "the idolmaster":"アイドルマスター","idolmaster":"アイドルマスター","love live":"ラブライブ！","genshin impact":"原神",
    "honkai star rail":"崩壊：スターレイル","uma musume":"ウマ娘 プリティーダービー","demon slayer":"鬼滅の刃","jujutsu kaisen":"呪術廻戦",
    "my hero academia":"僕のヒーローアカデミア","spy x family":"SPY×FAMILY","chainsaw man":"チェンソーマン","frieren":"葬送のフリーレン",
    "oshi no ko":"【推しの子】","bocchi the rock":"ぼっち・ざ・ろっく！","lycoris recoil":"リコリス・リコイル","konosuba":"この素晴らしい世界に祝福を！",
    "re zero":"Re:ゼロから始める異世界生活","rezero":"Re:ゼロから始める異世界生活","sword art online":"ソードアート・オンライン",
    "attack on titan":"進撃の巨人","hololive":"ホロライブ","nijisanji":"にじさんじ","arknights":"アークナイツ","nikke":"勝利の女神：NIKKE",
    "granblue fantasy":"グランブルーファンタジー","final fantasy":"ファイナルファンタジー","dragon quest":"ドラゴンクエスト",
    "netorare":"寝取られ","ntr":"寝取られ","netori":"寝取り","ahegao":"アヘ顔","deepthroat":"イラマチオ","footjob":"足コキ",
    "titjob":"パイズリ","cunnilingus":"クンニ","voyeurism":"覗き","exhibitionism":"露出","tentacles":"触手","futanari":"ふたなり",
    "yaoi":"BL","yuri":"百合","crossdressing":"女装","stockings":"ストッキング","pantyhose":"パンスト","lingerie":"ランジェリー",
    "harem":"ハーレム","incest":"近親相姦","mother":"母親","daughter":"娘","older sister":"姉","younger sister":"妹","childhood friend":"幼なじみ"
  };
  function jpTerm(v) {
    const raw = String(v || "").trim(), m = raw.match(/^(male|female|mixed|other):(.*)$/i);
    const ns = m ? ({male:"男性",female:"女性",mixed:"男女",other:"その他"})[m[1].toLowerCase()] : "";
    const base = (m ? m[2] : raw).trim(), out = JP[base.toLowerCase()] || base;
    return ns ? `${ns}：${out}` : out;
  }
  function displayValue(kind, v) {
    const raw = String(v || "").replace(/^(?:parody|work|character|tag):/i, "").trim();
    return ["works","characters","tags"].includes(kind) ? jpTerm(raw) : raw;
  }
  function titleOf(x) { return x.title_jp || x.title || `${x.source}:${x.source_id}`; }
  function worksOf(x) { return uniq([...(x.works || []), ...(x.parodies || [])]); }
  function contentTagsOf(x) { return uniq(x.tags || []).filter(v => !/^(?:language|artist|group|parody|work|character):/i.test(String(v || "").trim())); }
  function fieldValues(x, key) { if (key === "works") return worksOf(x); if (key === "tags") return contentTagsOf(x); return x[key] || []; }
  function dateScore(x) { const n = Date.parse(x.posted_at || ""); return Number.isFinite(n) ? n : 0; }
  function relative(v) { const t = Date.parse(v || ""); if (!Number.isFinite(t)) return ""; const d = Math.max(0, Date.now()-t), days=Math.floor(d/86400000); if(days<1)return"今日"; if(days<7)return`${days}日前`; return new Date(t).toLocaleDateString("ja-JP"); }
  function localThumb(u) { return u && !/^https?:/i.test(u); }
  function memberScore(x){let n=0;if(localThumb(x.thumbnail))n+=80;else if(x.thumbnail)n+=40;if(x.title_jp)n+=20;if(x.rating!=null)n+=8;if(x.artists?.length)n+=5;if(x.source==="ehentai")n+=3;return n;}

  function collapseGroups(items) {
    const map = new Map();
    for (const x of items) { const key=x.duplicate_group||x.uid; if(!map.has(key))map.set(key,[]); map.get(key).push(x); }
    const out=[];
    for (const [key,members] of map) {
      members.sort((a,b)=>memberScore(b)-memberScore(a)||dateScore(b)-dateScore(a));
      const p={...members[0]}; p.uid=key; p._groupId=key; p._members=members; p._sources=uniq(members.map(x=>x.source));
      p._categories=uniq(members.map(x=>x.category)); p.thumbnail=members.find(x=>localThumb(x.thumbnail))?.thumbnail||members.find(x=>x.thumbnail)?.thumbnail||"";
      p.artists=uniq(members.flatMap(x=>x.artists||[]));p.groups=uniq(members.flatMap(x=>x.groups||[]));p.works=uniq(members.flatMap(x=>x.works||x.parodies||[]));
      p.parodies=uniq(members.flatMap(x=>x.parodies||[]));p.characters=uniq(members.flatMap(x=>x.characters||[]));p.tags=uniq(members.flatMap(x=>x.tags||[]));
      p.pages=Math.max(...members.map(x=>Number(x.pages||0)),0);p.duplicate_count=members.length;
      if(members.some(x=>favs.has(x.uid)))favs.add(key); if(members.every(x=>hidden.has(x.uid)))hidden.add(key); out.push(p);
    }
    saveSet("meta_favorites",favs);saveSet("meta_hidden",hidden);return out;
  }
  function searchable(x){return [x.title,x.title_jp,x.category,...(x.artists||[]),...(x.groups||[]),...worksOf(x),...(x.characters||[]),...(x.tags||[])].join(" ").toLowerCase();}
  function sourceWorkUrl(m){if(m?.source==="3hentai"){const d=safeUrl(m.source_url);if(m.source_url_kind==="direct"&&d)return d;const q=String(m.title_jp||m.title||"").trim();return q?`https://3hentai.net/search?q=${encodeURIComponent(q)}`:"https://3hentai.net/";}return safeUrl(m?.source_url);}
  function facetButton(kind,value,label){return `<button class="facet" type="button" data-facet-kind="${esc(kind)}" data-facet-value="${esc(value)}">${esc(label)}</button>`;}
  function facetSummary(x){const a=[];const w=worksOf(x)[0];if(w)a.push(facetButton("works",w,`作品：${displayValue("works",w)}`));if(x.characters?.[0])a.push(facetButton("characters",x.characters[0],`キャラ：${displayValue("characters",x.characters[0])}`));for(const t of contentTagsOf(x).slice(0,2))a.push(facetButton("tags",t,`#${displayValue("tags",t)}`));return a.slice(0,4).join("");}
  function metaLine(x){const a=[];if(x.rating!=null&&Number(x.rating)>0)a.push(`★${Number(x.rating).toFixed(2).replace(/0+$/,"").replace(/\.$/,"")}`);if(x.pages)a.push(`${x.pages}p`);if(x.posted_at)a.push(relative(x.posted_at));return a.join(" ・ ");}
  function card(x){const thumb=safeUrl(x.thumbnail),url=sourceWorkUrl(x),id=x._groupId,labels=x._sources.map(sourceLabel).join(" / ");const dup=x.duplicate_count>1?`<span class="dupe">同一作品 ${x.duplicate_count}ソース</span>`:"";return `<article class="card"><a class="thumb" href="${esc(url||"#")}" target="_blank" rel="noopener noreferrer">${thumb?`<img src="${esc(thumb)}" alt="" loading="lazy" referrerpolicy="no-referrer">`:`<div class="ph">サムネイルなし</div>`}<span class="source">${esc(labels)}</span>${dup}${favs.has(id)?'<span class="favmark">★</span>':""}</a><div class="body"><a class="title" href="${esc(url||"#")}" target="_blank" rel="noopener noreferrer">${esc(titleOf(x))}</a><div class="facetline">${facetSummary(x)}</div><p class="meta">${esc(metaLine(x))}</p><button class="more" data-more="${esc(id)}">⋮</button></div></article>`;}

  function refreshFacetValues(selected="") { const kind=$("#facetKind").value, sel=$("#facetValue"); sel.innerHTML='<option value="">すべて</option>'; if(!kind){sel.disabled=true;return;} const counts=new Map(); for(const x of state.items)for(const v of fieldValues(x,kind))if(v)counts.set(v,(counts.get(v)||0)+1); for(const [v,n] of [...counts].sort((a,b)=>b[1]-a[1]||a[0].localeCompare(b[0],"ja")).slice(0,500)){const o=document.createElement("option");o.value=v;o.textContent=`${displayValue(kind,v)} (${n})`;sel.appendChild(o);}sel.disabled=false;if(selected)sel.value=selected; }
  function updateActiveFacet(){const kind=$("#facetKind").value,value=$("#facetValue").value,b=$("#activeFacet");if(!kind||!value){b.hidden=true;return;}const labels={artists:"作者",groups:"グループ",works:"作品",characters:"キャラ",tags:"タグ"};b.hidden=false;b.textContent=`${labels[kind]||kind}: ${displayValue(kind,value)} ×`;}
  function apply(reset=true){const q=$("#q").value.trim().toLowerCase(),cat=$("#category").value,minPages=Number($("#pages").value||0),sort=$("#sort").value,kind=$("#facetKind").value,val=$("#facetValue").value;let a=state.items.filter(x=>!hidden.has(x._groupId));if(state.source!=="all")a=a.filter(x=>x._sources.includes(state.source));if(state.favoritesOnly)a=a.filter(x=>favs.has(x._groupId));if(q)a=a.filter(x=>searchable(x).includes(q));if(cat)a=a.filter(x=>x._categories.includes(cat));if(kind&&val)a=a.filter(x=>fieldValues(x,kind).includes(val));if(minPages)a=a.filter(x=>Number(x.pages||0)>=minPages);a.sort((a,b)=>sort==="rating"?(Number(b.rating||-1)-Number(a.rating||-1))||dateScore(b)-dateScore(a):sort==="pages"?(Number(b.pages||0)-Number(a.pages||0))||dateScore(b)-dateScore(a):dateScore(b)-dateScore(a));state.filtered=a;if(reset)state.shown=INITIAL;updateActiveFacet();render();}
  function render(){const list=state.filtered.slice(0,state.shown);$("#grid").innerHTML=list.length?list.map(card).join(""):'<div class="empty">該当作品がありません</div>';const total=Number(state.manifest?.item_count||0),loaded=state.raw.length;$("#count").textContent=`${state.filtered.length.toLocaleString()}件表示対象 ・ ${loaded.toLocaleString()}/${total.toLocaleString()}件読込`;$("#loadMore").hidden=(state.shown>=state.filtered.length && state.loadedChunks.size>=Number(state.manifest?.chunk_count||0));$("#loadMore").textContent=state.loading?"読み込み中…":state.shown<state.filtered.length?"もっと見る":"次の作品を読み込む";}

  async function loadChunk(i){if(state.loadedChunks.has(i))return;const r=await fetch(`catalog/chunk-${String(i).padStart(3,"0")}.json?v=${encodeURIComponent(state.manifest?.version||"")}`,{cache:"no-store"});if(!r.ok)throw new Error(`chunk ${i}: HTTP ${r.status}`);const d=await r.json();if(!Array.isArray(d.items))throw new Error(`chunk ${i}: invalid`);state.raw.push(...d.items);state.loadedChunks.add(i);}
  function rebuild(){state.items=collapseGroups(state.raw);const cats=[...new Set(state.items.flatMap(x=>x._categories).filter(Boolean))].sort((a,b)=>a.localeCompare(b,"ja"));const current=$("#category").value;$("#category").innerHTML='<option value="">全カテゴリ</option>'+cats.map(c=>`<option value="${esc(c)}">${esc(c)}</option>`).join("");$("#category").value=current;refreshFacetValues($("#facetValue").value);apply(false);}
  async function loadNextChunk(){if(state.loading)return;const n=Number(state.manifest?.chunk_count||0);const next=[...Array(n).keys()].find(i=>!state.loadedChunks.has(i));if(next==null)return;state.loading=true;render();try{await loadChunk(next);rebuild();}catch(e){$("#status").textContent=`追加データ読込失敗: ${e.message}`;}finally{state.loading=false;render();}}
  async function loadAll(){if(state.loading)return;state.loading=true;$("#loadAll").disabled=true;try{const n=Number(state.manifest?.chunk_count||0);for(let i=0;i<n;i++){if(state.loadedChunks.has(i))continue;$("#status").textContent=`全件読み込み ${i+1}/${n}`;await loadChunk(i);if(i%4===0)await new Promise(r=>setTimeout(r,0));}rebuild();$("#status").textContent=`全${Number(state.manifest.item_count).toLocaleString()}件を読み込み済み`;}catch(e){$("#status").textContent=`全件読込失敗: ${e.message}`;}finally{state.loading=false;$("#loadAll").disabled=false;render();}}
  function detailBlock(label,values,kind){values=uniq(values||[]);if(!values.length)return"";return `<div class="detailblock"><div class="detailtitle">${esc(label)}</div><div class="detailchips">${values.slice(0,36).map(v=>facetButton(kind,v,kind==="tags"?`#${displayValue(kind,v)}`:displayValue(kind,v))).join("")}</div></div>`;}
  function menu(id){const x=state.items.find(i=>i._groupId===id);if(!x)return;const links=x._members.map(m=>`<a href="${esc(sourceWorkUrl(m)||"#")}" target="_blank" rel="noopener noreferrer">${esc(sourceLabel(m.source))}で見る</a>`).join("");$("#sheet").innerHTML=detailBlock("作品・原作",worksOf(x),"works")+detailBlock("キャラクター",x.characters,"characters")+detailBlock("タグ",contentTagsOf(x),"tags")+`<button data-act="fav">${favs.has(id)?"お気に入りから外す":"お気に入りに追加"}</button><button data-act="hide">この作品を非表示</button>${links}`;$("#sheetBg").classList.add("on");$("#sheet").classList.add("on");$("#sheet [data-act=fav]").onclick=()=>{favs.has(id)?favs.delete(id):favs.add(id);saveSet("meta_favorites",favs);closeSheet();apply();};$("#sheet [data-act=hide]").onclick=()=>{hidden.add(id);saveSet("meta_hidden",hidden);closeSheet();apply();};}
  function closeSheet(){$("#sheetBg").classList.remove("on");$("#sheet").classList.remove("on");}

  async function init(){try{const [manifest,status]=await Promise.all([fetch("catalog/manifest.json",{cache:"no-store"}).then(r=>{if(!r.ok)throw new Error(`manifest HTTP ${r.status}`);return r.json();}),fetch("source_status.json",{cache:"no-store"}).then(r=>r.ok?r.json():{}).catch(()=>({}))]);state.manifest=manifest;const n=Math.min(INITIAL_CHUNKS,Number(manifest.chunk_count||0));for(let i=0;i<n;i++)await loadChunk(i);rebuild();$("#status").textContent=`更新 ${new Date(manifest.updated_at).toLocaleString("ja-JP")} ・ 全${Number(manifest.item_count).toLocaleString()}件`;const parts=["ehentai","hitomi","3hentai","asmhentai"].map(k=>{const s=status[k]||{};return `${sourceLabel(k)}: ${s.status||"不明"}${s.accepted!=null?` ${s.accepted}件`:""}`;});$("#sourceStatus").textContent=parts.join(" / ");}catch(e){$("#status").textContent=`起動失敗: ${e.message}`;$("#grid").innerHTML='<div class="empty">公開データを読み込めませんでした。再読み込みしてください。</div>';}}

  let searchTimer=0;$("#q").addEventListener("input",()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>apply(),180);});$("#sort").addEventListener("change",()=>apply());$("#category").addEventListener("change",()=>apply());$("#facetKind").addEventListener("change",()=>{refreshFacetValues();apply();});$("#facetValue").addEventListener("change",()=>apply());$("#pages").addEventListener("change",()=>apply());
  $("#loadMore").onclick=async()=>{if(state.shown<state.filtered.length){state.shown+=STEP;render();}else await loadNextChunk();};$("#loadAll").onclick=loadAll;$("#favBtn").onclick=()=>{state.favoritesOnly=!state.favoritesOnly;$("#favBtn").textContent=state.favoritesOnly?"★のみ":"★";apply();};$("#activeFacet").onclick=()=>{$("#facetKind").value="";refreshFacetValues();$("#facetValue").value="";apply();};
  $$('[data-source]').forEach(b=>b.onclick=()=>{$$('[data-source]').forEach(x=>x.classList.remove("on"));b.classList.add("on");state.source=b.dataset.source;apply();});document.addEventListener("click",e=>{const f=e.target.closest("[data-facet-kind][data-facet-value]");if(f){e.preventDefault();$("#facetKind").value=f.dataset.facetKind;refreshFacetValues(f.dataset.facetValue);$("#facetValue").value=f.dataset.facetValue;closeSheet();apply();return;}const b=e.target.closest("[data-more]");if(b){e.preventDefault();menu(b.dataset.more);}});$("#sheetBg").onclick=closeSheet;
  init();
})();
