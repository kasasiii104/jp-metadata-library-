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
 constructor(tag='div'){this.tag=tag;this._value='';this.options=[];this.listeners={};this.dataset={};this.hidden=false;this.textContent='';this._html='';const c=new Set();this.classList={add:x=>c.add(x),remove:x=>c.delete(x),toggle:(x,on)=>on?c.add(x):c.delete(x),contains:x=>c.has(x)};}
 set value(v){this._value=this.tag==='select'&&!this.options.some(o=>o.value===v)?'':String(v);}
 get value(){return this._value;}
 set innerHTML(v){this._html=v;if(this.tag==='select'){this.options=[...v.matchAll(/<option value="([^"]*)">([^<]*)<\/option>/g)].map(m=>({value:m[1].replaceAll('&amp;','&').replaceAll('&lt;','<').replaceAll('&gt;','>'),textContent:m[2]}));this._value=this.options[0]?.value||'';}}
 get innerHTML(){return this._html;}
 appendChild(o){this.options.push(o);}
 addEventListener(k,f){this.listeners[k]=f;}
 async fire(k){return this.listeners[k]?.({});}
 async click(){return this.onclick?.();}
}
async function boot(query='',options={}){
 const data=options.fixture||fixture;
 const els={};for(const m of html.matchAll(/<(\w+)[^>]*\bid="([^"]+)"[^>]*>/g)){const e=new Element(m[1]);els[m[2]]=e;if(m[1]==='select')e.innerHTML=html.slice(m.index).split('</select>')[0];}
 for(const act of ['fav','hide'])els[`sheet [data-act=${act}]`]=new Element('button');
 const sources=[...html.matchAll(/<button\b[^>]*data-source="([^"]+)"/g)].map(m=>m[1]).map(source=>{const e=new Element('button');e.dataset.source=source;return e;});
 const events={},documentEvents={},loc=new URL('https://example.org/site/'+query),writes=[];
 const history={};for(const method of ['pushState','replaceState'])history[method]=(a,b,url)=>{if(options.historyFail)throw Error('blocked');writes.push([method,url]);loc.href=new URL(url,loc).href;};
 const document={querySelector:s=>els[s.slice(1)]||null,querySelectorAll:s=>s==='[data-source]'?sources:[],createElement:t=>new Element(t),addEventListener:(k,f)=>documentEvents[k]=f};
 const local=options.local||storage(),session=options.session||storage();
 const context={document,window:{addEventListener:(k,f)=>events[k]=f},location:loc,history,localStorage:local,sessionStorage:session,URL,URLSearchParams,console,setTimeout,clearTimeout,fetch:async url=>{const match=url.match(/chunk-(\d+)/);return {ok:!options.fetchFail,status:500,json:async()=>match?{items:data.chunks[Number(match[1])]}:url.includes('manifest')?data.manifest:{}};}};
 vm.runInNewContext(code,context);for(let i=0;i<10;i++)await new Promise(r=>setTimeout(r,0));
 return {els,sources,loc,writes,events,documentEvents,local,session,async change(id,value){els[id].value=value;await els[id].fire('change');},async pop(query){loc.search=query;events.popstate();},titles(){return [...els.grid.innerHTML.matchAll(/class="title"[^>]*>([^<]*)/g)].map(m=>m[1]);}};
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
 for(const pattern of [/name="description"/,/name="theme-color"/,/rel="manifest"/,/rel="apple-touch-icon"/,/app\.js\?v=20261117-3/])assert.match(html,pattern);
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
