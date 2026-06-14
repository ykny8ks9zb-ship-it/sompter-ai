const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  ask: (prompt) => ipcRenderer.invoke('ask', { prompt }),
  startAskStream: (prompt) => ipcRenderer.invoke('startAskStream', { prompt }),
  onChatChunk: (callback) => {
    ipcRenderer.on('chat-chunk', (_event, data) => callback(data));
  },
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
  openExternalURL: (url) => ipcRenderer.invoke('openExternalURL', url),
  writeOnboardingState: (val) => ipcRenderer.invoke('writeOnboardingState', val),
  checkOnboardingState: () => ipcRenderer.invoke('checkOnboardingState'),

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
  startBackend: () => ipcRenderer.invoke('startBackend'),
  checkOllamaModels: () => ipcRenderer.invoke('checkOllamaModels'),
  pullOllamaModel: (model) => ipcRenderer.invoke('pullOllamaModel', model),

  // Folder selection
  selectFolder: () => ipcRenderer.invoke('selectFolder'),
  checkPathExists: (p) => ipcRenderer.invoke('checkPathExists', { path: p }),

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

  quitApp: () => ipcRenderer.invoke('quitApp'),

  // Watch Mode
  startWatchMode: () => ipcRenderer.invoke('startWatchMode'),
  stopWatchMode: () => ipcRenderer.invoke('stopWatchMode'),
  getWatchStatus: () => ipcRenderer.invoke('getWatchStatus'),
  getDaemonStatus: () => ipcRenderer.invoke('getDaemonStatus'),
  startDaemon: () => ipcRenderer.invoke('startDaemon'),
  stopDaemon: () => ipcRenderer.invoke('stopDaemon'),
  restartDaemon: () => ipcRenderer.invoke('restartDaemon'),
  getMemoryData: () => ipcRenderer.invoke('getMemoryData'),
  searchMemory: (query) => ipcRenderer.invoke('searchMemory', query),
  exportMemory: () => ipcRenderer.invoke('exportMemory'),
  listScreenshots: () => ipcRenderer.invoke('listScreenshots'),
  getScreenshot: (obsId) => ipcRenderer.invoke('getScreenshot', obsId),
  getPromptTemplates: () => ipcRenderer.invoke('getPromptTemplates'),
  savePromptTemplate: (template) => ipcRenderer.invoke('savePromptTemplate', template),
  deletePromptTemplate: (id) => ipcRenderer.invoke('deletePromptTemplate', id),
  getNotifPrefs: () => ipcRenderer.invoke('getNotifPrefs'),
  setNotifPrefs: (prefs) => ipcRenderer.invoke('setNotifPrefs', prefs),
  notesSend: (text) => ipcRenderer.invoke('notesSend', text),
  notesRead: () => ipcRenderer.invoke('notesRead'),
  notesOpenNote: () => ipcRenderer.invoke('notesOpenNote'),
  onWatchChunk: (callback) => {
    ipcRenderer.on('watch-chunk', (_event, data) => callback(data));
  },
  onWatchStatus: (callback) => {
    ipcRenderer.on('watch-status', (_event, data) => callback(data));
  },
});
