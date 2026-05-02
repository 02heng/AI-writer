'use strict';

/**
 * 将 backend/app/data/themes.json 复制到 renderer/themes-bundled.json，
 * 供前端在后端暂未就绪或 /api/themes 失败时使用完整词条（避免仅有 7 条兜底）。
 */
const fs = require('fs');
const path = require('path');

const root = path.join(__dirname, '..');
const src = path.join(root, 'backend', 'app', 'data', 'themes.json');
const dst = path.join(root, 'renderer', 'themes-bundled.json');

if (!fs.existsSync(src)) {
  console.error('[sync-themes] Missing:', src);
  process.exit(1);
}
fs.copyFileSync(src, dst);
console.log('[sync-themes] OK →', dst);
