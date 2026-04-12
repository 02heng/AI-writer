'use strict';

const { app, BrowserWindow, session, shell, ipcMain } = require('electron');
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
      deepseekModel: s.deepseekModel || 'deepseek-chat'
    };
  } catch {
    return { deepseekApiKey: '', deepseekModel: 'deepseek-chat' };
  }
});

ipcMain.handle('aiwriter:save-settings', async (_e, data) => {
  const dir = path.dirname(settingsPath());
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(settingsPath(), JSON.stringify(data, null, 2), 'utf8');
  stopBackend();
  await startBackend({ userDataPath: app.getPath('userData'), projectRoot });
  return { ok: true };
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
