'use strict';

const { app, BrowserWindow, session, shell, ipcMain, dialog } = require('electron');
const fs = require('fs');
const path = require('path');
const { applyNonSystemDrivePaths } = require('./paths.cjs');
const { startBackend, stopBackend, getBackendBaseUrl } = require('./backend.cjs');

applyNonSystemDrivePaths(app);

const projectRoot = path.join(__dirname, '..');

function settingsPath() {
  return path.join(app.getPath('userData'), 'settings.json');
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

ipcMain.handle('aiwriter:pick-books-dir', async () => {
  const win = BrowserWindow.getFocusedWindow();
  const r = await dialog.showOpenDialog(win || undefined, {
    properties: ['openDirectory', 'createDirectory']
  });
  if (r.canceled || !r.filePaths || !r.filePaths[0]) return '';
  return r.filePaths[0];
});

ipcMain.handle('aiwriter:restart-backend', async () => {
  stopBackend();
  await startBackend({ userDataPath: app.getPath('userData'), projectRoot });
  return { ok: true };
});

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
