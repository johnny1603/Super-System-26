/* pack_pose_frames.js — make normalised pose frames light (docs/AGENT-LIFE.md §Pose frames).

   Step 3 of a pose set, after scripts/normalize_pose_frames.ps1. This machine
   has no WebP encoder, so each truecolour PNG becomes an 8-bit PALETTE PNG
   with per-entry alpha (tRNS): 255 colours chosen for that frame (median cut,
   then a few k-means passes) + 1 fully transparent entry. No dithering — a
   dither pattern differs per frame and would shimmer during a cross-fade.
   Node only (zlib), no package.

   node scripts/pack_pose_frames.js <in dir> <out dir>
*/
'use strict';
const fs = require('fs'), path = require('path'), zlib = require('zlib');

const SIG = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
const CRC = (() => { const t = new Int32Array(256); for (let n = 0; n < 256; n++) { let c = n;
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; t[n] = c; } return t; })();
function crc32(buf) { let c = -1; for (let i = 0; i < buf.length; i++) c = CRC[(c ^ buf[i]) & 255] ^ (c >>> 8); return (c ^ -1) >>> 0; }

function readPng(file) {
  const b = fs.readFileSync(file);
  if (!b.subarray(0, 8).equals(SIG)) throw new Error(file + ': not a PNG');
  let pos = 8, w = 0, h = 0, idat = [];
  while (pos < b.length) {
    const len = b.readUInt32BE(pos), type = b.toString('latin1', pos + 4, pos + 8), data = b.subarray(pos + 8, pos + 8 + len);
    if (type === 'IHDR') {
      w = data.readUInt32BE(0); h = data.readUInt32BE(4);
      if (data[8] !== 8 || data[9] !== 6 || data[12] !== 0) throw new Error(file + ': expects 8-bit RGBA, not interlaced');
    } else if (type === 'IDAT') idat.push(data);
    pos += 12 + len;
  }
  const raw = zlib.inflateSync(Buffer.concat(idat)), px = Buffer.alloc(w * h * 4), bpp = 4, stride = w * 4;
  for (let y = 0; y < h; y++) {
    const f = raw[y * (stride + 1)], line = raw.subarray(y * (stride + 1) + 1, (y + 1) * (stride + 1)), o = y * stride;
    for (let x = 0; x < stride; x++) {
      const a = x >= bpp ? px[o + x - bpp] : 0, up = y ? px[o - stride + x] : 0, c = y && x >= bpp ? px[o - stride + x - bpp] : 0;
      let v = line[x];
      if (f === 1) v += a; else if (f === 2) v += up; else if (f === 3) v += (a + up) >> 1;
      else if (f === 4) { const p = a + up - c, pa = Math.abs(p - a), pb = Math.abs(p - up), pc = Math.abs(p - c);
        v += pa <= pb && pa <= pc ? a : pb <= pc ? up : c; }
      px[o + x] = v & 255;
    }
  }
  return { w, h, px };
}

function chunk(type, data) {
  const head = Buffer.alloc(8); head.writeUInt32BE(data.length, 0); head.write(type, 4, 'latin1');
  const crc = Buffer.alloc(4); crc.writeUInt32BE(crc32(Buffer.concat([head.subarray(4), data])), 0);
  return Buffer.concat([head, data, crc]);
}

// median cut over the opaque-ish pixels (alpha counts like a colour), then k-means
function palette(px, n) {
  const pts = [];
  for (let i = 0; i < px.length; i += 4) if (px[i + 3] > 0) pts.push([px[i], px[i + 1], px[i + 2], px[i + 3]]);
  let boxes = [pts];
  while (boxes.length < n) {
    let bi = -1, bd = -1, bc = 0;
    boxes.forEach((bx, i) => { if (bx.length < 2) return;
      for (let c = 0; c < 4; c++) { let lo = 255, hi = 0; for (const p of bx) { if (p[c] < lo) lo = p[c]; if (p[c] > hi) hi = p[c]; }
        const d = (hi - lo) * Math.sqrt(bx.length); if (d > bd) { bd = d; bi = i; bc = c; } } });
    if (bi < 0) break;
    const bx = boxes[bi].sort((p, q) => p[bc] - q[bc]), mid = bx.length >> 1;
    boxes.splice(bi, 1, bx.slice(0, mid), bx.slice(mid));
  }
  let pal = boxes.map(bx => { const s = [0, 0, 0, 0]; for (const p of bx) for (let c = 0; c < 4; c++) s[c] += p[c];
    return s.map(v => v / bx.length); });
  for (let it = 0; it < 4; it++) {
    const sum = pal.map(() => [0, 0, 0, 0, 0]);
    for (const p of pts) { const k = nearest(pal, p[0], p[1], p[2], p[3]); const s = sum[k];
      s[0] += p[0]; s[1] += p[1]; s[2] += p[2]; s[3] += p[3]; s[4]++; }
    pal = pal.map((c, i) => sum[i][4] ? sum[i].slice(0, 4).map(v => v / sum[i][4]) : c);
  }
  return pal.map(c => c.map(v => Math.round(v)));
}
function nearest(pal, r, g, b, a) {
  let best = 0, bd = Infinity;
  for (let i = 0; i < pal.length; i++) { const c = pal[i], dr = c[0] - r, dg = c[1] - g, db = c[2] - b, da = (c[3] - a) * 1.5;
    const d = dr * dr * 0.3 + dg * dg * 0.59 + db * db * 0.11 + da * da; if (d < bd) { bd = d; best = i; } }
  return best;
}

function pack(inFile, outFile) {
  const { w, h, px } = readPng(inFile);
  const pal = palette(px, 255), cache = new Map(), idx = Buffer.alloc(w * h);
  for (let i = 0, j = 0; i < px.length; i += 4, j++) {
    if (px[i + 3] === 0) { idx[j] = 0; continue; }
    const key = (px[i] << 24 | px[i + 1] << 16 | px[i + 2] << 8 | px[i + 3]) >>> 0;
    let k = cache.get(key); if (k === undefined) { k = nearest(pal, px[i], px[i + 1], px[i + 2], px[i + 3]) + 1; cache.set(key, k); }
    idx[j] = k;
  }
  // per row, the filter with the smallest sum (the usual heuristic)
  const rows = [];
  for (let y = 0; y < h; y++) {
    const cur = idx.subarray(y * w, (y + 1) * w), up = y ? idx.subarray((y - 1) * w, y * w) : Buffer.alloc(w);
    let best = null, bs = Infinity;
    for (let f = 0; f < 5; f++) {
      const out = Buffer.alloc(w + 1); out[0] = f; let s = 0;
      for (let x = 0; x < w; x++) { const a = x ? cur[x - 1] : 0, b = up[x], c = x ? up[x - 1] : 0;
        let p = f === 0 ? 0 : f === 1 ? a : f === 2 ? b : f === 3 ? (a + b) >> 1 : (() => { const q = a + b - c,
          pa = Math.abs(q - a), pb = Math.abs(q - b), pc = Math.abs(q - c); return pa <= pb && pa <= pc ? a : pb <= pc ? b : c; })();
        const v = (cur[x] - p) & 255; out[x + 1] = v; s += v < 128 ? v : 256 - v; }
      if (s < bs) { bs = s; best = out; }
    }
    rows.push(best);
  }
  const ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(w, 0); ihdr.writeUInt32BE(h, 4); ihdr[8] = 8; ihdr[9] = 3;
  const plte = Buffer.alloc(256 * 3), trns = Buffer.alloc(256);
  pal.forEach((c, i) => { plte[(i + 1) * 3] = c[0]; plte[(i + 1) * 3 + 1] = c[1]; plte[(i + 1) * 3 + 2] = c[2]; trns[i + 1] = c[3]; });
  const png = Buffer.concat([SIG, chunk('IHDR', ihdr), chunk('PLTE', plte), chunk('tRNS', trns),
    chunk('IDAT', zlib.deflateSync(Buffer.concat(rows), { level: 9, memLevel: 9 })), chunk('IEND', Buffer.alloc(0))]);
  fs.writeFileSync(outFile, png);
  return { before: fs.statSync(inFile).size, after: png.length };
}

if (require.main === module) {
  const [inDir, outDir] = process.argv.slice(2);
  if (!inDir || !outDir) { console.error('usage: node scripts/pack_pose_frames.js <in dir> <out dir>'); process.exit(2); }
  fs.mkdirSync(outDir, { recursive: true });
  let b = 0, a = 0;
  for (const name of fs.readdirSync(inDir).filter(n => n.toLowerCase().endsWith('.png')).sort()) {
    const r = pack(path.join(inDir, name), path.join(outDir, name)); b += r.before; a += r.after;
    console.log(`${name.padEnd(32)} ${(r.before / 1024).toFixed(0).padStart(5)} KB -> ${(r.after / 1024).toFixed(0).padStart(4)} KB`);
  }
  console.log(`total ${(b / 1024).toFixed(0)} KB -> ${(a / 1024).toFixed(0)} KB`);
}
module.exports = { pack, readPng };
