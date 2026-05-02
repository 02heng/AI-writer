'use strict';

const { app, BrowserWindow, session, shell, ipcMain, dialog } = require('electron');
const fs = require('fs');
const path = require('path');
const os = require('os');
const {
  applyNonSystemDrivePaths,
  resolveAnalyticsRoot,
  resolveEarlyLogFile,
  resolveSnapshotRoot
} = require('./paths.cjs');
const { startBackend, stopBackend, getBackendBaseUrl } = require('./backend.cjs');
const {
  initSnapshotScheduler,
  disposeSnapshotScheduler,
  openLoginWindow,
  captureSnapshot,
  getSnapshotInfo,
  notifySettingsMayHaveEnabledSnap
} = require('./snapshot-scheduler.cjs');

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
    let logFile = resolveEarlyLogFile('debug-d7648d.log');
    if (!logFile && app.isReady()) {
      try {
        logFile = path.join(app.getPath('logs'), 'debug-d7648d.log');
      } catch {
        logFile = null;
      }
    }
    if (!logFile) {
      const dir = path.join(os.tmpdir(), 'aiwriter-debug');
      fs.mkdirSync(dir, { recursive: true });
      logFile = path.join(dir, 'debug-d7648d.log');
    } else {
      fs.mkdirSync(path.dirname(logFile), { recursive: true });
    }
    fs.appendFileSync(logFile, payload + '\n', 'utf8');
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

  ipcMain.handle('aiwriter:get-paths', () => {
    let snapshotRoot = '';
    let analyticsRoot = '';
    try {
      snapshotRoot = resolveSnapshotRoot(app) || '';
    } catch {
      snapshotRoot = '';
    }
    try {
      analyticsRoot = resolveAnalyticsRoot(app) || '';
    } catch {
      analyticsRoot = '';
    }
    return {
      userData: app.getPath('userData'),
      downloads: app.getPath('downloads'),
      cache: app.getPath('cache'),
      logs: app.getPath('logs'),
      snapshotRoot,
      analyticsRoot
    };
  });

  ipcMain.handle('aiwriter:get-backend-url', () => getBackendBaseUrl());

  ipcMain.handle('aiwriter:load-settings', () => {
    try {
      const raw = fs.readFileSync(settingsPath(), 'utf8');
      const s = JSON.parse(raw);
      return {
        deepseekApiKey: s.deepseekApiKey || '',
        deepseekModel: s.deepseekModel || 'deepseek-v4-flash',
        booksRoot: s.booksRoot || '',
        snapshotAgentEnabled: Boolean(s.snapshotAgentEnabled),
        snapshotPageUrl:
          (s.snapshotPageUrl && String(s.snapshotPageUrl).trim()) ||
          'https://fanqienovel.com/main/writer/data?bookId=7628439872088329241',
        metricsDomSelectors: Array.isArray(s.metricsDomSelectors) ? s.metricsDomSelectors : []
      };
    } catch {
      return {
        deepseekApiKey: '',
        deepseekModel: 'deepseek-v4-flash',
        booksRoot: '',
        snapshotAgentEnabled: false,
        snapshotPageUrl: 'https://fanqienovel.com/main/writer/data?bookId=7628439872088329241',
        metricsDomSelectors: []
      };
    }
  });

  ipcMain.handle('aiwriter:save-settings', async (_e, data) => {
    const dir = path.dirname(settingsPath());
    try {
      fs.mkdirSync(dir, { recursive: true });
    } catch (e) {
      return {
        ok: false,
        settingsSaved: false,
        restartOk: false,
        restartError: null,
        writeError: e.message || String(e)
      };
    }
    const prev = {};
    try {
      Object.assign(prev, JSON.parse(fs.readFileSync(settingsPath(), 'utf8')));
    } catch {
      // ignore
    }
    let metricsDomSelectors = Array.isArray(prev.metricsDomSelectors)
      ? prev.metricsDomSelectors
      : [];
    if (data.metricsDomSelectors !== undefined) {
      if (Array.isArray(data.metricsDomSelectors)) {
        metricsDomSelectors = data.metricsDomSelectors.filter(
          (x) => x && typeof x.key === 'string' && typeof x.selector === 'string'
        );
      }
    }
    const merged = {
      deepseekApiKey: data.deepseekApiKey ?? prev.deepseekApiKey ?? '',
      deepseekModel: data.deepseekModel ?? prev.deepseekModel ?? 'deepseek-v4-flash',
      booksRoot: data.booksRoot !== undefined ? data.booksRoot : prev.booksRoot || '',
      snapshotAgentEnabled:
        data.snapshotAgentEnabled !== undefined
          ? Boolean(data.snapshotAgentEnabled)
          : Boolean(prev.snapshotAgentEnabled),
      snapshotPageUrl:
        data.snapshotPageUrl !== undefined
          ? String(data.snapshotPageUrl || '').trim()
          : prev.snapshotPageUrl ||
            'https://fanqienovel.com/main/writer/data?bookId=7628439872088329241',
      metricsDomSelectors
    };
    let writeError = null;
    try {
      fs.writeFileSync(settingsPath(), JSON.stringify(merged, null, 2), 'utf8');
    } catch (e) {
      writeError = e.message || String(e);
      return {
        ok: false,
        settingsSaved: false,
        restartOk: false,
        restartError: null,
        writeError
      };
    }
    if (merged.snapshotAgentEnabled) {
      try {
        notifySettingsMayHaveEnabledSnap();
      } catch {
        /* noop */
      }
    }
    stopBackend();
    try {
      await startBackend({ userDataPath: app.getPath('userData'), projectRoot });
      return { ok: true, settingsSaved: true, restartOk: true, restartError: null };
    } catch (e) {
      const restartError = e.message || String(e);
      console.error('[ipc] save-settings backend restart:', restartError);
      return {
        ok: true,
        settingsSaved: true,
        restartOk: false,
        restartError
      };
    }
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
    try {
      await startBackend({ userDataPath: app.getPath('userData'), projectRoot });
      return { ok: true, restartOk: true, restartError: null };
    } catch (e) {
      const restartError = e.message || String(e);
      console.error('[ipc] restart-backend:', restartError);
      return { ok: true, restartOk: false, restartError };
    }
  });

  ipcMain.handle('aiwriter:open-snapshot-login', async () => {
    openLoginWindow();
    return { ok: true };
  });

  ipcMain.handle('aiwriter:get-snapshot-info', async () => getSnapshotInfo());

  ipcMain.handle('aiwriter:test-snapshot-now', async (_e, payload) => {
    const slot = payload && payload.slot === 'evening' ? 'evening' : 'morning';
    return captureSnapshot(slot, { force: true });
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
  initSnapshotScheduler({ app });

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on('before-quit', () => {
  disposeSnapshotScheduler();
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
