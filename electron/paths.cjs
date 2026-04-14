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

module.exports = {
  resolvePreferredDataRoot,
  applyNonSystemDrivePaths,
  resolveEarlyLogFile
};
