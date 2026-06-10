const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  ask: (prompt) => ipcRenderer.invoke('ask', { prompt }),
  controlPlan: () => ipcRenderer.invoke('controlPlan'),
  runAction: (action, params) => ipcRenderer.invoke('runAction', { action, params }),
  runBrowserAction: (action, params) => ipcRenderer.invoke('runBrowserAction', { action, params }),
  opencodeRun: (projectPath, task) => ipcRenderer.invoke('opencodeRun', { projectPath, task }),
  projectStatus: (projectPath) => ipcRenderer.invoke('projectStatus', { projectPath }),
  projectDiff: (projectPath) => ipcRenderer.invoke('projectDiff', { projectPath }),
  runTest: (projectPath, command) => ipcRenderer.invoke('runTest', { projectPath, command }),
  openFolder: (projectPath) => ipcRenderer.invoke('openFolder', { projectPath }),
  openFile: (filePath) => ipcRenderer.invoke('openFile', { filePath }),
  openREADME: () => ipcRenderer.invoke('openREADME'),

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

  // Smart Fix
  smartFix: (projectPath, projectName, userPrompt) => ipcRenderer.invoke('smartFix', { projectPath, projectName, userPrompt }),

  // Run Snapshots
  runsList: (projectPath) => ipcRenderer.invoke('runsList', { projectPath }),
  runsDetail: (projectPath, runId) => ipcRenderer.invoke('runsDetail', { projectPath, runId }),
  runsUndo: (projectPath, runId) => ipcRenderer.invoke('runsUndo', { projectPath, runId }),

  // Diagnostics
  getDiagnostics: (projectPath) => ipcRenderer.invoke('getDiagnostics', { projectPath }),
  saveDiagnosticsReport: (projectPath, data) => ipcRenderer.invoke('saveDiagnosticsReport', { projectPath, data }),

  // Provider Settings
  getSettings: () => ipcRenderer.invoke('getSettings'),
  saveSettings: (data) => ipcRenderer.invoke('saveSettings', data),
  testProvider: (provider) => ipcRenderer.invoke('testProvider', { provider }),
  openEnvFile: () => ipcRenderer.invoke('openEnvFile'),

  // Service Controls
  getServiceStatus: () => ipcRenderer.invoke('getServiceStatus'),
  runServiceAction: (action) => ipcRenderer.invoke('runServiceAction', { action }),
  openLogsFolder: () => ipcRenderer.invoke('openLogsFolder'),

  // Menu Bar / Dock / Notifications
  getMenuBarPrefs: () => ipcRenderer.invoke('getMenuBarPrefs'),
  saveMenuBarPrefs: (prefs) => ipcRenderer.invoke('saveMenuBarPrefs', prefs),
  showNotification: (title, body) => ipcRenderer.invoke('showNotification', { title, body }),

  // About
  getAboutInfo: () => ipcRenderer.invoke('getAboutInfo'),

  // App Management
  appReset: () => ipcRenderer.invoke('appReset'),
  appExportLogs: () => ipcRenderer.invoke('appExportLogs'),
  appSafeMode: () => ipcRenderer.invoke('appSafeMode'),

  onMenuAction: (callback) => {
    ipcRenderer.on('menu-action', (_event, action) => callback(action));
  },

  // Global toggle
  onGlobalToggle: (callback) => {
    ipcRenderer.on('global-toggle', () => callback());
  },
});
