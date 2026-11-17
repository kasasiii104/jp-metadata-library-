const row=(uid,source,posted_at,extra={})=>({uid,source,posted_at,title:uid,category:'通常',pages:24,rating:2,source_url:'https://example.org/'+uid,tags:['日本語 & <タグ>'],...extra});
module.exports={manifest:{updated_at:'2026-11-17T00:00:00+00:00',chunk_count:3,item_count:6,version:'test'},chunks:[
[row('A','ehentai','2026-11-15T00:00:00+00:00',{duplicate_group:'dupe',title_jp:'代表',rating:5}),row('B','hitomi','2026-11-16T00:01:34+00:00',{duplicate_group:'dupe'})],
[row('C','3hentai','2026-07-29 22:26:00-05',{pages:90}),row('D','asmhentai',null)],
[row('E','hitomi','2026-11-16T10:00:00+0900',{category:'後続',artists:['作者 & <新>']}),row('F','asmhentai','invalid')]]};
