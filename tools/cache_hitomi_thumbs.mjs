import fs from 'node:fs';
import path from 'node:path';
import hitomi, { Extension, ThumbnailSize } from 'node-hitomi';

const DATA = path.resolve('docs/data.json');
const STATUS = path.resolve('docs/source_status.json');
const OUTDIR = path.resolve('docs/thumbs/hitomi');
const LIMIT = Number(process.env.HITOMI_THUMB_LIMIT || 110);
const DELAY_MS = Number(process.env.HITOMI_THUMB_DELAY_MS || 120);

const sleep = ms => new Promise(r => setTimeout(r, ms));
const load = p => JSON.parse(fs.readFileSync(p, 'utf8'));
const save = (p, obj) => fs.writeFileSync(p, JSON.stringify(obj, null, 2) + '\n');

if (!fs.existsSync(DATA)) {
  console.error('[hitomi-thumbs] docs/data.json not found');
  process.exit(0);
}
fs.mkdirSync(OUTDIR, { recursive: true });

const data = load(DATA);
const items = Array.isArray(data.items) ? data.items : [];
const hitomiItems = items.filter(x => x && x.source === 'hitomi' && /^\d+$/.test(String(x.source_id || '')));

let attempted = 0, cached = 0, reused = 0, failed = 0;
const failures = [];

for (const item of hitomiItems) {
  const gid = String(item.source_id);
  const rel = `thumbs/hitomi/${gid}.webp`;
  const dest = path.resolve('docs', rel);

  if (fs.existsSync(dest) && fs.statSync(dest).size > 512) {
    item.thumbnail = rel;
    reused++;
    continue;
  }
  if (attempted >= LIMIT) continue;
  attempted++;

  try {
    const gallery = await hitomi.galleries.retrieve(Number(gid));
    const thumbs = gallery.getThumbnails();
    if (!thumbs || !thumbs.length) throw new Error('no representative thumbnails');

    let bytes = null;
    let lastErr = null;
    // Small WebP is compact and is sufficient for the card grid.
    for (const img of thumbs.slice(0, 2)) {
      try {
        bytes = await img.fetch(Extension.Webp, ThumbnailSize.Small);
        if (bytes && bytes.length > 512) break;
      } catch (e) {
        lastErr = e;
      }
    }
    if (!bytes || bytes.length <= 512) throw lastErr || new Error('thumbnail fetch returned no data');

    fs.writeFileSync(dest, Buffer.from(bytes));
    item.thumbnail = rel;
    cached++;
    if (cached <= 3) console.log(`[hitomi-thumbs] cached ${gid} -> ${rel} (${bytes.length} bytes)`);
  } catch (e) {
    failed++;
    if (failures.length < 5) failures.push(`${gid}: ${String(e?.message || e)}`);
  }
  await sleep(DELAY_MS);
}

save(DATA, data);

let status = {};
try { if (fs.existsSync(STATUS)) status = load(STATUS); } catch {}
status.hitomi = status.hitomi || {};
status.hitomi.thumbnail_cache = {
  attempted,
  cached,
  reused,
  failed,
  pending: Math.max(0, hitomiItems.length - reused - cached),
  failures
};
save(STATUS, status);

console.log(`[hitomi-thumbs] total=${hitomiItems.length} attempted=${attempted} cached=${cached} reused=${reused} failed=${failed}`);
