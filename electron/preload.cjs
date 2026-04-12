'use strict';

const { contextBridge } = require('electron');
const { ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('aiWriter', {
  platform: process.platform,
  getPaths: () => ipcRenderer.invoke('aiwriter:get-paths'),
  getBackendUrl: () => ipcRenderer.invoke('aiwriter:get-backend-url'),
  loadSettings: () => ipcRenderer.invoke('aiwriter:load-settings'),
  saveSettings: (data) => ipcRenderer.invoke('aiwriter:save-settings', data),
  restartBackend: () => ipcRenderer.invoke('aiwriter:restart-backend')
});
