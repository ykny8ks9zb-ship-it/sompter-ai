const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  ask: (prompt) => ipcRenderer.invoke('ask', { prompt }),
  controlPlan: () => ipcRenderer.invoke('controlPlan'),
  runAction: (action, params) => ipcRenderer.invoke('runAction', { action, params }),
  opencodeRun: (projectPath, task) => ipcRenderer.invoke('opencodeRun', { projectPath, task }),
  projectStatus: (projectPath) => ipcRenderer.invoke('projectStatus', { projectPath }),
  projectDiff: (projectPath) => ipcRenderer.invoke('projectDiff', { projectPath }),
  runTest: (projectPath, command) => ipcRenderer.invoke('runTest', { projectPath, command }),
  openFolder: (projectPath) => ipcRenderer.invoke('openFolder', { projectPath }),
  openFile: (filePath) => ipcRenderer.invoke('openFile', { filePath }),

  // Window state
  initWindow: (collapsed, y) => ipcRenderer.invoke('initWindow', collapsed, y),
  collapseWindow: (y) => ipcRenderer.invoke('collapseWindow', y),
  expandWindow: (y) => ipcRenderer.invoke('expandWindow', y),

  onWindowStateChanged: (callback) => {
    ipcRenderer.on('window-state-changed', (_event, collapsed) => callback(collapsed));
  },
  onYPositionChanged: (callback) => {
    ipcRenderer.on('y-position-changed', (_event, y) => callback(y));
  },

  // Health
  getHealth: () => ipcRenderer.invoke('getHealth'),

  // Setup
  getSetupStatus: () => ipcRenderer.invoke('getSetupStatus'),
  testScreenshot: () => ipcRenderer.invoke('testScreenshot'),
  testControl: () => ipcRenderer.invoke('testControl'),
  testOpencode: () => ipcRenderer.invoke('testOpencode'),
  openPrivacySettings: (pane) => ipcRenderer.invoke('openPrivacySettings', pane),

  // Folder selection
  selectFolder: () => ipcRenderer.invoke('selectFolder'),

  // Global toggle
  onGlobalToggle: (callback) => {
    ipcRenderer.on('global-toggle', () => callback());
  },
});
