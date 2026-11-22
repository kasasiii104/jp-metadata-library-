const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const logic=require('../docs/app.js');
const fixture=require('./fixtures');
const html=fs.readFileSync('docs/index.html','utf8'),code=fs.readFileSync('docs/app.js','utf8');
const storage=(initial={})=>{const data={...initial};return {data,getItem:k=>data[k]??null,setItem:(k,v)=>data[k]=v};};
test('日時: offset の3形式、欠落、不正な日付',()=>{
 assert.equal(logic.parseDate('2026-11-16T00:01:34+00:00'),Date.UTC(2026,10,16,0,1,34));
 assert.equal(logic.parseDate('2026-07-29 22:26:00-05'),Date.UTC(2026,6,30,3,26));
 assert.equal(logic.parseDate('2026-11-16T10:00:00+0900'),Date.UTC(2026,10,16,1));
 for(const v of [null,undefined,'','invalid','2026-02-30T00:00:00Z'])assert.ok(Number.isNaN(logic.parseDate(v)));
});
test('日時: 日付のみUTC、非ISO互換、ISO風の範囲外拒否',()=>{
 assert.equal(logic.parseDate('2026-07-29'),Date.UTC(2026,6,29));
 const legacy='Nov 16, 2026 10:00:00 GMT';
 assert.equal(logic.parseDate(legacy),Date.parse(legacy));
 for(const v of ['2026-02-30','2026-13-01','2026-00-01T00:00:00Z','2026-04-31T00:00:00Z','2026-11-00'])assert.ok(Number.isNaN(logic.parseDate(v)),v);
});
test('URL: 往復、日本語と記号、既定値省略、不正値',()=>{
 const state={q:'日本語 & +?#<>',src:'hitomi',cat:'分類 <&>',fk:'tags',fv:'タグ & +?#<>',sort:'pages',pages:'40'};
 assert.deepEqual(logic.decodeState(logic.encodeState(state)),state);
 assert.equal(logic.encodeState(logic.decodeState('')),'');
 assert.deepEqual(logic.decodeState('?src=unknown&sort=no&fk=no&fv=test&pages=NaN'),logic.decodeState(''));
 assert.equal(logic.decodeState('?pages=-1').pages,'0');
});
test('シード順序: 同seed、別seed、部分列、入力不変',()=>{
 const items=Array.from({length:30},(_,i)=>({uid:`id-${i}`}));
 const a=logic.seededOrder(items,12), b=logic.seededOrder(items,13);
 assert.deepEqual(a,logic.seededOrder(items,12));assert.notDeepEqual(a,b);
 const sub=items.filter((_,i)=>i%3===0);assert.deepEqual(logic.seededOrder(sub,12),a.filter(x=>sub.includes(x)));
 assert.equal(items[0].uid,'id-0');
});
test('新着: strict >、欠落除外、グループのどれかが新着',()=>{
 const since=Date.UTC(2026,10,16);
 assert.equal(logic.isNew({posted_at:'2026-11-16T00:00:00Z'},since),false);
 assert.equal(logic.isNew({posted_at:null},since),false);
 assert.equal(logic.isNew({_members:fixture.chunks[0]},since),true);
});
test('新着: first_seen を posted_at より優先し、基準と同時刻なら除外',()=>{
 const since=Date.UTC(2026,10,16);
 assert.equal(logic.isNew({first_seen:'2026-11-17',posted_at:'2026-11-15'},since),true);
 assert.equal(logic.isNew({first_seen:'2026-11-16',posted_at:'2026-11-17'},since),false);
});
test('新着: first_seen の欠落・不正値では posted_at を使い、両方欠落なら除外',()=>{
 const since=Date.UTC(2026,10,16);
 assert.equal(logic.isNew({posted_at:'2026-11-17'},since),true);
 assert.equal(logic.isNew({first_seen:'invalid',posted_at:'2026-11-17'},since),true);
 assert.equal(logic.isNew({},since),false);
});
test('新着: メンバーのいずれかの first_seen が基準より後ならグループが新着',()=>{
 const since=Date.UTC(2026,10,16);
 assert.equal(logic.isNew({_members:[{first_seen:'2026-11-15'},{first_seen:'2026-11-17'}]},since),true);
});
test('since: 初回、2回目、セッション固定、未来値修復、storage例外',()=>{
 const updated=fixture.manifest.updated_at, now=Date.UTC(2026,10,17),old=Date.UTC(2026,10,16);
 assert.deepEqual(logic.decideSince(updated,null,null),{since:now,lastSeen:now});
 assert.equal(logic.decideSince(updated,String(old),null).since,old);
 assert.equal(logic.decideSince(updated,String(now),String(old)).since,old);
 assert.equal(logic.decideSince('2026-11-15T00:00:00Z',String(now),null).lastSeen,Date.UTC(2026,10,15));
 const l=storage({meta_last_seen:String(old)}),s=storage();
 assert.equal(logic.initializeSince(updated,()=>l,()=>s),old);assert.equal(l.data.meta_last_seen,String(now));
 assert.equal(logic.initializeSince(updated,()=>l,()=>s),old);
 const fail=()=>{throw Error('storage blocked');};assert.equal(logic.initializeSince(updated,fail,fail),now);
 assert.equal(logic.decideSince('invalid',null,null,123).since,123);
});
test('日時: オフセットなしISO風日時はUTC',()=>{
 for(const v of ['2026-07-29 22:26:00','2026-07-29T22:26:00','2026-07-29T22:26:00Z'])assert.equal(logic.parseDate(v),Date.UTC(2026,6,29,22,26),v);
 assert.equal(logic.parseDate('2026-07-29T22:26:00.5'),Date.UTC(2026,6,29,22,26,0,500));
});
test('since: 保存値検証と自己修復',()=>{
 const current=Date.UTC(2026,10,17),old=current-86400000,updated='2026-11-17T00:00:00Z';
 for(const v of ['1e100',' ','not-a-date','-1','1.5',String(current+1),'0','',` ${old}`,`${old} `,'00000000000000001']){
  assert.deepEqual(logic.decideSince(updated,v,null),{since:current,lastSeen:current},v);
  assert.deepEqual(logic.decideSince(updated,String(old),v),{since:old,lastSeen:current},v);
  assert.deepEqual(logic.decideSince(updated,null,v),{since:current,lastSeen:current},v);
  const local=storage({meta_last_seen:v}),session=storage({meta_new_since:v});
  assert.equal(logic.initializeSince(updated,()=>local,()=>session),current,v);
  assert.equal(local.data.meta_last_seen,String(current),v);
  assert.equal(session.data.meta_new_since,String(current),v);
  assert.equal(logic.initializeSince(updated,()=>local,()=>session),current,v);
 }
 assert.deepEqual(logic.decideSince(updated,String(current),String(current)),{since:current,lastSeen:current});
 assert.deepEqual(logic.decideSince(updated,String(current),String(old)),{since:old,lastSeen:current});
 assert.deepEqual(logic.decideSince(updated,'not-a-date',String(old)),{since:old,lastSeen:current});
 assert.deepEqual(logic.decideSince('invalid',String(current+1),null,current),{since:current,lastSeen:current});
});
class Element {
 constructor(tag='div'){this.tag=tag;this._value='';this.options=[];this.children=[];this.listeners={};this.dataset={};this.hidden=false;this.parent=null;this._text='';this._html='';const c=new Set();this.classList={add:x=>c.add(x),remove:x=>c.delete(x),toggle:(x,on)=>on?c.add(x):c.delete(x),contains:x=>c.has(x)};}
 set value(v){this._value=this.tag==='select'&&!this.options.some(o=>o.value===v)?'':String(v);}
 get value(){return this._value;}
 set innerHTML(v){this._html=v;if(this.tag==='select'){this.options=[...v.matchAll(/<option value="([^"]*)">([^<]*)<\/option>/g)].map(m=>({value:m[1].replaceAll('&amp;','&').replaceAll('&lt;','<').replaceAll('&gt;','>'),textContent:m[2]}));this._value=this.options[0]?.value||'';}}
 get innerHTML(){return this._html;}
 get textContent(){return this._text+this.children.map(child=>child.textContent).join('');}
 set textContent(text){this.replaceChildren();this._text=String(text);}
 get firstElementChild(){return this.children[0];}
 get isConnected(){return this.connected||!!this.parent?.isConnected;}
 append(...children){for(const child of children)child.parent=this;this.children.push(...children);}
 replaceChildren(...children){
  for(const child of this.children)child.parent=null;
  this.children=[];this._text='';this.append(...children);
  if(this.tag==='select')this.options=children;
 }
 showModal(){this.open=true;}
 close(){this.open=false;}
 appendChild(o){this.options.push(o);}
 addEventListener(k,f){this.listeners[k]=f;}
 async fire(k){return this.listeners[k]?.({});}
 async click(){return this.onclick?.();}
}
function fakeTimers(){
 let nextId=0;
 const pending=new Map();
 return {
  setTimeout(callback,delay){pending.set(++nextId,{callback,delay});return nextId;},
  clearTimeout(id){pending.delete(id);},
  delays(){return [...pending.values()].map(timer=>timer.delay);},
  async runNext(){
   const [id,timer]=pending.entries().next().value;
   pending.delete(id);
   timer.callback();
   await settleReader();
  },
 };
}
const settleReader=()=>new Promise(resolve=>setImmediate(resolve));
async function boot(query='',options={}){
 const data=options.fixture||fixture;
 const els={};for(const m of html.matchAll(/<(\w+)[^>]*\bid="([^"]+)"[^>]*>/g)){const e=new Element(m[1]);els[m[2]]=e;if(m[1]==='select')e.innerHTML=html.slice(m.index).split('</select>')[0];}
 for(const act of ['fav','hide'])els[`sheet [data-act=${act}]`]=new Element('button');
 const sources=[...html.matchAll(/<button\b[^>]*data-source="([^"]+)"/g)].map(m=>m[1]).map(source=>{const e=new Element('button');e.dataset.source=source;return e;});
 const events={},documentEvents={},readerFetches=[],loc=new URL('https://example.org/site/'+query),writes=[];
 const history={};for(const method of ['pushState','replaceState'])history[method]=(a,b,url)=>{if(options.historyFail)throw Error('blocked');writes.push([method,url]);history.state=a;loc.href=new URL(url,loc).href;};
 const document={body:new Element(),querySelector:s=>els[s.slice(1)]||null,querySelectorAll:s=>s==='[data-source]'?sources:options.queryControls?.(s)||[],createElement:t=>new Element(t),addEventListener(k,f){const previous=documentEvents[k];documentEvents[k]=e=>{previous?.(e);f(e);};}};
 if(els.readerPages)els.readerPages.connected=true;
 const local=options.local||storage(),session=options.session||storage();
 const context={document,window:{addEventListener:(k,f)=>events[k]=f},location:loc,history,localStorage:local,sessionStorage:session,URL,URLSearchParams,AbortController,Option:function(textContent,value){Object.assign(this,{textContent,value});},console,
  setTimeout:options.timers?.setTimeout||setTimeout,clearTimeout:options.timers?.clearTimeout||clearTimeout,fetch:async (url,init)=>{
  if(url instanceof URL){readerFetches.push(new URL(url));return options.fetchPages?.(url,init)||{ok:false};}
  const match=url.match(/chunk-(\d+)/);return {ok:!options.fetchFail,status:500,json:async()=>match?{items:data.chunks[Number(match[1])]}:url.includes('manifest')?data.manifest:{}};}};
 const app=options.reader?code.replace('const EMBED_WORKER_BASE = "https://jp-embed.kasasiii104.workers.dev";',options.reader==='disabled'?'const EMBED_WORKER_BASE = "";':'const EMBED_WORKER_BASE = "https://reader.example/";'):code;
 vm.runInNewContext(app,context);for(let i=0;i<10;i++)await new Promise(r=>setTimeout(r,0));
 return {els,sources,loc,writes,events,documentEvents,local,session,history,readerFetches,async change(id,value){els[id].value=value;await els[id].fire('change');},async pop(query){loc.search=query;events.popstate();},titles(){return [...els.grid.innerHTML.matchAll(/class="title"[^>]*>([^<]*)/g)].map(m=>m[1]);}};
}
test('結合: 初期新着順、検索replace、離散push、同一URL省略、popstate',async()=>{
 const b=await boot();assert.match(b.els.status.textContent,/^更新 /);assert.deepEqual(b.titles(),['代表','C','D']);assert.equal(b.els.sort.value,'new');assert.equal(b.writes.length,0);
 b.els.q.value='C';b.els.q.fire('input');await new Promise(r=>setTimeout(r,210));assert.deepEqual(b.titles(),['C']);assert.equal(b.writes.at(-1)[0],'replaceState');assert.equal(b.loc.search,'?q=C');
 await b.pop('');assert.deepEqual(b.titles(),['代表','C','D']);const n=b.writes.length;
 await b.change('sort','pages');assert.deepEqual(b.titles(),['C','代表','D']);assert.equal(b.writes.at(-1)[0],'pushState');
 await b.change('sort','pages');assert.equal(b.writes.length,n+1);
 await b.pop('?src=hitomi');assert.deepEqual(b.titles(),['代表']);assert.equal(b.writes.length,n+1);
});
test('結合: ソース、カテゴリ、facet、ページ数、カードfacet、解除',async()=>{
 const b=await boot();await b.sources[3].click();assert.deepEqual(b.titles(),['C']);assert.match(b.loc.search,/src=3hentai/);
 await b.sources[0].click();await b.change('category','通常');assert.equal(b.titles().length,3);
 await b.change('facetKind','tags');await b.change('facetValue','日本語 & <タグ>');assert.equal(b.titles().length,3);assert.equal(new URLSearchParams(b.loc.search).get('fv'),'日本語 & <タグ>');
 await b.change('pages','80');assert.deepEqual(b.titles(),['C']);await b.els.activeFacet.click();assert.equal(b.els.facetKind.value,'');
 b.documentEvents.click({target:{closest:s=>s.includes('facet-kind')?{dataset:{facetKind:'tags',facetValue:'日本語 & <タグ>'}}:null},preventDefault(){}});assert.equal(b.els.facetValue.value,'日本語 & <タグ>');
});
test('結合: 未読込カテゴリ/facetの復元をチャンク追加後も保持',async()=>{
 const b=await boot('?cat='+encodeURIComponent('後続')+'&fk=artists&fv='+encodeURIComponent('作者 & <新>'));
 assert.equal(b.els.category.value,'後続');assert.equal(b.els.facetValue.value,'作者 & <新>');assert.deepEqual(b.titles(),[]);
 await b.els.loadMore.click();assert.deepEqual(b.titles(),['E']);assert.equal(b.els.category.value,'後続');assert.equal(b.els.facetValue.value,'作者 & <新>');assert.equal(b.writes.length,0);
});
test('結合: ランダム入・再シャッフル・解除と並び替え解除、URL非掲載',async()=>{
 const b=await boot();await b.els.loadAll.click();const original=b.titles();await b.els.randomBtn.click();const first=b.titles();assert.equal(b.els.randomActive.hidden,false);
 let changed=false;for(let i=0;i<8;i++){await b.els.randomBtn.click();if(JSON.stringify(first)!==JSON.stringify(b.titles()))changed=true;}assert.ok(changed);
 await b.els.randomActive.click();assert.deepEqual(b.titles(),original);assert.equal(b.els.randomActive.hidden,true);
 await b.els.randomBtn.click();await b.change('sort','rating');assert.equal(b.els.randomActive.hidden,true);assert.equal(b.writes.length,1);assert.equal(b.loc.search,'?sort=rating');
});
test('結合: 新着初回0、既訪問の重複グループ新着、AND条件と件数',async()=>{
 const first=await boot();await first.els.newBtn.click();assert.deepEqual(first.titles(),[]);assert.match(first.els.count.textContent,/新着 0件（基準/);
 const b=await boot('',{local:storage({meta_last_seen:String(Date.UTC(2026,10,16))})});await b.els.newBtn.click();assert.deepEqual(b.titles(),['代表']);assert.match(b.els.count.textContent,/新着 1件（基準/);
 await b.sources[3].click();assert.deepEqual(b.titles(),[]);await b.sources[0].click();await b.els.loadAll.click();assert.deepEqual(b.titles(),['E','代表']);assert.match(b.els.newBtn.textContent,/\(2\)/);assert.match(b.els.count.textContent,/6\/6件読込/);
 await b.change('category','後続');assert.deepEqual(b.titles(),['E']);assert.match(b.els.count.textContent,/新着 1件/);
});
test('結合: 非表示の新着はボタン件数から除外し再構築後も維持',async()=>{
 const b=await boot('',{local:storage({meta_last_seen:String(Date.UTC(2026,10,16))})});
 assert.match(b.els.newBtn.textContent,/\(1\)/);
 const id=b.els.grid.innerHTML.match(/data-more="([^"]+)"/)[1];
 b.documentEvents.click({target:{closest:s=>s==='[data-more]'?{dataset:{more:id}}:null},preventDefault(){}});
 await b.els['sheet [data-act=hide]'].click();
 assert.match(b.els.newBtn.textContent,/\(0\)/);
 await b.els.newBtn.click();assert.deepEqual(b.titles(),[]);
 await b.els.loadAll.click();assert.match(b.els.newBtn.textContent,/\(1\)/);assert.deepEqual(b.titles(),['E']);
});
test('結合: history/storage例外でも動作、失敗時status',async()=>{
 const broken={getItem(){throw Error('blocked');},setItem(){throw Error('blocked');},removeItem(){throw Error('blocked');}};
 const b=await boot('',{historyFail:true,local:broken,session:broken});await b.change('sort','pages');assert.deepEqual(b.titles(),['C','代表','D']);assert.match(b.els.status.textContent,/^更新 /);
 const failure=await boot('',{fetchFail:true});assert.match(failure.els.status.textContent,/^起動失敗/);
});
test('HTML IDs、FALLBACK、PWA、PNGシグネチャと寸法',()=>{
 const ids=new Set([...html.matchAll(/\bid="([^"]+)"/g)].map(m=>m[1]));for(const m of code.matchAll(/\$\("#([\w-]+)(?:[ "\)])/g))assert.ok(ids.has(m[1]),m[1]);
 for(const region of ['CARDS','STATUS'])for(const end of ['START','END'])assert.equal(html.split(`<!-- FALLBACK_${region}_${end} -->`).length,2);
 for(const pattern of [/name="description"/,/name="theme-color"/,/rel="manifest"/,/rel="apple-touch-icon"/,/app\.js\?v=20261122-1/])assert.match(html,pattern);
 const manifest=JSON.parse(fs.readFileSync('docs/manifest.webmanifest'));assert.equal(manifest.start_url,'./');assert.equal(manifest.scope,'./');assert.equal(manifest.display,'standalone');
 for(const icon of [...manifest.icons,{src:'icons/apple-touch-icon.png',sizes:'180x180'}]){const bytes=fs.readFileSync(path.join('docs',icon.src));if(icon.src.endsWith('.png')){assert.equal(bytes.subarray(0,8).toString('hex'),'89504e470d0a1a0a');assert.equal(bytes.subarray(12,16).toString(),'IHDR');assert.equal(`${bytes.readUInt32BE(16)}x${bytes.readUInt32BE(20)}`,icon.sizes);}}
});

test("FALLBACK生成領域の構造（マーカー単一・順序・入れ子なし）",()=>{
 const ranges=['CARDS','STATUS'].map(region=>{
  const markers=['START','END'].map(end=>`<!-- FALLBACK_${region}_${end} -->`);
  for(const marker of markers)assert.equal(html.split(marker).length,2,marker);
  const start=html.indexOf(markers[0]),end=html.indexOf(markers[1]);
  assert.ok(start<end,region);
  return {start,end:end+markers[1].length};
 }).sort((a,b)=>a.start-b.start);
 assert.ok(ranges[0].end<=ranges[1].start);
});

const nhentaiFixture={
 manifest:{...fixture.manifest,chunk_count:3,item_count:4},
 chunks:[[
  {uid:'nh-1',source:'nhentai',source_id:'1',title:'Plain',tags:['big breasts','untranslated token'],parodies:['parody:kantai collection'],characters:['character:ponytail'],posted_at:'2026-11-16'},
  {uid:'nh-2',source:'nhentai',source_id:'2',title:'Namespaced',tags:['female:big breasts'],parodies:[],characters:[],posted_at:'2026-11-15'}
 ],[
  {uid:'other',source:'hitomi',title:'Other',tags:['glasses'],works:['work:azur lane'],posted_at:'2026-11-14'}
 ],[
  {uid:'nh-3',source:'nhentai',source_id:'3',duplicate_group:'nh-1',title:'Later',tags:['netorare'],parodies:[],characters:[],posted_at:'2026-11-13'}
 ]]
};
test('URL: nhentai復元・往復、未知ソースはall',()=>{
 const state=logic.decodeState('?src=nhentai');
 assert.equal(state.src,'nhentai');
 assert.deepEqual(logic.decodeState(logic.encodeState(state)),state);
 assert.equal(logic.decodeState('?src=unknown').src,'all');
});
test('結合: nhentaiチップで絞り込み・URL反映・カードラベル・状態表示',async()=>{
 const b=await boot('',{fixture:nhentaiFixture});
 assert.deepEqual(b.titles(),['Plain','Namespaced','Other']);
 const chip=b.sources.find(e=>e.dataset.source==='nhentai');
 await chip.click();
 assert.deepEqual(b.titles(),['Plain','Namespaced']);
 assert.equal(new URLSearchParams(b.loc.search).get('src'),'nhentai');
 assert.ok(chip.classList.contains('on'));
 assert.equal([...b.els.grid.innerHTML.matchAll(/<span class="source">([^<]+)<\/span>/g)].map(m=>m[1]).join(','),'nhentai,nhentai');
 assert.match(b.els.sourceStatus.textContent,/nhentai: 不明/);
});
test('結合: src=nhentai起動で絞り込みとチップonを復元',async()=>{
 const b=await boot('?src=nhentai',{fixture:nhentaiFixture});
 assert.deepEqual(b.titles(),['Plain','Namespaced']);
 for(const chip of b.sources)assert.equal(chip.classList.contains('on'),chip.dataset.source==='nhentai');
 assert.equal(b.writes.length,0);
});
test('結合: 日本語タグ・名前空間・parody・works・characterの訳語検索',async()=>{
 const b=await boot('',{fixture:nhentaiFixture});
 for(const [query,titles] of [['巨乳',['Plain','Namespaced']],['女性：巨乳',['Namespaced']],['艦隊これくしょん',['Plain']],['ポニーテール',['Plain']],['アズールレーン',['Other']]]){
  await b.pop('?q='+encodeURIComponent(query));assert.deepEqual(b.titles(),titles,query);
 }
});
test('結合: 英語生値・大小文字・未訳語の検索を維持',async()=>{
 const b=await boot('',{fixture:nhentaiFixture});
 for(const [query,titles] of [['big breasts',['Plain','Namespaced']],['BIG',['Plain','Namespaced']],['female:big breasts',['Namespaced']],['parody:kantai collection',['Plain']],['character:ponytail',['Plain']],['untranslated token',['Plain']]]){
  await b.pop('?q='+encodeURIComponent(query));assert.deepEqual(b.titles(),titles,query);
 }
});
test('結合: 検索キャッシュはチャンク追加のグループ再構築で更新',async()=>{
 const b=await boot('?q='+encodeURIComponent('寝取られ'),{fixture:nhentaiFixture});
 assert.deepEqual(b.titles(),[]);
 await b.els.loadAll.click();assert.deepEqual(b.titles(),['Plain']);
 await b.pop('?q='+encodeURIComponent('巨乳'));assert.deepEqual(b.titles(),['Plain','Namespaced']);
});

const readerRow=(source,id,extra={})=>({
 uid:`${source}:${id}`,source,source_id:String(id),title:`${source}-${id}`,
 tags:['tag'],pages:2,...extra,
});
const readerData={reader_media_id:'14',reader_exts:'wp'};
const eMember=readerRow('ehentai',42,{source_url:'https://e-hentai.org/g/42/aabbcc/'});
const hitomiMember=readerRow('hitomi',43);
const nhMember=readerRow('nhentai',44,readerData);
const readerGroup=(...members)=>members.map(member=>({
 ...member,duplicate_group:'group',duplicate_count:members.length,
 duplicate_uids:members.filter(sibling=>sibling!==member).map(sibling=>sibling.uid),
}));
function bootReader(members,query='',options={}){
 return boot(query,{reader:true,timers:fakeTimers(),...options,fixture:{
  manifest:{...fixture.manifest,chunk_count:1,item_count:members.length},
  chunks:[members.map(member=>({...member}))],
 }});
}
function clickReaderControl(browser,selector,dataset){
 browser.documentEvents.click({target:{closest:s=>s===selector?{
  dataset,isConnected:false,closest:()=>null,
 }:null},preventDefault(){}});
}

test('FIX11 nhentai: カードとシートの「読む」は有効な材料がある行に限る',async()=>{
 const members=[
  nhMember,readerRow('nhentai',45),
  readerRow('nhentai',46,{...readerData,reader_media_id:'014'}),
  readerRow('nhentai',47,{...readerData,reader_exts:'wjx',pages:3}),
  readerRow('nhentai',48,{...readerData,pages:'2'}),
  readerRow('nhentai',49,{...readerData,pages:3}),
  readerRow('nhentai',50,{...readerData,reader_exts:'w'.repeat(2001),pages:2001}),
 ];
 const b=await bootReader(members);
 assert.deepEqual([...b.els.grid.innerHTML.matchAll(/class="facet reader-open" type="button" data-read="([^"]+)"/g)].map(m=>m[1]),[nhMember.uid]);
 assert.deepEqual(new Set([...b.els.grid.innerHTML.matchAll(/data-read="([^"]+)"/g)].map(m=>m[1])),new Set([nhMember.uid]));
 for(const member of members){
  clickReaderControl(b,'[data-more]',{more:member.uid});
  assert.match(b.els.sheet.innerHTML,/で見る/);
  assert.equal(b.els.sheet.innerHTML.includes('で読む'),member===nhMember);
 }
});

test('FIX12b ehentai は単独でも読め、hitomi との切替と履歴復元ができる',async()=>{
 const alone=await bootReader([eMember]);
 assert.match(alone.els.grid.innerHTML,/data-read="ehentai:42"/);
 clickReaderControl(alone,'[data-more]',{more:eMember.uid});
 assert.match(alone.els.sheet.innerHTML,/で見る/);
 assert.match(alone.els.sheet.innerHTML,/で読む/);
 assert.match(alone.els.sheet.innerHTML,/data-read-uid="ehentai:42"/);
 clickReaderControl(alone,'[data-read]',{read:eMember.uid,readUid:eMember.uid});
 assert.equal(alone.readerFetches[0].pathname,'/pages');
 assert.equal(alone.readerFetches[0].searchParams.get('uid'),eMember.uid);

 const b=await bootReader(readerGroup(eMember,hitomiMember),'?src=ehentai');
 assert.match(b.els.grid.innerHTML,/data-read="group"/);
 clickReaderControl(b,'[data-more]',{more:'group'});
 assert.equal([...b.els.sheet.innerHTML.matchAll(/で見る/g)].length,2);
 assert.deepEqual([...b.els.sheet.innerHTML.matchAll(/data-read-uid="([^"]+)"/g)].map(m=>m[1]),[eMember.uid,hitomiMember.uid]);
 clickReaderControl(b,'[data-read]',{read:'group'});
 assert.equal(b.readerFetches[0].searchParams.get('uid'),eMember.uid);
 assert.equal(b.els.readerMember.value,eMember.uid);
 assert.deepEqual(b.els.readerMember.options.map(o=>o.value),[eMember.uid,hitomiMember.uid]);
 assert.equal(b.els.readerMember.hidden,false);
 b.els.readerMember.value=hitomiMember.uid;
 b.els.readerMember.onchange();
 assert.equal(b.readerFetches[1].searchParams.get('uid'),hitomiMember.uid);

 b.loc.hash='';
 b.history.state=null;
 b.events.popstate();
 assert.equal(b.els.reader.open,false);

 b.loc.hash='#read';
 b.history.state={reader:{id:'group',uid:eMember.uid}};
 b.events.popstate();
 assert.equal(b.els.reader.open,true);
 assert.equal(b.readerFetches[2].searchParams.get('uid'),eMember.uid);
});

test('FIX12b 既定出典は読める絞り込み先を優先し、切替には読める出典だけを載せる',async()=>{
 const members=readerGroup(eMember,hitomiMember,nhMember);
 for(const member of [nhMember,eMember]){
  const preferred=await bootReader(members,'?src='+member.source);
  clickReaderControl(preferred,'[data-read]',{read:'group'});
  assert.equal(preferred.readerFetches[0].searchParams.get('uid'),member.uid);
  assert.equal(preferred.els.readerMember.value,member.uid);
  assert.equal(preferred.els.readerMember.hidden,false);
  assert.deepEqual(preferred.els.readerMember.options.map(o=>o.value),[eMember.uid,hitomiMember.uid,nhMember.uid]);
 }

 const fallback=await bootReader(readerGroup(eMember,hitomiMember,readerRow('nhentai',45)),'?src=nhentai');
 clickReaderControl(fallback,'[data-read]',{read:'group'});
 assert.equal(fallback.readerFetches[0].searchParams.get('uid'),eMember.uid);
 assert.equal(fallback.els.readerMember.value,eMember.uid);
 assert.deepEqual(fallback.els.readerMember.options.map(o=>o.value),[eMember.uid,hitomiMember.uid]);
});

test('FIX11 除外タグ付きグループには nhentai の材料が揃っていても「読む」が出ない',async()=>{
 const b=await bootReader(readerGroup(nhMember,{...eMember,tags:['female:loli']}));
 assert.doesNotMatch(b.els.grid.innerHTML,/data-read=/);
 clickReaderControl(b,'[data-more]',{more:'group'});
 assert.doesNotMatch(b.els.sheet.innerHTML,/で読む/);
 assert.equal([...b.els.sheet.innerHTML.matchAll(/で見る/g)].length,2);
});

test('FIX12c サムネイルとタイトルから既定出典を開き、閉じると起点に戻る',async()=>{
 const members=readerGroup(
  {...eMember,title:'A & "B"'},
  {...hitomiMember,source_url:'https://hitomi.la/galleries/43.html'},
 );
 for(const className of ['thumb','title']){
  let focused;
  const controls=['thumb','title','facet reader-open'].map(name=>({
   className:name,dataset:{read:'group'},getClientRects:()=>[{}],focus:()=>{focused=name;},
  }));
  const b=await bootReader(members,'',{queryControls:selector=>selector==='[data-read], [data-more]'?controls:[]});
  const markup=b.els.grid.innerHTML;
  const thumb=markup.match(/<button class="thumb"[^>]*>/)?.[0];
  const title=markup.match(/<button class="title"[^>]*>/)?.[0];
  for(const control of [thumb,title]){
   assert.ok(control);
   assert.match(control,/type="button"/);
   assert.match(control,/data-read="group"/);
   assert.doesNotMatch(control,/href=/);
  }
  assert.match(thumb,/aria-label="A &amp; &quot;B&quot;を読む"/);
  assert.match(markup,/<button class="facet reader-open" type="button" data-read="group">読む<\/button>/);
  clickReaderControl(b,'[data-more]',{more:'group'});
  assert.match(b.els.sheet.innerHTML,/<a href="https:\/\/e-hentai\.org\/g\/42\/aabbcc\/" target="_blank" rel="noopener noreferrer">E-Hentaiで見る<\/a>/);
  assert.match(b.els.sheet.innerHTML,/<a href="https:\/\/hitomi\.la\/galleries\/43\.html" target="_blank" rel="noopener noreferrer">Hitomiで見る<\/a>/);
  const control=className==='thumb'?thumb:title;
  const trigger={className,dataset:{read:control.match(/data-read="([^"]+)"/)[1]},isConnected:true,closest:()=>null};
  const grid=b.els.grid;
  Object.defineProperty(grid,'innerHTML',{
   get:()=>grid._html,
   set(value){grid._html=value;trigger.isConnected=false;focused=undefined;},
  });
  b.documentEvents.click({target:{closest:selector=>selector==='[data-read]'?trigger:null}});
  assert.equal(b.els.reader.open,true);
  assert.equal(b.readerFetches[0].pathname,'/pages');
  assert.equal(b.readerFetches[0].searchParams.get('uid'),eMember.uid);
  b.history.back=()=>{
   b.loc.hash='';
   b.history.state=null;
   b.events.popstate();
  };
  await b.els.readerClose.click();
  assert.equal(b.els.reader.open,false);
  assert.equal(focused,className);
 }
});

async function openReaderWithTrackedCards(){
 let controls=[],focused;
 const b=await bootReader([eMember,hitomiMember],'',{
  queryControls:selector=>selector==='[data-read], [data-more]'?controls:[],
 });
 const grid=b.els.grid;
 function updateControls(markup){
  for(const control of controls)control.isConnected=false;
  focused=undefined;
  controls=[...markup.matchAll(/<button class="([^"]+)" type="button" data-read="([^"]+)"/g)].map(([,className,read])=>{
   const control={className,dataset:{read},isConnected:true,closest:()=>null,
    getClientRects:()=>control.isConnected?[{}]:[],focus:()=>{focused=control;}};
   return control;
  });
 }
 Object.defineProperty(grid,'innerHTML',{
  get:()=>grid._html,
  set(value){grid._html=value;updateControls(value);},
 });
 updateControls(grid.innerHTML);
 const titleControl=()=>controls.find(c=>c.className==='title'&&c.dataset.read===eMember.uid);
 const trigger=titleControl();
 assert.ok(trigger);
 b.documentEvents.click({target:{closest:selector=>selector==='[data-read]'?trigger:null}});
 assert.equal(b.els.reader.open,true);
 return {b,trigger,titleControl,focusedControl:()=>focused};
}

test('FIX12c リーダー表示中にカードが再描画されても同じ作品のタイトルへ戻る',async()=>{
 const {b,trigger,titleControl,focusedControl}=await openReaderWithTrackedCards();
 await b.els.loadAll.click();
 assert.equal(trigger.isConnected,false);
 const replacement=titleControl();
 assert.ok(replacement?.isConnected);
 b.history.back=()=>{
  b.loc.hash='';
  b.history.state=null;
  b.events.popstate();
 };
 await b.els.readerClose.click();
 assert.equal(b.els.reader.open,false);
 assert.equal(focusedControl(),replacement);
});

test('FIX12c リーダーから別クエリの履歴に移動すると検索と一覧を復元して閉じる',async()=>{
 const {b,trigger,titleControl,focusedControl}=await openReaderWithTrackedCards();
 assert.deepEqual(b.titles().sort(),[eMember.title,hitomiMember.title].sort());
 b.loc.hash='';
 b.history.state=null;
 await b.pop('?q='+eMember.title);
 assert.equal(b.els.q.value,eMember.title);
 assert.deepEqual(b.titles(),[eMember.title]);
 assert.equal(b.els.reader.open,false);
 assert.equal(trigger.isConnected,false);
 const replacement=titleControl();
 assert.ok(replacement?.isConnected);
 assert.equal(focusedControl(),replacement);
});

test('FIX12c Worker 無効または読める出典がないカードは従来の出典リンクを保つ',async()=>{
 const unreadable={...nhMember,reader_media_id:undefined,source_url:'https://nhentai.net/g/44/'};
 for(const [member,options] of [[eMember,{reader:'disabled'}],[unreadable,{}]]){
  const b=await bootReader([member],'',options);
  const markup=b.els.grid.innerHTML;
  for(const className of ['thumb','title']){
   const link=markup.match(new RegExp(`<a class="${className}"[^>]*>`))?.[0];
   assert.equal(link,`<a class="${className}" href="${member.source_url}" target="_blank" rel="noopener noreferrer">`);
  }
  assert.doesNotMatch(markup,/data-read=/);
 }
});

const readerError=/ページを読み込めませんでした/;
const imagePage=(n,url=`/image-${n}.webp`)=>({n,url});
const pagesResponse=batch=>({ok:true,json:async()=>batch});

async function openRetryReader(...responses){
 const timers=fakeTimers();
 const b=await bootReader([eMember],'',{timers,fetchPages:()=>{
  const response=responses.shift();
  return typeof response==='function'?response():response;
 }});
 clickReaderControl(b,'[data-read]',{read:eMember.uid});
 await settleReader();
 return {...b,timers,pages:b.els.readerPages};
}

test('FIX12f: 初回取得は 4 秒、10 秒で再試行し、成功まで読み込み表示を維持する',async()=>{
 const b=await openRetryReader(()=>{throw new Error('network');},{ok:false},
  pagesResponse({pages:[imagePage(1)],count:1}));
 assert.equal(b.pages.textContent,'読み込み中…');
 assert.deepEqual(b.timers.delays(),[4000]);
 await b.timers.runNext();
 assert.equal(b.pages.textContent,'読み込み中…');
 assert.deepEqual(b.timers.delays(),[10000]);
 await b.timers.runNext();
 assert.equal(b.pages.children[0].tag,'figure');
 assert.equal(b.pages.children[0].firstElementChild.textContent,'1 / 1');
 assert.equal(b.readerFetches.length,3);
 assert.equal(new Set(b.readerFetches.map(url=>url.href)).size,1);
 assert.deepEqual(b.timers.delays(),[]);
});

test('FIX12f: 初回取得の全試行失敗で従来のエラーを表示する',async()=>{
 const b=await openRetryReader({ok:false},{ok:false},{ok:false});
 await b.timers.runNext();
 await b.timers.runNext();
 assert.equal(b.readerFetches.length,3);
 assert.deepEqual(b.pages.children.map(child=>child.tag),['p']);
 assert.match(b.pages.textContent,readerError);
 assert.deepEqual(b.timers.delays(),[]);
});

test('FIX12f: 継続取得の再試行中は既存ページを保ち、全試行失敗で末尾に注記する',async()=>{
 const b=await openRetryReader(pagesResponse({pages:[imagePage(1)],next:2,listing_last:3,count:4}),
  pagesResponse({pages:[imagePage(2),imagePage(3,'javascript:bad')],next:3,listing_last:4,count:9}),
  pagesResponse({pages:[imagePage(2)],next:3,listing_last:2,count:9}),{ok:false});
 const [first]=b.pages.children;
 assert.equal(first.firstElementChild.textContent,'1 / 4');
 assert.deepEqual(b.timers.delays(),[4000]);
 await b.timers.runNext();
 assert.equal(b.pages.children.length,1);
 assert.equal(b.pages.children[0],first);
 assert.equal(first.firstElementChild.textContent,'1 / 4');
 assert.deepEqual(b.timers.delays(),[10000]);
 await b.timers.runNext();
 assert.equal(b.pages.children[0],first);
 assert.equal(b.pages.children[1].tag,'p');
 assert.match(b.pages.children[1].textContent,readerError);
 for(const url of b.readerFetches.slice(1)){
  assert.equal(url.href,b.readerFetches[1].href);
  assert.deepEqual(['from','last','pl'].map(key=>url.searchParams.get(key)),['2','1','3']);
 }
});

test('FIX12f: 不正形式と再試行の連番違反を拒否し、正しいバッチで回復する',async()=>{
 const b=await openRetryReader(pagesResponse({pages:{}}),
  pagesResponse({pages:[imagePage(1),imagePage(3)]}),
  pagesResponse({pages:[imagePage(1),imagePage(2)]}));
 await b.timers.runNext();
 assert.equal(b.pages.textContent,'読み込み中…');
 assert.deepEqual(b.timers.delays(),[10000]);
 await b.timers.runNext();
 assert.deepEqual(b.pages.children.map(figure=>figure.dataset.page),[1,2]);
});

test('FIX12f: 画像 URL が不正だった試行の連番・重複・件数を次の試行に持ち越さない',async()=>{
 const b=await openRetryReader(
  pagesResponse({pages:[imagePage(1),imagePage(2,'javascript:bad')],next:2,listing_last:3,count:9}),
  pagesResponse({pages:[imagePage(1),imagePage(2)],count:2}));
 await b.timers.runNext();
 assert.deepEqual(b.pages.children.map(figure=>figure.firstElementChild.textContent),['1 / 2','2 / 2']);
 assert.equal(b.els.readerCount.textContent,'1 / 2');
});

test('FIX12f: 待機中に閉じるとタイマーを解除し、以後リクエストも表示更新もしない',async()=>{
 const b=await openRetryReader({ok:false},pagesResponse({pages:[imagePage(1)]}));
 assert.deepEqual(b.timers.delays(),[4000]);
 b.history.back=()=>{};
 await b.els.readerClose.click();
 await settleReader();
 assert.deepEqual(b.timers.delays(),[]);
 assert.equal(b.readerFetches.length,1);
 assert.equal(b.pages.textContent,'');
 assert.equal(b.els.readerCount.textContent,'');
});

test('FIX12f: 画像ごとに自動再取得し、失敗後の手動操作と破棄時の動作を維持する',async()=>{
 const b=await openRetryReader(pagesResponse({pages:[imagePage(1),imagePage(2)]}));
 const [[,img,retry],[,removedImg,removedRetry]]=b.pages.children.map(figure=>figure.children);
 img.onerror();
 removedImg.onerror();
 assert.deepEqual(b.timers.delays(),[3000,3000]);
 assert.equal(img.src,'https://reader.example/image-1.webp');
 assert.equal(img.hidden,false);
 assert.equal(retry.hidden,true);
 await b.timers.runNext();
 assert.ok(new URL(img.src).searchParams.has('_retry'));
 assert.equal(retry.hidden,true);
 assert.equal(img.hidden,false);
 img.onerror();
 assert.equal(retry.hidden,false);
 assert.equal(img.hidden,true);
 assert.deepEqual(b.timers.delays(),[3000]);
 await retry.click();
 assert.ok(new URL(img.src).searchParams.has('_retry'));
 assert.equal(retry.hidden,true);
 assert.equal(img.hidden,false);
 b.history.back=()=>{};
 await b.els.readerClose.click();
 await b.timers.runNext();
 assert.equal(removedImg.src,'https://reader.example/image-2.webp');
 assert.equal(removedRetry.hidden,true);
});
