'use strict';

/**
 * Rasterize build/logo.svg → build/icon.png (512×512) for Electron + electron-builder.
 */
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import sharp from 'sharp';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.join(__dirname, '..');
const svgPath = path.join(root, 'build', 'logo.svg');
const pngPath = path.join(root, 'build', 'icon.png');

if (!fs.existsSync(svgPath)) {
  console.error('[render-app-icon] Missing:', svgPath);
  process.exit(1);
}

await sharp(svgPath).resize(512, 512).png({ compressionLevel: 9 }).toFile(pngPath);
console.log('[render-app-icon] OK →', pngPath);
