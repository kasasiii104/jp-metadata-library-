"use strict";
(() => {
  const EMBED_WORKER_BASE = "https://jp-embed.kasasiii104.workers.dev";
  const excluded = new Set(["lolicon","shotacon","loli","shota","oppai loli","toddlercon","low lolicon","low shotacon","low loli","low shota"]);
  function normalizeReaderTag(value) {
    return String(value ?? "").normalize("NFKC").toLowerCase().split(/:(.*)/s, 2)
      .map(part => part.replace(/[’‘]/g, "'").replace(/[_-]/g, " ").replace(/\s+/g, " ").trim())
      .join(":");
  }
  const uidPattern = /^(ehentai|hitomi|3hentai|asmhentai|nhentai|hentaifox):[0-9A-Za-z_-]{1,40}$/;

  function readerIdentityVerified(record) {
    if (typeof record?.uid !== "string" || !uidPattern.test(record.uid) || record.source_id == null) return false;
    const [source, id] = record.uid.split(":");
    if (record.source !== source || String(record.source_id) !== id) return false;
    if (source === "ehentai" || source === "3hentai") {
      if (!URL.canParse(record.source_url)) return false;
      const { protocol, username, password, port, hostname, pathname } = new URL(record.source_url);
      const expectedHost = source === "ehentai" ? "e-hentai.org" : "3hentai.net";
      // URL normalizes an explicit :443 away. Require a bare hostname in the original authority.
      const authority = /^https:\/\/([^/?#\\]+)/i.exec(String(record.source_url).trim())?.[1];
      if (protocol !== "https:" || username || password || port || hostname !== expectedHost ||
          authority?.toLowerCase() !== hostname) return false;
      if (source === "ehentai") return /^\/g\/(\d+)\/[a-z0-9]+\/$/i.exec(pathname)?.[1] === id;
      return /^3\d+$/.test(id) && /^\/d\/(\d+)$/.exec(pathname)?.[1] === id.slice(1);
    }
    return true;
  }

  function readerTagsVerified(record) {
    return Array.isArray(record.tags) && record.tags.length > 0 &&
      record.tags.every(tag => typeof tag === "string");
  }

  function isReaderExcluded(record) {
    return readerTagsVerified(record) && record.tags.some(tag => {
      const normalized = normalizeReaderTag(tag);
      const base = normalized.slice(normalized.lastIndexOf(":") + 1).trim()
        .replace(/\s*\(\s*(?:female|male|other|mixed|f|m)\s*\)$/, "").trim();
      return excluded.has(base) ||
        /ロリコン|ショタコン/.test(String(tag).normalize("NFKC"));
    });
  }

  function readerMemberUids(record) {
    const siblings = record.duplicate_uids ?? [];
    if (!Array.isArray(siblings)) return null;
    const members = new Set([record.uid, ...siblings]);
    const grouped = Object.hasOwn(record, "duplicate_group") || record.duplicate_count > 1 || siblings.length;
    if (!grouped) return members;
    return typeof record.duplicate_group === "string" && record.duplicate_group.trim() &&
      Number.isInteger(record.duplicate_count) && record.duplicate_count >= 2 &&
      members.size === record.duplicate_count ? members : null;
  }

  function readerSiblingMatches(record, members, sibling) {
    const siblingMembers = readerMemberUids(sibling);
    return sibling.duplicate_group === record.duplicate_group &&
      sibling.duplicate_count === record.duplicate_count &&
      siblingMembers?.size === members.size &&
      [...siblingMembers].every(uid => members.has(uid));
  }

  function readerBlocked(group) {
    return (group._members || [group]).some(isReaderExcluded);
  }
  function indexReaderCatalog(items) {
    const uidCounts = new Map();
    const references = new Map();
    for (const item of items) {
      uidCounts.set(item.uid, (uidCounts.get(item.uid) ?? 0) + 1);
      if (!Array.isArray(item.duplicate_uids)) continue;
      for (const uid of item.duplicate_uids) {
        if (!references.has(uid)) references.set(uid, new Set());
        references.get(uid).add(item.uid);
      }
    }
    return { uidCounts, references };
  }
  function readerManifestVerified(manifest) {
    return typeof manifest?.version === "string" && manifest.version.trim().length > 0 &&
      Number.isInteger(manifest.chunk_count) && manifest.chunk_count >= 1 && manifest.chunk_count <= 1000 &&
      Number.isInteger(manifest.item_count) && manifest.item_count >= 1;
  }
  function canRead(group, catalog, manifest) {
    if (!readerManifestVerified(manifest)) return false;
    const siblings = group._members;
    if (!siblings?.length || !siblings.every(readerTagsVerified) ||
        !siblings.every(readerIdentityVerified) || readerBlocked(group)) return false;
    const record = siblings[0];
    const members = readerMemberUids(record);
    const loaded = new Set(siblings.map(sibling => sibling.uid));
    return members?.size === loaded.size && loaded.size === siblings.length &&
      siblings.every(sibling => Number.isInteger(sibling._chunk) && members.has(sibling.uid) &&
        readerSiblingMatches(record, members, sibling)) &&
      (!catalog || [...members].every(uid => catalog.uidCounts.get(uid) === 1 &&
        [...(catalog.references.get(uid) ?? [])].every(referrer => members.has(referrer))));
  }
  function isReadableMember(member) {
    if (["hitomi", "3hentai", "asmhentai", "hentaifox", "ehentai"].includes(member.source)) return true;
    const { reader_media_id: media, reader_exts: exts, pages } = member;
    return member.source === "nhentai" &&
      typeof media === "string" && /^[1-9][0-9]{0,11}$/.test(media) &&
      typeof exts === "string" && /^[wjpg]{1,2000}$/.test(exts) &&
      Number.isInteger(pages) && pages === exts.length;
  }
  function readerGroupChunks(member, members) {
    const siblingUids = new Set(member.duplicate_uids || []);
    return [...new Set(members.filter(sibling => sibling.uid !== member.uid && siblingUids.has(sibling.uid) &&
      Math.abs(sibling._chunk - member._chunk) > 1).map(sibling => sibling._chunk))].slice(0, 6);
  }
  const dateCache = new Map();
  function parseDate(value) {
    if (typeof value !== "string") return NaN;
    if (dateCache.has(value)) return dateCache.get(value);
    const trimmed = value.trim();
    const parts = trimmed.match(/^(\d{4})-(\d{2})-(\d{2})/);
    let result;
    if (!parts) result = Date.parse(value);
    else {
      const [,y,m,d] = parts.map(Number);
      const leap = y%4===0 && (y%100!==0 || y%400===0);
      const days = [31,leap?29:28,31,30,31,30,31,31,30,31,30,31];
      if (m<1 || m>12 || d<1 || d>days[m-1]) result = NaN;
      else {
        const normalized = trimmed.replace(/^(\d{4}-\d{2}-\d{2}) /, "$1T").replace(/([+-]\d{2})(\d{2})$/, "$1:$2").replace(/(T.*)([+-]\d{2})$/, "$1$2:00");
        const utc = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?$/.test(normalized) ? `${normalized}Z` : normalized;
        result = Date.parse(trimmed.length===10 ? `${trimmed}T00:00:00Z` : utc);
      }
    }
    dateCache.set(value, result);
    return result;
  }
  const defaults = {q:"",src:"all",cat:"",fk:"",fv:"",sort:"new",pages:"0"};
  function decodeState(query) {
    const p = new URLSearchParams(query), out = {...defaults};
    for (const k of ["q","cat","fv"]) out[k] = p.get(k) || "";
    for (const [k, allowed] of Object.entries({src:["all","ehentai","hitomi","3hentai","asmhentai","nhentai"],fk:["","artists","groups","works","characters","tags"],sort:["new","rating","pages"],pages:["0","20","40","80"]})) {
      const v=p.get(k); if(allowed.includes(v))out[k]=v;
    }
    if(!out.fk)out.fv="";
    return out;
  }
  function encodeState(value) {
    const v=decodeState(new URLSearchParams(value)), p=new URLSearchParams();
    for(const k of Object.keys(defaults))if(v[k]!==defaults[k])p.set(k,v[k]);
    return p.toString();
  }
  function seededOrder(items, seed) {
    const hash = uid => { let h=2166136261; for(const c of `${seed}:${uid}`)h=Math.imul(h^c.charCodeAt(0),16777619); return h>>>0; };
    return [...items].sort((a,b)=>hash(a.uid)-hash(b.uid)||String(a.uid).localeCompare(String(b.uid)));
  }
  function isNew(item, since) {
    return (item._members || [item]).some(x=>{
      const firstSeen=parseDate(x.first_seen);
      return (Number.isFinite(firstSeen)?firstSeen:parseDate(x.posted_at))>since;
    });
  }
  function decideSince(updated, previous, session, now=Date.now()) {
    const current=Number.isFinite(parseDate(updated))?parseDate(updated):now;
    const number=v=>typeof v==="string"&&/^\d{1,16}$/.test(v)&&Number(v)>0&&Number(v)<=current?Number(v):NaN;
    const old=number(previous), fixed=number(session);
    return {since:Number.isFinite(fixed)?fixed:Number.isFinite(old)?old:current,lastSeen:current};
  }
  function initializeSince(updated, local, session, now) {
    const read=(s,k)=>{try{return s()?.getItem(k);}catch{return null;}};
    const result=decideSince(updated,read(local,"meta_last_seen"),read(session,"meta_new_since"),now);
    try{local()?.setItem("meta_last_seen",String(result.lastSeen));}catch{}
    try{session()?.setItem("meta_new_since",String(result.since));}catch{}
    return result.since;
  }
  if(typeof module !== "undefined" && module.exports) { module.exports={parseDate,decodeState,encodeState,seededOrder,isNew,decideSince,initializeSince,readerBlocked,isReaderExcluded,normalizeReaderTag,readerIdentityVerified,readerManifestVerified,canRead,indexReaderCatalog,readerGroupChunks}; return; }

  const $ = (s) => document.querySelector(s);
  const $$ = (s) => [...document.querySelectorAll(s)];
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
  const safeUrl = (u) => { try { const x = new URL(String(u || ""), location.href); return /^https?:$/.test(x.protocol) ? x.href : ""; } catch { return ""; } };
  const uniq = (a) => [...new Set((a || []).filter(Boolean))];
  const sourceLabel = (s) => ({ehentai:"E-Hentai",hitomi:"Hitomi","3hentai":"3Hentai",asmhentai:"ASMHentai",nhentai:"nhentai"})[s] || s;
  const INITIAL = 40, STEP = 40, INITIAL_CHUNKS = 2;
  const state = { raw:[], items:[], filtered:[], shown:INITIAL, source:"all", favoritesOnly:false, manifest:null, loadedChunks:new Set(), loading:false, seed:null, newOnly:false, since:NaN, newCount:0 };

  function readSet(key) {
    try { const v = JSON.parse(localStorage.getItem(key) || "[]"); return new Set(Array.isArray(v) ? v : []); }
    catch { try { localStorage.removeItem(key); } catch {} return new Set(); }
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
  function contentTagsOf(x) { return uniq(x.tags).filter(v => !/^(?:language|artist|group|parody|work|character):/i.test(String(v || "").trim())); }
  function fieldValues(x, key) { if (key === "works") return worksOf(x); if (key === "tags") return contentTagsOf(x); return x[key] || []; }
  function dateScore(x) { const n = parseDate(x.posted_at); return Number.isFinite(n) ? n : 0; }
  function relative(v) { const t = parseDate(v); if (!Number.isFinite(t)) return ""; const d = Math.max(0, Date.now()-t), days=Math.floor(d/86400000); if(days<1)return"今日"; if(days<7)return`${days}日前`; return new Date(t).toLocaleDateString("ja-JP"); }
  function localThumb(u) { return u && !/^https?:/i.test(u); }
  function memberScore(x){let n=0;if(localThumb(x.thumbnail))n+=80;else if(x.thumbnail)n+=40;if(x.title_jp)n+=20;if(x.rating!=null)n+=8;if(x.artists?.length)n+=5;if(x.source==="ehentai")n+=3;return n;}

  let readerCatalog;
  function collapseGroups(items) {
    if (EMBED_WORKER_BASE) readerCatalog = indexReaderCatalog(items);
    const map = new Map();
    for (const x of items) { const key=x.duplicate_group||x.uid; if(!map.has(key))map.set(key,[]); map.get(key).push(x); }
    const out=[];
    for (const [key,members] of map) {
      members.sort((a,b)=>memberScore(b)-memberScore(a)||dateScore(b)-dateScore(a));
      const p={...members[0]}; p.uid=key; p._groupId=key; p._members=members; p._sources=uniq(members.map(x=>x.source));
      p._categories=uniq(members.map(x=>x.category)); p.thumbnail=members.find(x=>localThumb(x.thumbnail))?.thumbnail||members.find(x=>x.thumbnail)?.thumbnail||"";
      p.artists=uniq(members.flatMap(x=>x.artists||[]));p.groups=uniq(members.flatMap(x=>x.groups||[]));p.works=uniq(members.flatMap(x=>x.works||x.parodies||[]));
      p.parodies=uniq(members.flatMap(x=>x.parodies||[]));p.characters=uniq(members.flatMap(x=>x.characters||[]));p.tags=uniq(members.flatMap(x=>x.tags??[]));
      p.pages=Math.max(...members.map(x=>Number(x.pages||0)),0);p.duplicate_count=members.length;
      if(members.some(x=>favs.has(x.uid)))favs.add(key); if(members.every(x=>hidden.has(x.uid)))hidden.add(key); out.push(p);
    }
    saveSet("meta_favorites",favs);saveSet("meta_hidden",hidden);return out;
  }
  function searchable(x) {
    if (x._search != null) return x._search;
    const works=worksOf(x), characters=x.characters||[], tags=x.tags;
    const translated=[];
    for (const [kind,values] of [["works",works],["characters",characters],["tags",tags]]) {
      for (const v of values) { const label=displayValue(kind,v); if(label!==v)translated.push(label); }
    }
    return x._search=[x.title,x.title_jp,x.category,...(x.artists||[]),...(x.groups||[]),...works,...characters,...tags,...translated].join(" ").toLowerCase();
  }
  function sourceWorkUrl(m){if(m?.source==="3hentai"){const d=safeUrl(m.source_url);if(m.source_url_kind==="direct"&&d)return d;const q=String(m.title_jp||m.title||"").trim();return q?`https://3hentai.net/search?q=${encodeURIComponent(q)}`:"https://3hentai.net/";}return safeUrl(m?.source_url);}
  function facetButton(kind,value,label){return `<button class="facet" type="button" data-facet-kind="${esc(kind)}" data-facet-value="${esc(value)}">${esc(label)}</button>`;}
  function facetSummary(x){const a=[];const w=worksOf(x)[0];if(w)a.push(facetButton("works",w,`作品：${displayValue("works",w)}`));if(x.characters?.[0])a.push(facetButton("characters",x.characters[0],`キャラ：${displayValue("characters",x.characters[0])}`));for(const t of contentTagsOf(x).slice(0,2))a.push(facetButton("tags",t,`#${displayValue("tags",t)}`));return a.slice(0,4).join("");}
  function metaLine(x){const a=[];if(x.rating!=null&&Number(x.rating)>0)a.push(`★${Number(x.rating).toFixed(2).replace(/0+$/,"").replace(/\.$/,"")}`);if(x.pages)a.push(`${x.pages}p`);if(x.posted_at)a.push(relative(x.posted_at));return a.join(" ・ ");}
  function readerAvailable(group){return Boolean(EMBED_WORKER_BASE&&readerReady)&&canRead(group,readerCatalog,state.manifest)&&group._members.some(isReadableMember);}
  function card(x){
    const thumb=safeUrl(x.thumbnail),id=esc(x._groupId),title=esc(titleOf(x)),labels=x._sources.map(sourceLabel).join(" / ");
    const read=readerAvailable(x);
    const link=read?"":`href="${esc(sourceWorkUrl(x)||"#")}" target="_blank" rel="noopener noreferrer"`;
    const thumbContent=thumb?`<img src="${esc(thumb)}" alt="" loading="lazy" referrerpolicy="no-referrer">`
      :read?'<span class="ph">サムネイルなし</span>':'<div class="ph">サムネイルなし</div>';
    const dup=x.duplicate_count>1?`<span class="dupe">同一作品 ${x.duplicate_count}ソース</span>`:"";
    const thumbnail=thumbContent+`<span class="source">${esc(labels)}</span>`+dup+(favs.has(x._groupId)?'<span class="favmark">★</span>':"");
    const readAttrs=`type="button" data-read="${id}"`;
    const thumbControl=read?`<button class="thumb" ${readAttrs} aria-label="${title}を読む">${thumbnail}</button>`
      :`<a class="thumb" ${link}>${thumbnail}</a>`;
    const titleControl=read?`<button class="title" ${readAttrs}>${title}</button>`:`<a class="title" ${link}>${title}</a>`;
    return `<article class="card">${thumbControl}<div class="body">${titleControl}<div class="facetline">${facetSummary(x)}</div><p class="meta">${esc(metaLine(x))}</p>${read?`<button class="facet reader-open" ${readAttrs}>読む</button>`:""}<button class="more" data-more="${id}">⋮</button></div></article>`;
  }

  function refreshFacetValues(selected="") { const kind=$("#facetKind").value, sel=$("#facetValue"); sel.innerHTML='<option value="">すべて</option>'; if(!kind){sel.disabled=true;return;} const counts=new Map(); for(const x of state.items)for(const v of fieldValues(x,kind))if(v)counts.set(v,(counts.get(v)||0)+1); for(const [v,n] of [...counts].sort((a,b)=>b[1]-a[1]||String(a[0]).localeCompare(String(b[0]),"ja")).slice(0,500)){const o=document.createElement("option");o.value=v;o.textContent=`${displayValue(kind,v)} (${n})`;sel.appendChild(o);}if(selected&&![...sel.options].some(o=>o.value===selected)){const o=document.createElement("option");o.value=selected;const n=counts.get(selected);o.textContent=n?`${displayValue(kind,selected)} (${n})`:displayValue(kind,selected);sel.appendChild(o);}sel.disabled=false;if(selected)sel.value=selected; }
  function updateActiveFacet(){const kind=$("#facetKind").value,value=$("#facetValue").value,b=$("#activeFacet");if(!kind||!value){b.hidden=true;return;}const labels={artists:"作者",groups:"グループ",works:"作品",characters:"キャラ",tags:"タグ"};b.hidden=false;b.textContent=`${labels[kind]||kind}: ${displayValue(kind,value)} ×`;}
  function apply(reset=true){const q=$("#q").value.trim().toLowerCase(),cat=$("#category").value,minPages=Number($("#pages").value||0),sort=$("#sort").value,kind=$("#facetKind").value,val=$("#facetValue").value;let a=state.items.filter(x=>!hidden.has(x._groupId));if(state.source!=="all")a=a.filter(x=>x._sources.includes(state.source));if(state.favoritesOnly)a=a.filter(x=>favs.has(x._groupId));if(q)a=a.filter(x=>searchable(x).includes(q));if(cat)a=a.filter(x=>x._categories.includes(cat));if(kind&&val)a=a.filter(x=>fieldValues(x,kind).includes(val));if(minPages)a=a.filter(x=>Number(x.pages||0)>=minPages);a.sort((a,b)=>sort==="rating"?(Number(b.rating||-1)-Number(a.rating||-1))||dateScore(b)-dateScore(a):sort==="pages"?(Number(b.pages||0)-Number(a.pages||0))||dateScore(b)-dateScore(a):dateScore(b)-dateScore(a));if(state.newOnly)a=a.filter(x=>isNew(x,state.since));if(state.seed!==null)a=seededOrder(a,state.seed);state.filtered=a;if(reset)state.shown=INITIAL;updateActiveFacet();$("#randomActive").hidden=state.seed===null;render();}
  function render(){const list=state.filtered.slice(0,state.shown);$("#grid").innerHTML=list.length?list.map(card).join(""):'<div class="empty">該当作品がありません</div>';const total=Number(state.manifest?.item_count||0),loaded=state.raw.length;$("#count").textContent=`${state.filtered.length.toLocaleString()}件表示対象 ・ ${loaded.toLocaleString()}/${total.toLocaleString()}件読込`;$("#newBtn").textContent=`新着${state.newOnly?"表示中":""} (${state.newCount})`;
    if(state.newOnly)$("#count").textContent+=` ・ 新着 ${state.filtered.length}件（基準 ${new Date(state.since).toLocaleString("ja-JP")} 以降）`;
    $("#loadMore").hidden=(state.shown>=state.filtered.length && state.loadedChunks.size>=Number(state.manifest?.chunk_count||0));$("#loadMore").textContent=state.loading?"読み込み中…":state.shown<state.filtered.length?"もっと見る":"次の作品を読み込む";}

  async function loadChunk(i){if(state.loadedChunks.has(i))return;const r=await fetch(`catalog/chunk-${String(i).padStart(3,"0")}.json?v=${encodeURIComponent(state.manifest?.version||"")}`,{cache:"no-store"});if(!r.ok)throw new Error(`chunk ${i}: HTTP ${r.status}`);const d=await r.json();if(!Array.isArray(d.items))throw new Error(`chunk ${i}: invalid`);for(const item of d.items)item._chunk=i;state.raw.push(...d.items);state.loadedChunks.add(i);}
  function updateNewCount(){state.newCount=state.items.filter(x=>!hidden.has(x._groupId)&&isNew(x,state.since)).length;}
  function rebuild(){state.items=collapseGroups(state.raw);updateNewCount();const cats=[...new Set(state.items.flatMap(x=>x._categories).filter(Boolean))].sort((a,b)=>a.localeCompare(b,"ja"));const current=$("#category").value;if(current&&!cats.includes(current))cats.push(current);$("#category").innerHTML='<option value="">全カテゴリ</option>'+cats.map(c=>`<option value="${esc(c)}">${esc(c)}</option>`).join("");$("#category").value=current;refreshFacetValues($("#facetValue").value);apply(false);}
  async function loadNextChunk(){if(state.loading)return;const n=Number(state.manifest?.chunk_count||0);const next=[...Array(n).keys()].find(i=>!state.loadedChunks.has(i));if(next==null)return;state.loading=true;render();try{await loadChunk(next);rebuild();}catch(e){$("#status").textContent=`追加データ読込失敗: ${e.message}`;}finally{state.loading=false;render();}}
  async function loadAll(){if(state.loading)return;state.loading=true;$("#loadAll").disabled=true;try{const n=Number(state.manifest?.chunk_count||0);for(let i=0;i<n;i++){if(state.loadedChunks.has(i))continue;$("#status").textContent=`全件読み込み ${i+1}/${n}`;await loadChunk(i);if(i%4===0)await new Promise(r=>setTimeout(r,0));}rebuild();$("#status").textContent=`全${Number(state.manifest.item_count).toLocaleString()}件を読み込み済み`;}catch(e){$("#status").textContent=`全件読込失敗: ${e.message}`;}finally{state.loading=false;$("#loadAll").disabled=false;render();}}
  function detailBlock(label,values,kind){values=uniq(values||[]);if(!values.length)return"";return `<div class="detailblock"><div class="detailtitle">${esc(label)}</div><div class="detailchips">${values.slice(0,36).map(v=>facetButton(kind,v,kind==="tags"?`#${displayValue(kind,v)}`:displayValue(kind,v))).join("")}</div></div>`;}
  function menu(id){const x=state.items.find(i=>i._groupId===id);if(!x)return;const read=readerAvailable(x);const links=x._members.map(m=>`<a href="${esc(sourceWorkUrl(m)||"#")}" target="_blank" rel="noopener noreferrer">${esc(sourceLabel(m.source))}で見る</a>${read&&isReadableMember(m)?`<button type="button" data-read="${esc(id)}" data-read-uid="${esc(m.uid)}">${esc(sourceLabel(m.source))}で読む</button>`:""}`).join("");$("#sheet").innerHTML=detailBlock("作品・原作",worksOf(x),"works")+detailBlock("キャラクター",x.characters,"characters")+detailBlock("タグ",contentTagsOf(x),"tags")+`<button data-act="fav">${favs.has(id)?"お気に入りから外す":"お気に入りに追加"}</button><button data-act="hide">この作品を非表示</button>${links}`;$("#sheetBg").classList.add("on");$("#sheet").classList.add("on");$("#sheet [data-act=fav]").onclick=()=>{favs.has(id)?favs.delete(id):favs.add(id);saveSet("meta_favorites",favs);closeSheet();apply();};$("#sheet [data-act=hide]").onclick=()=>{hidden.add(id);saveSet("meta_hidden",hidden);updateNewCount();closeSheet();apply();};}
  function closeSheet(){$("#sheetBg").classList.remove("on");$("#sheet").classList.remove("on");}

  const reader=$("#reader"), readerPages=$("#readerPages");
  const readerReady=reader&&readerPages&&["#readerClose","#readerSource","#readerMember","#readerCount"].every(id=>$(id));
  let readerRequest, readerObserver, readerTrigger, readerReturnUrl;
  function hideReader(){
    readerRequest?.abort();readerObserver?.disconnect();
    if(reader.open)reader.close();
    readerPages.replaceChildren();document.body.classList.remove("reading");
    if(readerTrigger){
      const {id,className,element}=readerTrigger;
      let target=element;
      if(!target?.isConnected||!target.getClientRects().length){
        const controls=$$("[data-read], [data-more]").filter(b=>(b.dataset.read===id&&!b.dataset.readUid)||b.dataset.more===id);
        target=controls.find(b=>b.dataset.read===id&&b.className===className)||controls[0];
      }
      if(target?.getClientRects().length)target.focus();
    }
    readerTrigger=null;
  }
  function closeReader(){
    if(!reader.open)return;
    hideReader();
    if(location.hash==="#read"&&history.state?.reader){history.back();return;}
  }
  async function openReader(id,uid,trigger,push=true){
    const group=state.items.find(x=>x._groupId===id);
    if(!group||!readerAvailable(group)){if(reader?.open)hideReader();return;}
    const members=group._members.filter(isReadableMember);
    const member=uid!=null?members.find(m=>m.uid===uid):members.find(m=>m.source===state.source)||members[0];
    if(!member){if(reader.open)hideReader();return;}
    if(push){
      // Commit pending search input before reserving a separate history entry.
      clearTimeout(searchTimer);apply(false);writeUrl("replace");
      history.pushState({reader:{id,uid:member.uid}},"","#read");
    }
    readerReturnUrl=location.pathname+location.search;
    if(trigger){
      readerTrigger=trigger.closest("#sheet")?{id}:{id,className:trigger.className,element:trigger};
    }
    closeSheet();
    readerRequest?.abort();readerObserver?.disconnect();
    const request=readerRequest=new AbortController();
    const source=$("#readerSource"), select=$("#readerMember"), counter=$("#readerCount");
    source.href=sourceWorkUrl(member)||"#";
    select.replaceChildren(...members.map(m=>new Option(sourceLabel(m.source),m.uid)));
    select.value=member.uid;select.hidden=members.length<2;
    select.onchange=()=>{
      history.replaceState({reader:{id,uid:select.value}},"","#read");
      openReader(id,select.value,null,false);
    };
    readerPages.textContent="読み込み中…";counter.textContent="";
    if(!reader.open)reader.showModal();
    document.body.classList.add("reading");readerPages.scrollTop=0;
    try{
      const base=new URL(EMBED_WORKER_BASE,location.href);
      const endpoint=new URL("pages",base.href.replace(/\/?$/,"/"));
      endpoint.search=new URLSearchParams({uid:member.uid,c:member._chunk});
      const chunks=readerGroupChunks(member,group._members);
      if(chunks.length)endpoint.searchParams.set("g",chunks.join(","));
      let count=null, currentPage=null;
      const seen=new Set(), visible=new Set();
      const pageLabel=n=>count==null?String(n):`${n} / ${count}`;
      if(typeof IntersectionObserver!=="undefined"){
        readerObserver=new IntersectionObserver(entries=>{
          if(request.signal.aborted)return;
          for(const entry of entries){
            if(entry.isIntersecting)visible.add(entry.target);else visible.delete(entry.target);
          }
          const page=Math.min(...[...visible].map(figure=>Number(figure.dataset.page)));
          if(Number.isFinite(page)){currentPage=page;counter.textContent=pageLabel(page);}
        },{root:readerPages,rootMargin:"0px 0px -50% 0px"});
      }
      let from=0, lastImage=0, listingLast=0;
      do {
        if(from){
          endpoint.searchParams.set("from",from);
          endpoint.searchParams.set("last",lastImage);
          endpoint.searchParams.set("pl",listingLast);
        }
        const response=await fetch(endpoint,{signal:request.signal});
        if(!response.ok)throw new Error();
        const batch=await response.json();
        if(request.signal.aborted)return;
        if(!Array.isArray(batch.pages)||!batch.pages.some(({n})=>!seen.has(n)))throw new Error();
        if(member.source==="ehentai"){
          if(batch.pages.some(({n},offset)=>!Number.isSafeInteger(n)||n!==lastImage+offset+1))throw new Error();
          lastImage=batch.pages.at(-1).n;
        }
        if(batch.next!=null){
          if(!Number.isSafeInteger(batch.next)||batch.next<=from||member.source!=="ehentai"||
            !Number.isSafeInteger(batch.listing_last)||batch.listing_last<listingLast||
            batch.listing_last<batch.next)throw new Error();
          listingLast=batch.listing_last;
        }else if(member.source==="ehentai"&&batch.listing_last!=null&&
          (!Number.isSafeInteger(batch.listing_last)||batch.listing_last<listingLast))throw new Error();
        if(batch.count!=null&&batch.count!==count){
          count=batch.count;
          for(const figure of readerPages.children)figure.firstElementChild.textContent=pageLabel(figure.dataset.page);
        }
        const figures=[];
        for(const {n,url} of batch.pages){
          if(seen.has(n))continue;
          seen.add(n);
          const figure=document.createElement("figure"), caption=document.createElement("figcaption");
          const img=document.createElement("img"), retry=document.createElement("button");
          const resolved=safeUrl(new URL(url,endpoint));
          if(!resolved)throw new Error();
          figure.dataset.page=n;caption.textContent=pageLabel(n);
          img.loading="lazy";img.decoding="async";img.referrerPolicy=new URL(resolved).origin===endpoint.origin?"origin":"no-referrer";img.alt=`ページ ${n}`;
          retry.type="button";retry.textContent="再試行";retry.hidden=true;
          img.onerror=()=>{retry.hidden=false;img.hidden=true;};
          retry.onclick=()=>{
            const refresh=new URL(resolved);
            refresh.searchParams.set("_retry",Date.now());
            retry.hidden=true;img.hidden=false;img.src=refresh.href;
          };
          img.src=resolved;figure.append(caption,img,retry);figures.push(figure);
        }
        if(!from)readerPages.replaceChildren();
        readerPages.append(...figures);
        for(const figure of figures)readerObserver?.observe(figure);
        currentPage??=batch.pages[0]?.n;
        if(currentPage!=null)counter.textContent=pageLabel(currentPage);
        if(batch.next==null)break;
        from=batch.next;
      }while(!request.signal.aborted);
    }catch{
      if(!request.signal.aborted){
        const message=document.createElement("p");
        message.textContent="ページを読み込めませんでした。「出典で開く」から作品を確認できます。";
        if(readerPages.children.length)readerPages.append(message);
        else readerPages.replaceChildren(message);
      }
    }
  }
  if(readerReady){
    $("#readerClose").onclick=closeReader;
    reader.addEventListener("cancel",e=>{e.preventDefault();closeReader();});
    document.addEventListener("click",e=>{
      const button=e.target.closest("[data-read]");
      if(button)openReader(button.dataset.read,button.dataset.readUid,button);
    });
  }

  async function init(){try{const [manifest,status]=await Promise.all([fetch("catalog/manifest.json",{cache:"no-store"}).then(r=>{if(!r.ok)throw new Error(`manifest HTTP ${r.status}`);return r.json();}),fetch("source_status.json",{cache:"no-store"}).then(r=>r.ok?r.json():{}).catch(()=>({}))]);state.manifest=manifest;const n=Math.min(INITIAL_CHUNKS,Number(manifest.chunk_count||0));for(let i=0;i<n;i++)await loadChunk(i);state.since=initializeSince(manifest.updated_at,()=>localStorage,()=>sessionStorage);rebuild();$("#status").textContent=`更新 ${new Date(manifest.updated_at).toLocaleString("ja-JP")} ・ 全${Number(manifest.item_count).toLocaleString()}件`;const parts=["ehentai","hitomi","3hentai","asmhentai","nhentai"].map(k=>{const s=status[k]||{};return `${sourceLabel(k)}: ${s.status||"不明"}${s.accepted!=null?` ${s.accepted}件`:""}`;});$("#sourceStatus").textContent=parts.join(" / ");}catch(e){$("#status").textContent=`起動失敗: ${e.message}`;$("#grid").innerHTML='<div class="empty">公開データを読み込めませんでした。再読み込みしてください。</div>';}}

  function writeUrl(mode) {
    const query=encodeState({q:$("#q").value,src:state.source,cat:$("#category").value,fk:$("#facetKind").value,fv:$("#facetValue").value,sort:$("#sort").value,pages:$("#pages").value});
    const url=location.pathname+(query?"?"+query:"")+location.hash;
    if(url===location.pathname+location.search+location.hash)return;
    try{history[mode+"State"](null,"",url);}catch{}
  }
  function restoreUrl(renderNow=true) {
    clearTimeout(searchTimer);
    const v=decodeState(location.search);state.source=v.src;
    for(const [id,key] of [["q","q"],["sort","sort"],["pages","pages"],["facetKind","fk"]])$("#"+id).value=v[key];
    const cat=$("#category");if(v.cat&&![...cat.options].some(o=>o.value===v.cat)){const o=document.createElement("option");o.value=v.cat;o.textContent=v.cat;cat.appendChild(o);}cat.value=v.cat;
    refreshFacetValues(v.fv);$$('[data-source]').forEach(b=>b.classList.toggle("on",b.dataset.source===state.source));if(renderNow)apply();
  }
  const change=()=>{apply();writeUrl("push");};
  let searchTimer=0;$("#q").addEventListener("input",()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{apply();writeUrl("replace");},180);});$("#sort").addEventListener("change",()=>{state.seed=null;change();});$("#category").addEventListener("change",change);$("#facetKind").addEventListener("change",()=>{refreshFacetValues();change();});$("#facetValue").addEventListener("change",change);$("#pages").addEventListener("change",change);
  $("#loadMore").onclick=async()=>{if(state.shown<state.filtered.length){state.shown+=STEP;render();}else await loadNextChunk();};$("#loadAll").onclick=loadAll;$("#favBtn").onclick=()=>{state.favoritesOnly=!state.favoritesOnly;$("#favBtn").textContent=state.favoritesOnly?"★のみ":"★";apply();};$("#activeFacet").onclick=()=>{$("#facetKind").value="";refreshFacetValues();$("#facetValue").value="";change();};
  $$('[data-source]').forEach(b=>b.onclick=()=>{$$('[data-source]').forEach(x=>x.classList.remove("on"));b.classList.add("on");state.source=b.dataset.source;change();});document.addEventListener("click",e=>{const f=e.target.closest("[data-facet-kind][data-facet-value]");if(f){e.preventDefault();$("#facetKind").value=f.dataset.facetKind;refreshFacetValues(f.dataset.facetValue);$("#facetValue").value=f.dataset.facetValue;closeSheet();change();return;}const b=e.target.closest("[data-more]");if(b){e.preventDefault();menu(b.dataset.more);}});$("#sheetBg").onclick=closeSheet;
  $("#randomBtn").onclick=()=>{state.seed=(state.seed===null?Date.now():state.seed+1);apply();};
  $("#randomActive").onclick=()=>{state.seed=null;apply();};
  $("#newBtn").onclick=()=>{state.newOnly=!state.newOnly;apply();};
  window.addEventListener("popstate",()=>{
    if(readerReady&&location.hash==="#read"&&history.state?.reader){
      const {id,uid}=history.state.reader;
      restoreUrl();
      openReader(id,uid,null,false);
    }else{
      // Leaving the reader on the same list must keep the focused card in the DOM.
      const sameList=readerReturnUrl===location.pathname+location.search;
      readerReturnUrl=null;
      if(!sameList)restoreUrl();
      if(reader?.open&&readerReady)hideReader();
    }
  });
  if(location.hash==="#read"){
    try{history.replaceState(null,"",location.pathname+location.search);}catch{}
  }
  restoreUrl(false);
  init();
})();
