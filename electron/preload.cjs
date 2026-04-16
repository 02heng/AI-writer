'use strict';

const { contextBridge } = require('electron');
const { ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('aiWriter', {
  platform: process.platform,
  getPaths: () => ipcRenderer.invoke('aiwriter:get-paths'),
  getBackendUrl: () => ipcRenderer.invoke('aiwriter:get-backend-url'),
  loadSettings: () => ipcRenderer.invoke('aiwriter:load-settings'),
  saveSettings: (data) => ipcRenderer.invoke('aiwriter:save-settings', data),
  restartBackend: () => ipcRenderer.invoke('aiwriter:restart-backend'),
  pickBooksDir: () => ipcRenderer.invoke('aiwriter:pick-books-dir'),
  saveTextFileAs: (opts) => ipcRenderer.invoke('aiwriter:save-text-file', opts),
  openSnapshotLogin: () => ipcRenderer.invoke('aiwriter:open-snapshot-login'),
  getSnapshotInfo: () => ipcRenderer.invoke('aiwriter:get-snapshot-info'),
  testSnapshotNow: (slot) =>
    ipcRenderer.invoke('aiwriter:test-snapshot-now', { slot: slot === 'evening' ? 'evening' : 'morning' })
});
