'use strict';

/**
 * 从 build/icon.png 生成 build/icon.ico。
 * - NSIS 安装程序对 ICO 更可靠；仅用 PNG 时常显示默认「地球」图标。
 * - png-to-ico 要求正方形：横版 Logo 先垫黑边再缩放。
 */
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import sharp from 'sharp';
import pngToIco from 'png-to-ico';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.join(__dirname, '..');
const pngPath = path.join(root, 'build', 'icon.png');
const icoPath = path.join(root, 'build', 'icon.ico');

if (!fs.existsSync(pngPath)) {
  console.error('[make-windows-ico] Missing:', pngPath);
  process.exit(1);
}

const meta = await sharp(pngPath).metadata();
const w = meta.width ?? 0;
const h = meta.height ?? 0;
if (!w || !h) {
  console.error('[make-windows-ico] Invalid PNG dimensions');
  process.exit(1);
}

const side = Math.max(w, h);
const top = Math.floor((side - h) / 2);
const bottom = Math.ceil((side - h) / 2);
const left = Math.floor((side - w) / 2);
const right = Math.ceil((side - w) / 2);

const padded = await sharp(pngPath)
  .extend({
    top,
    bottom,
    left,
    right,
    background: { r: 0, g: 0, b: 0, alpha: 1 }
  })
  .png()
  .toBuffer();

const squarePng = await sharp(padded).resize(512, 512).png().toBuffer();

const buf = await pngToIco(squarePng);
fs.writeFileSync(icoPath, buf);
console.log('[make-windows-ico] OK →', icoPath, `(source ${w}×${h})`);
