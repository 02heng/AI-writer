'use strict';

const { app, BrowserWindow, session, shell, ipcMain, dialog } = require('electron');
const fs = require('fs');
const path = require('path');
const os = require('os');
const { applyNonSystemDrivePaths } = require('./paths.cjs');
const { startBackend, stopBackend, getBackendBaseUrl } = require('./backend.cjs');

// #region agent log
function agentLog(location, message, data, hypothesisId) {
  const row = {
    sessionId: 'd7648d',
    timestamp: Date.now(),
    location,
    message,
    data: data || {},
    hypothesisId: hypothesisId || 'H0'
  };
  const payload = JSON.stringify(row);
  fetch('http://127.0.0.1:7358/ingest/ec74e965-0955-4757-aff0-bed113fed1c4', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Debug-Session-Id': 'd7648d' },
    body: payload
  }).catch(() => {});
  try {
    let dir;
    try {
      dir = app.isReady() ? app.getPath('userData') : path.join(os.tmpdir(), 'aiwriter-debug');
    } catch {
      dir = path.join(os.tmpdir(), 'aiwriter-debug');
    }
    fs.mkdirSync(dir, { recursive: true });
    fs.appendFileSync(path.join(dir, 'debug-d7648d.log'), payload + '\n', 'utf8');
  } catch (_) {}
  try {
    const repoLog = path.join(__dirname, '..', 'debug-d7648d.log');
    fs.appendFileSync(repoLog, payload + '\n', 'utf8');
  } catch (_) {}
}
// #endregion

applyNonSystemDrivePaths(app);

const projectRoot = path.join(__dirname, '..');

agentLog(
  'main.cjs:boot',
  'main module loaded',
  { __dirname, electron: process.versions.electron, execPath: process.execPath },
  'H1'
);

function settingsPath() {
  return path.join(app.getPath('userData'), 'settings.json');
}

function resolveSenderWindow(event) {
  let win = BrowserWindow.fromWebContents(event.sender);
  if (win && !win.isDestroyed()) return win;
  win = BrowserWindow.getFocusedWindow();
  if (win && !win.isDestroyed()) return win;
  const all = BrowserWindow.getAllWindows();
  return all.find((w) => w && !w.isDestroyed()) || null;
}

const IPC_CHANNELS = [
  'aiwriter:get-paths',
  'aiwriter:get-backend-url',
  'aiwriter:load-settings',
  'aiwriter:save-settings',
  'aiwriter:pick-books-dir',
  'aiwriter:restart-backend'
];

function safeExportFileName(name) {
  const s = String(name || 'novel')
    .replace(/[<>:"/\\|?*\n\r\t]+/g, '_')
    .trim()
    .slice(0, 120);
  return s || 'novel';
}

/** 另存为导出 TXT：单独注册，避免与其它 IPC 批量 remove/register 时序问题；主进程加载时即可用。 */
function registerSaveTextFileHandler() {
  try {
    ipcMain.removeHandler('aiwriter:save-text-file');
  } catch (_) {
    /* noop */
  }
  ipcMain.handle('aiwriter:save-text-file', async (event, payload) => {
    const raw = (payload && payload.defaultFileName) || 'novel.txt';
    const content = (payload && payload.content) != null ? String(payload.content) : '';
    let base = safeExportFileName(raw.replace(/\.txt$/i, ''));
    const withExt = base.toLowerCase().endsWith('.txt') ? base : `${base}.txt`;
    const win = resolveSenderWindow(event);
    const defaultPath = path.join(app.getPath('downloads'), withExt);
    const saveOpts = {
      title: '导出整本 TXT',
      defaultPath,
      buttonLabel: '保存',
      filters: [{ name: '文本 (*.txt)', extensions: ['txt'] }]
    };
    if (process.platform === 'darwin') {
      saveOpts.properties = ['createDirectory'];
    }
    const r = await dialog.showSaveDialog(win ?? undefined, saveOpts);
    if (r.canceled || !r.filePath) {
      return { ok: false, canceled: true };
    }
    try {
      fs.writeFileSync(r.filePath, content, 'utf8');
      return { ok: true, path: r.filePath };
    } catch (err) {
      return { ok: false, canceled: false, error: err.message || String(err) };
    }
  });
}

registerSaveTextFileHandler();

function registerAllIpcHandlers() {
  for (const ch of IPC_CHANNELS) {
    try {
      ipcMain.removeHandler(ch);
    } catch (_) {
      /* noop */
    }
  }

  ipcMain.handle('aiwriter:get-paths', () => ({
    userData: app.getPath('userData'),
    downloads: app.getPath('downloads'),
    cache: app.getPath('cache'),
    logs: app.getPath('logs')
  }));

  ipcMain.handle('aiwriter:get-backend-url', () => getBackendBaseUrl());

  ipcMain.handle('aiwriter:load-settings', () => {
    try {
      const raw = fs.readFileSync(settingsPath(), 'utf8');
      const s = JSON.parse(raw);
      return {
        deepseekApiKey: s.deepseekApiKey || '',
        deepseekModel: s.deepseekModel || 'deepseek-chat',
        booksRoot: s.booksRoot || ''
      };
    } catch {
      return { deepseekApiKey: '', deepseekModel: 'deepseek-chat', booksRoot: '' };
    }
  });

  ipcMain.handle('aiwriter:save-settings', async (_e, data) => {
    const dir = path.dirname(settingsPath());
    fs.mkdirSync(dir, { recursive: true });
    const prev = {};
    try {
      Object.assign(prev, JSON.parse(fs.readFileSync(settingsPath(), 'utf8')));
    } catch {
      // ignore
    }
    const merged = {
      deepseekApiKey: data.deepseekApiKey ?? prev.deepseekApiKey ?? '',
      deepseekModel: data.deepseekModel ?? prev.deepseekModel ?? 'deepseek-chat',
      booksRoot: data.booksRoot !== undefined ? data.booksRoot : prev.booksRoot || ''
    };
    fs.writeFileSync(settingsPath(), JSON.stringify(merged, null, 2), 'utf8');
    stopBackend();
    await startBackend({ userDataPath: app.getPath('userData'), projectRoot });
    return { ok: true };
  });

  ipcMain.handle('aiwriter:pick-books-dir', async (event) => {
    agentLog('main.cjs:pick-books-dir:invoke', 'handler entered', {}, 'H2');
    const win = resolveSenderWindow(event);
    const properties = ['openDirectory'];
    if (process.platform === 'darwin') properties.push('createDirectory');
    const r = await dialog.showOpenDialog(win ?? undefined, {
      title: '选择书本存放目录',
      properties,
      buttonLabel: '选择此文件夹'
    });
    if (r.canceled || !r.filePaths || !r.filePaths[0]) return '';
    return r.filePaths[0];
  });

  ipcMain.handle('aiwriter:restart-backend', async () => {
    stopBackend();
    await startBackend({ userDataPath: app.getPath('userData'), projectRoot });
    return { ok: true };
  });

  agentLog('main.cjs:ipc', 'registerAllIpcHandlers done', { channels: IPC_CHANNELS }, 'H1');
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1100,
    height: 820,
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false
    }
  });

  win.loadFile(path.join(__dirname, '..', 'renderer', 'index.html'));

  session.defaultSession.on('will-download', (_event, item) => {
    const dir = app.getPath('downloads');
    const savePath = path.join(dir, item.getFilename());
    item.setSavePath(savePath);
  });
}

app.whenReady().then(async () => {
  registerSaveTextFileHandler();
  registerAllIpcHandlers();

  try {
    await startBackend({
      userDataPath: app.getPath('userData'),
      projectRoot
    });
  } catch (e) {
    console.error('[AI-writer] 后端启动失败:', e.message || e);
  }

  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on('before-quit', () => {
  stopBackend();
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('web-contents-created', (_event, contents) => {
  contents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith('http:') || url.startsWith('https:')) {
      shell.openExternal(url);
      return { action: 'deny' };
    }
    return { action: 'allow' };
  });
});
