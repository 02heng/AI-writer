'use strict';

const fs = require('fs');
const path = require('path');

/**
 * Resolve a writable root on D: or E: (not C:).
 * Override with env AIWRITER_DATA_ROOT (absolute path).
 * Preference: D:\AI-writer-data then E:\AI-writer-data when drives exist.
 */
function resolvePreferredDataRoot() {
  const envRoot = process.env.AIWRITER_DATA_ROOT;
  if (envRoot && envRoot.trim()) {
    return path.resolve(envRoot.trim());
  }

  if (process.platform !== 'win32') {
    return null;
  }

  const candidates = ['D:\\AI-writer-data', 'E:\\AI-writer-data'];
  for (const dir of candidates) {
    const drive = path.parse(dir).root;
    try {
      if (fs.existsSync(drive)) {
        return dir;
      }
    } catch {
      // ignore
    }
  }

  return null;
}

function ensureDirSync(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

/**
 * Apply before app.ready. Redirects Electron paths away from C: when possible.
 * @param {import('electron').App} app
 */
function applyNonSystemDrivePaths(app) {
  const root = resolvePreferredDataRoot();
  if (!root) {
    console.warn(
      '[AI-writer] No D:/E: data root found and AIWRITER_DATA_ROOT unset — using default Electron paths (may be on C:).'
    );
    return;
  }

  try {
    ensureDirSync(root);
  } catch (e) {
    console.error('[AI-writer] Cannot create data root:', root, e);
    return;
  }

  const userData = path.join(root, 'UserData');
  const cache = path.join(root, 'Cache');
  const downloads = path.join(root, 'Downloads');
  const logs = path.join(root, 'Logs');
  const temp = path.join(root, 'Temp');

  try {
    ensureDirSync(userData);
    ensureDirSync(cache);
    ensureDirSync(downloads);
    ensureDirSync(logs);
    ensureDirSync(temp);
  } catch (e) {
    console.error('[AI-writer] Cannot create subfolders under', root, e);
    return;
  }

  app.setPath('userData', userData);
  app.setPath('cache', cache);
  app.setPath('downloads', downloads);
  app.setPath('logs', logs);

  console.log('[AI-writer] Data root:', root);
}

/**
 * 主进程尽早写日志用（app.ready 之前）：与 UserData 同盘的数据根下 Logs。
 */
function resolveEarlyLogFile(fileName) {
  const root = resolvePreferredDataRoot();
  if (!root) return null;
  try {
    const logs = path.join(root, 'Logs');
    ensureDirSync(logs);
    return path.join(logs, fileName);
  } catch {
    return null;
  }
}

/**
 * 每日页面快照目录：优先 D:/E: 数据根下 Snapshots；否则在 Windows 上若 D: 存在则用 D:\\AI-writer-data\\Snapshots。
 * @param {import('electron').App | null} app — 若无可写数据盘时，用 app.getPath('userData')/Snapshots（可能仍在 D 盘 UserData 下）
 */
function resolveSnapshotRoot(app) {
  const root = resolvePreferredDataRoot();
  if (root) {
    const p = path.join(root, 'Snapshots');
    try {
      ensureDirSync(p);
      return p;
    } catch {
      // fall through
    }
  }
  if (process.platform === 'win32') {
    try {
      if (fs.existsSync('D:\\')) {
        const base = path.join('D:', 'AI-writer-data');
        const p = path.join(base, 'Snapshots');
        ensureDirSync(base);
        ensureDirSync(p);
        return p;
      }
    } catch {
      // fall through
    }
  }
  if (app && typeof app.getPath === 'function') {
    const fallback = path.join(app.getPath('userData'), 'Snapshots');
    try {
      ensureDirSync(fallback);
      return fallback;
    } catch {
      return fallback;
    }
  }
  return null;
}

/**
 * 分析/审核/指标根目录：与 UserData 同级 D:\\AI-writer-data\\Analytics。
 * 环境变量 AIWRITER_ANALYTICS_ROOT 可覆盖（与后端 paths.analytics_root 对齐）。
 */
function resolveAnalyticsRoot(app) {
  const env = process.env.AIWRITER_ANALYTICS_ROOT;
  if (env && String(env).trim()) {
    const p = path.resolve(String(env).trim());
    try {
      ensureDirSync(p);
      ensureDirSync(path.join(p, 'reviews'));
      ensureDirSync(path.join(p, 'metrics'));
      ensureDirSync(path.join(p, 'state'));
      return p;
    } catch {
      return null;
    }
  }
  const root = resolvePreferredDataRoot();
  if (root) {
    const p = path.join(root, 'Analytics');
    try {
      ensureDirSync(p);
      ensureDirSync(path.join(p, 'reviews'));
      ensureDirSync(path.join(p, 'metrics'));
      ensureDirSync(path.join(p, 'state'));
      return p;
    } catch {
      // fall through
    }
  }
  if (process.platform === 'win32') {
    try {
      if (fs.existsSync('D:\\')) {
        const base = path.join('D:', 'AI-writer-data');
        const p = path.join(base, 'Analytics');
        ensureDirSync(base);
        ensureDirSync(p);
        ensureDirSync(path.join(p, 'reviews'));
        ensureDirSync(path.join(p, 'metrics'));
        ensureDirSync(path.join(p, 'state'));
        return p;
      }
    } catch {
      // fall through
    }
  }
  if (app && typeof app.getPath === 'function') {
    const fallback = path.join(app.getPath('userData'), 'Analytics');
    try {
      ensureDirSync(fallback);
      ensureDirSync(path.join(fallback, 'reviews'));
      ensureDirSync(path.join(fallback, 'metrics'));
      ensureDirSync(path.join(fallback, 'state'));
      return fallback;
    } catch {
      return fallback;
    }
  }
  return null;
}

module.exports = {
  resolvePreferredDataRoot,
  applyNonSystemDrivePaths,
  resolveEarlyLogFile,
  resolveSnapshotRoot,
  resolveAnalyticsRoot
};
