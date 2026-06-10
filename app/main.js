const { app, BrowserWindow, ipcMain, screen, globalShortcut, dialog, Tray, Menu, Notification, nativeImage } = require('electron');
const path = require('path');
const { execFile, spawn } = require('child_process');
const fs = require('fs');
const os = require('os');

let mainWindow;
let isSnapping = false;
let tray = null;
let trayPollInterval = null;

const TRAY_ICON_B64 = 'iVBORw0KGgoAAAANSUhEUgAAABYAAAAWCAYAAADEtGw7AAAAfklEQVR4nGNgGHagO+abMhDHAXE1FIPYypQY6ADEO4D4Pw4MknMg1dBqPAai42paGEqc4VDvk2ooDOMOFgJhSgjvwGWoMgWGwjBmaoEmI0oNjqNWpBGORFoaTLOgoE3k0Sy5QQ2mTQahIBJpUl4QZyhasFC32ESzgLoF/aAFAMRAv8cGk8OxAAAAAElFTkSuQmCC';

const COLLAPSED_W = 56;
const COLLAPSED_H = 120;
const EXPANDED_W = 380;
const EXPANDED_H = 680;

// ---- Menu bar / Notification helpers ----

function getMenuBarPrefs() {
  const prefsPath = path.join(app.getPath('userData'), 'sompter-menu-bar-prefs.json');
  try {
    return JSON.parse(fs.readFileSync(prefsPath, 'utf-8'));
  } catch {
    return { enableNotifications: true, showInDock: false };
  }
}

function saveMenuBarPrefs(prefs) {
  const prefsPath = path.join(app.getPath('userData'), 'sompter-menu-bar-prefs.json');
  fs.mkdirSync(path.dirname(prefsPath), { recursive: true });
  fs.writeFileSync(prefsPath, JSON.stringify(prefs, null, 2));
}

function sendNotification(title, body) {
  try {
    const prefs = getMenuBarPrefs();
    if (!prefs.enableNotifications) return;
  } catch { return; }
  if (Notification.isSupported()) {
    const n = new Notification({ title, body });
    n.show();
  }
}

function createTrayIcon() {
  const img = nativeImage.createFromBuffer(Buffer.from(TRAY_ICON_B64, 'base64'));
  if (process.platform === 'darwin') img.setTemplateImage(true);
  return img;
}

function toggleSidebar() {
  if (!mainWindow) return;
  if (mainWindow.isVisible()) {
    mainWindow.hide();
  } else {
    mainWindow.show();
  }
}

function updateTrayMenu(health) {
  const isVisible = mainWindow && mainWindow.isVisible();
  const h = health || { backend: false, ollama: false, opencode: false };
  const backendOk = h.backend ? '●' : '○';
  const ollamaOk = h.ollama ? '●' : '○';
  const opencodeOk = h.opencode ? '●' : '○';
  const contextMenu = Menu.buildFromTemplate([
    {
      label: isVisible ? 'Hide Sidebar' : 'Show Sidebar',
      click: toggleSidebar,
    },
    { type: 'separator' },
    {
      label: 'Smart Fix',
      click: () => {
        if (mainWindow) {
          mainWindow.show();
          mainWindow.webContents.send('menu-action', 'smartfix');
        }
      },
    },
    {
      label: 'Open Setup',
      click: () => {
        if (mainWindow) {
          mainWindow.show();
          mainWindow.webContents.send('menu-action', 'setup');
        }
      },
    },
    {
      label: 'Open Services',
      click: () => {
        if (mainWindow) {
          mainWindow.show();
          mainWindow.webContents.send('menu-action', 'services');
        }
      },
    },
    {
      label: 'Open Diagnostics',
      click: () => {
        if (mainWindow) {
          mainWindow.show();
          mainWindow.webContents.send('menu-action', 'diagnostics');
        }
      },
    },
    {
      label: 'About Sompter AI',
      click: () => {
        if (mainWindow) {
          mainWindow.show();
          mainWindow.webContents.send('menu-action', 'about');
        }
      },
    },
    { type: 'separator' },
    { label: `Backend ${backendOk}   Ollama ${ollamaOk}   OpenCode ${opencodeOk}`, enabled: false },
    { type: 'separator' },
    {
      label: 'Restart Services',
      click: () => {
        if (mainWindow) {
          mainWindow.show();
          mainWindow.webContents.send('menu-action', 'restart-services');
        }
      },
    },
    { type: 'separator' },
    { label: 'Quit Sompter AI', click: () => { app.quit(); } },
  ]);
  tray.setContextMenu(contextMenu);
}

async function fetchHealth() {
  try {
    const r = await fetch('http://localhost:8787/api/health');
    return await r.json();
  } catch {
    return { backend: false, ollama: false, opencode: false };
  }
}

function updateTrayTooltip(health) {
  const h = health || { backend: false, ollama: false, opencode: false };
  const parts = [];
  parts.push(h.backend ? 'Backend: OK' : 'Backend: OFF');
  parts.push(h.ollama ? 'Ollama: OK' : 'Ollama: OFF');
  parts.push(h.opencode ? 'OpenCode: OK' : 'OpenCode: OFF');
  tray.setToolTip('Sompter AI — ' + parts.join(' | '));
}

async function refreshTrayState() {
  const health = await fetchHealth();
  updateTrayMenu(health);
  updateTrayTooltip(health);
  checkAndNotifyServiceChange(health);
}

function startTrayPolling() {
  if (trayPollInterval) clearInterval(trayPollInterval);
  refreshTrayState();
  trayPollInterval = setInterval(refreshTrayState, 10000);
}

function createTray() {
  tray = new Tray(createTrayIcon());
  tray.setToolTip('Sompter AI — Starting...');
  updateTrayMenu({ backend: false, ollama: false, opencode: false });
  tray.on('click', toggleSidebar);
  startTrayPolling();
}

// Previous notification states to avoid spam
let prevNotifStates = {};

function checkAndNotifyServiceChange(health) {
  const prefs = getMenuBarPrefs();
  if (!prefs.enableNotifications) return;
  const now = Date.now();
  const cooldown = 30000; // 30s between same notification

  ['backend', 'ollama', 'opencode'].forEach(svc => {
    const key = svc + '_offline';
    const wasOff = prevNotifStates[key] || false;
    const isOff = !health[svc];
    if (isOff && !wasOff) {
      const last = prevNotifStates[key + '_last'] || 0;
      if (now - last > cooldown) {
        sendNotification('Sompter AI', `${svc.charAt(0).toUpperCase() + svc.slice(1)} went offline`);
        prevNotifStates[key + '_last'] = now;
      }
    }
    prevNotifStates[key] = isOff;
  });
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: EXPANDED_W,
    height: EXPANDED_H,
    alwaysOnTop: true,
    resizable: false,
    frame: false,
    transparent: true,
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'renderer.js'),
      nodeIntegration: false,
      contextIsolation: true,
    },
  });

  mainWindow.loadFile(path.join(__dirname, 'index.html'));

  globalShortcut.register('CommandOrControl+Shift+A', () => {
    mainWindow.webContents.send('global-toggle');
  });

  mainWindow.on('move', () => {
    if (isSnapping) return;
    isSnapping = true;
    const [x, y] = mainWindow.getPosition();
    const { width: sw } = screen.getPrimaryDisplay().workAreaSize;
    const [w] = mainWindow.getSize();
    const sx = sw - w;
    if (x !== sx) {
      mainWindow.setPosition(sx, y);
    }
    // Send Y position to renderer so it can save to localStorage
    if (mainWindow.webContents && !mainWindow.webContents.isDestroyed()) {
      mainWindow.webContents.send('y-position-changed', y);
    }
    setTimeout(() => { isSnapping = false; }, 50);
  });
}

app.whenReady().then(async () => {
  const SAFE_MODE = process.argv.includes('--safe-mode') || process.env.SAFE_MODE === '1';

  // Detect project root directory for service launching
  const projectRoot = getProjectRoot();
  if (app.isPackaged && !SAFE_MODE) {
    console.log('Packaged mode — starting services...');
    startBundledServices(projectRoot);
  }

  createWindow();

  if (!SAFE_MODE) {
    createTray();
  }

  const prefs = getMenuBarPrefs();
  if (!prefs.showInDock) {
    if (app.dock && app.dock.hide) app.dock.hide();
  }
});

function getProjectRoot() {
  if (app.isPackaged) {
    // In packaged mode, the project root is the parent of the .app bundle
    const appPath = app.getAppPath();
    // app.getAppPath() returns .../Sompter AI.app/Contents/Resources/app
    // Project root is 5 levels up: Resources/app -> Resources -> Contents -> .app -> parent
    const candidate = path.resolve(appPath, '..', '..', '..', '..', '..');
    if (fs.existsSync(path.join(candidate, 'package.json'))) return candidate;
    // Try user's home/Documents/desk/untitled folder/sompter-ai as fallback
    const devPath = path.join(os.homedir(), 'Documents', 'desk', 'untitled folder', 'sompter-ai');
    if (fs.existsSync(path.join(devPath, 'package.json'))) return devPath;
    return candidate;
  }
  // Dev mode: dirname of __dirname (app/) is project root
  return path.resolve(__dirname, '..');
}

function startBundledServices(projectRoot) {
  const resourcesPath = process.resourcesPath;
  const scriptPath = path.join(resourcesPath, 'scripts', 'start-sompter-bundled.sh');

  // First try the bundled script
  if (fs.existsSync(scriptPath)) {
    const { spawn } = require('child_process');
    const proc = spawn('bash', [scriptPath, projectRoot, resourcesPath], {
      stdio: ['ignore', 'pipe', 'pipe'],
      detached: true,
    });
    let output = '';
    proc.stdout.on('data', d => { output += d.toString(); });
    proc.stderr.on('data', d => { output += d.toString(); });
    proc.on('close', code => {
      console.log('Bundled services script exited:', code);
      if (output) console.log('Output:', output.slice(0, 500));
    });
    return;
  }

  // Fallback: try the dev script path
  const devScript = path.join(projectRoot, 'scripts', 'start-sompter-bundled.sh');
  if (fs.existsSync(devScript)) {
    const { spawn } = require('child_process');
    const proc = spawn('bash', [devScript, projectRoot, resourcesPath], {
      stdio: ['ignore', 'pipe', 'pipe'],
      detached: true,
    });
    proc.on('close', code => {
      console.log('Dev services script exited:', code);
    });
  }
}

app.on('will-quit', () => {
  globalShortcut.unregisterAll();
  if (trayPollInterval) clearInterval(trayPollInterval);
  if (tray) tray.destroy();
  // Stop Playwright browser if running
  try {
    fetch(`${BACKEND}/api/browser/stop`, { method: 'POST' }).catch(() => {});
  } catch {}
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

// ---- Window state IPC ----

ipcMain.handle('initWindow', (_event, collapsed, y) => {
  const w = collapsed ? COLLAPSED_W : EXPANDED_W;
  const h = collapsed ? COLLAPSED_H : EXPANDED_H;
  const { width: sw } = screen.getPrimaryDisplay().workAreaSize;
  mainWindow.setSize(w, h);
  mainWindow.setPosition(sw - w, y || 80);
  mainWindow.show();
});

ipcMain.handle('collapseWindow', (_event, y) => {
  const { width: sw } = screen.getPrimaryDisplay().workAreaSize;
  mainWindow.setSize(COLLAPSED_W, COLLAPSED_H);
  mainWindow.setPosition(sw - COLLAPSED_W, y || 80);
  mainWindow.webContents.send('window-state-changed', true);
});

ipcMain.handle('expandWindow', (_event, y) => {
  const { width: sw } = screen.getPrimaryDisplay().workAreaSize;
  mainWindow.setSize(EXPANDED_W, EXPANDED_H);
  mainWindow.setPosition(sw - EXPANDED_W, y || 80);
  mainWindow.webContents.send('window-state-changed', false);
});

// ---- App IPC ----

const BACKEND = 'http://localhost:8787';

ipcMain.handle('getHealth', async () => {
  try {
    const r = await fetch(`${BACKEND}/api/health`);
    return await r.json();
  } catch {
    return { backend: false, ollama: false, opencode: false, provider: 'none' };
  }
});

ipcMain.handle('getSetupStatus', async () => {
  try {
    const r = await fetch(`${BACKEND}/api/setup/status`);
    return await r.json();
  } catch {
    return { screen_recording: false, accessibility: false, backend: false, ollama: false, opencode: false };
  }
});

ipcMain.handle('testScreenshot', async () => {
  try {
    const r = await fetch(`${BACKEND}/api/setup/test_screenshot`, { method: 'POST' });
    return await r.json();
  } catch (err) {
    return { success: false, message: `Error: ${err.message}` };
  }
});

ipcMain.handle('testControl', async () => {
  try {
    const r = await fetch(`${BACKEND}/api/setup/test_control`, { method: 'POST' });
    return await r.json();
  } catch (err) {
    return { success: false, message: `Error: ${err.message}` };
  }
});

ipcMain.handle('testOpencode', async () => {
  try {
    const r = await fetch(`${BACKEND}/api/setup/test_opencode`, { method: 'POST' });
    return await r.json();
  } catch (err) {
    return { success: false, message: `Error: ${err.message}` };
  }
});

ipcMain.handle('smartFix', async (_event, { projectPath, projectName, userPrompt }) => {
  try {
    const base64 = await takeScreenshot();
    const response = await fetch(`${BACKEND}/api/smartfix/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_path: projectPath, project_name: projectName, screenshot_base64: base64, user_prompt: userPrompt }),
    });
    return await response.json();
  } catch (err) {
    return { success: false, screen_summary: '', opencode_result: { success: false, output: `Error: ${err.message}` } };
  }
});

async function takeScreenshot() {
  const p = `/tmp/sompter_screenshot_${Date.now()}.png`;
  await new Promise((resolve, reject) => {
    execFile('screencapture', ['-x', p], (err) => (err ? reject(err) : resolve()));
  });
  const buf = fs.readFileSync(p);
  const b64 = buf.toString('base64');
  fs.unlinkSync(p);
  return b64;
}

ipcMain.handle('ask', async (_event, { prompt }) => {
  try {
    const base64 = await takeScreenshot();
    const response = await fetch(`${BACKEND}/api/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt, screenshot: base64 }),
    });
    const data = await response.json();
    return { success: true, message: data.message };
  } catch (err) {
    return { success: false, message: `Error: ${err.message}` };
  }
});

ipcMain.handle('controlPlan', async () => {
  try {
    const base64 = await takeScreenshot();
    const response = await fetch(`${BACKEND}/api/control/plan`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ screenshot: base64 }),
    });
    const data = await response.json();
    return data;
  } catch (err) {
    return { success: false, message: `Error: ${err.message}` };
  }
});

ipcMain.handle('opencodeRun', async (_event, { projectPath, task }) => {
  try {
    const base64 = await takeScreenshot();
    const response = await fetch(`${BACKEND}/api/opencode/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_path: projectPath, task, screenshot_base64: base64 }),
    });
    const data = await response.json();
    return data;
  } catch (err) {
    return { success: false, output: `Error: ${err.message}` };
  }
});

ipcMain.handle('projectStatus', async (_event, { projectPath }) => {
  try {
    const response = await fetch(`${BACKEND}/api/project/status?project_path=${encodeURIComponent(projectPath)}`);
    return await response.json();
  } catch (err) {
    return { success: false, message: `Error: ${err.message}` };
  }
});

ipcMain.handle('projectDiff', async (_event, { projectPath }) => {
  try {
    const response = await fetch(`${BACKEND}/api/project/diff?project_path=${encodeURIComponent(projectPath)}`);
    return await response.json();
  } catch (err) {
    return { success: false, message: `Error: ${err.message}` };
  }
});

ipcMain.handle('runTest', async (_event, { projectPath, command }) => {
  try {
    const response = await fetch(`${BACKEND}/api/project/test`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_path: projectPath, command }),
    });
    return await response.json();
  } catch (err) {
    return { success: false, message: `Error: ${err.message}` };
  }
});

ipcMain.handle('openFolder', async (_event, { projectPath }) => {
  try {
    await new Promise((resolve, reject) => {
      execFile('open', [projectPath], (err) => (err ? reject(err) : resolve()));
    });
    return { success: true, message: 'Opened' };
  } catch (err) {
    return { success: false, message: `Error: ${err.message}` };
  }
});

ipcMain.handle('openFile', async (_event, { filePath }) => {
  try {
    await new Promise((resolve, reject) => {
      execFile('open', [filePath], (err) => (err ? reject(err) : resolve()));
    });
    return { success: true, message: 'Opened' };
  } catch (err) {
    return { success: false, message: `Error: ${err.message}` };
  }
});

ipcMain.handle('openREADME', async () => {
  const projectRoot = getProjectRoot();
  const readmePath = path.join(projectRoot, 'README.md');
  try {
    await new Promise((resolve, reject) => {
      execFile('open', [readmePath], (err) => (err ? reject(err) : resolve()));
    });
    return { success: true, message: 'Opened' };
  } catch (err) {
    return { success: false, message: `Error: ${err.message}` };
  }
});

ipcMain.handle('openPrivacySettings', async (_event, pane) => {
  const url = `x-apple.systempreferences:com.apple.preference.security?${pane}`;
  try {
    await new Promise((resolve, reject) => {
      execFile('open', [url], (err) => (err ? reject(err) : resolve()));
    });
    return { success: true };
  } catch (err) {
    return { success: false, message: `Error: ${err.message}` };
  }
});

ipcMain.handle('runAction', async (_event, { action, params }) => {
  try {
    const response = await fetch(`${BACKEND}/api/action/${action}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params),
    });
    const data = await response.json();
    return data;
  } catch (err) {
    return { success: false, message: `Error: ${err.message}` };
  }
});

ipcMain.handle('runBrowserAction', async (_event, { action, params }) => {
  const BROWSER_ENDPOINTS = {
    browser_click: '/api/browser/click',
    browser_type: '/api/browser/type',
    browser_navigate: '/api/browser/navigate',
    browser_evaluate: '/api/browser/evaluate',
    browser_screenshot: '/api/browser/screenshot',
  };
  const endpoint = BROWSER_ENDPOINTS[action];
  if (!endpoint) return { success: false, message: `Unknown browser action: ${action}` };
  try {
    const response = await fetch(`${BACKEND}${endpoint}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params),
    });
    const data = await response.json();
    return data;
  } catch (err) {
    return { success: false, message: `Error: ${err.message}` };
  }
});

ipcMain.handle('selectFolder', async () => {
  const result = await dialog.showOpenDialog({ properties: ['openDirectory'] });
  if (result.canceled || result.filePaths.length === 0) {
    return { success: false, canceled: true };
  }
  return { success: true, path: result.filePaths[0] };
});

ipcMain.handle('runsList', async (_event, { projectPath }) => {
  try {
    const r = await fetch(`${BACKEND}/api/runs/list?project_path=${encodeURIComponent(projectPath)}`);
    return await r.json();
  } catch (err) {
    return { success: false, message: `Error: ${err.message}` };
  }
});

ipcMain.handle('getDiagnostics', async (_event, { projectPath }) => {
  try {
    const url = projectPath
      ? `${BACKEND}/api/diagnostics?project_path=${encodeURIComponent(projectPath)}`
      : `${BACKEND}/api/diagnostics`;
    const r = await fetch(url);
    return await r.json();
  } catch (err) {
    return { success: false, error: `Error: ${err.message}` };
  }
});

ipcMain.handle('saveDiagnosticsReport', async (_event, { projectPath, data }) => {
  try {
    const ts = new Date().toISOString().replace(/[:.]/g, '-');
    const reportDir = projectPath
      ? path.join(projectPath, '.sompter', 'diagnostics')
      : path.join(__dirname, '..', '.sompter', 'diagnostics');
    fs.mkdirSync(reportDir, { recursive: true });
    const reportPath = path.join(reportDir, `diagnostic-${ts}.json`);
    fs.writeFileSync(reportPath, JSON.stringify(data, null, 2), 'utf-8');
    return { success: true, path: reportPath };
  } catch (err) {
    return { success: false, error: `Error: ${err.message}` };
  }
});

ipcMain.handle('runsDetail', async (_event, { projectPath, runId }) => {
  try {
    const r = await fetch(`${BACKEND}/api/runs/detail?project_path=${encodeURIComponent(projectPath)}&run_id=${encodeURIComponent(runId)}`);
    return await r.json();
  } catch (err) {
    return { success: false, message: `Error: ${err.message}` };
  }
});

ipcMain.handle('runsUndo', async (_event, { projectPath, runId }) => {
  try {
    const r = await fetch(`${BACKEND}/api/runs/undo`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_path: projectPath, run_id: runId }),
    });
    return await r.json();
  } catch (err) {
    return { success: false, message: `Error: ${err.message}` };
  }
});

ipcMain.handle('getSettings', async () => {
  try {
    const r = await fetch(`${BACKEND}/api/settings`);
    return await r.json();
  } catch (err) {
    return { success: false, error: `Error: ${err.message}` };
  }
});

ipcMain.handle('saveSettings', async (_event, data) => {
  try {
    const r = await fetch(`${BACKEND}/api/settings`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    return await r.json();
  } catch (err) {
    return { success: false, message: `Error: ${err.message}` };
  }
});

ipcMain.handle('testProvider', async (_event, { provider }) => {
  try {
    const r = await fetch(`${BACKEND}/api/settings/test_provider`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ provider }),
    });
    return await r.json();
  } catch (err) {
    return { success: false, message: `Error: ${err.message}` };
  }
});

ipcMain.handle('openEnvFile', async () => {
  try {
    const envPath = path.join(__dirname, '..', '.env');
    if (fs.existsSync(envPath)) {
      const { shell } = require('electron');
      shell.openPath(envPath);
      return { success: true };
    }
    return { success: false, message: '.env not found' };
  } catch (err) {
    return { success: false, message: `Error: ${err.message}` };
  }
});

ipcMain.handle('getServiceStatus', async () => {
  try {
    const r = await fetch(`${BACKEND}/api/services/status`);
    return await r.json();
  } catch (err) {
    return { success: false, error: `Error: ${err.message}` };
  }
});

ipcMain.handle('runServiceAction', async (_event, { action }) => {
  try {
    const r = await fetch(`${BACKEND}/api/services/${action}`, { method: 'POST' });
    return await r.json();
  } catch (err) {
    return { success: false, message: `Error: ${err.message}` };
  }
});

ipcMain.handle('openLogsFolder', async () => {
  try {
    const logsDir = path.join(__dirname, '..', '.sompter', 'logs');
    fs.mkdirSync(logsDir, { recursive: true });
    const { shell } = require('electron');
    shell.openPath(logsDir);
    return { success: true };
  } catch (err) {
    return { success: false, message: `Error: ${err.message}` };
  }
});

// ---- Menu Bar / Dock / Notification IPC ----

ipcMain.handle('getMenuBarPrefs', async () => {
  return getMenuBarPrefs();
});

ipcMain.handle('saveMenuBarPrefs', async (_event, prefs) => {
  saveMenuBarPrefs(prefs);
  if (prefs.hasOwnProperty('showInDock')) {
    if (prefs.showInDock) {
      if (app.dock && app.dock.show) app.dock.show();
    } else {
      if (app.dock && app.dock.hide) app.dock.hide();
    }
  }
  return { success: true };
});

ipcMain.handle('showNotification', async (_event, { title, body }) => {
  sendNotification(title, body);
  return { success: true };
});

ipcMain.handle('appReset', async () => {
  try {
    const r = await fetch(`${BACKEND}/api/app/reset`, { method: 'POST' });
    return await r.json();
  } catch (err) {
    return { success: false, message: `Error: ${err.message}` };
  }
});

ipcMain.handle('appExportLogs', async () => {
  try {
    const r = await fetch(`${BACKEND}/api/app/export_logs`, { method: 'POST' });
    const data = await r.json();
    if (data.success) {
      // Save to desktop
      const desktop = app.getPath('desktop');
      const filePath = path.join(desktop, data.filename);
      const buf = Buffer.from(data.data, 'base64');
      fs.writeFileSync(filePath, buf);
      return { success: true, path: filePath };
    }
    return data;
  } catch (err) {
    return { success: false, message: `Error: ${err.message}` };
  }
});

ipcMain.handle('appSafeMode', async () => {
  return { success: true, safe_mode: process.argv.includes('--safe-mode') || process.env.SAFE_MODE === '1' };
});

// ---- About / Version IPC ----

ipcMain.handle('getAboutInfo', async () => {
  try {
    const r = await fetch(`${BACKEND}/api/about`);
    const data = await r.json();
    data.build_type = app.isPackaged ? 'packaged' : 'dev';
    return data;
  } catch (err) {
    // Fallback: read version and commit locally
    let version = '1.0.0';
    let commit = 'unknown';
    try {
      const pkgPath = path.join(__dirname, '..', 'package.json');
      const pkg = JSON.parse(fs.readFileSync(pkgPath, 'utf-8'));
      version = pkg.version || version;
    } catch { }
    try {
      const result = require('child_process').execFileSync('git', ['rev-parse', '--short', 'HEAD'], { cwd: path.join(__dirname, '..'), timeout: 3000, encoding: 'utf-8' });
      commit = result.trim();
    } catch { }
    return {
      app_name: 'Sompter AI',
      version,
      commit,
      build_type: app.isPackaged ? 'packaged' : 'dev',
      provider_mode: 'unknown',
      ollama_available: false,
      opencode_available: false,
      ollama_model: '-',
      gemini_model: '-',
      openai_model: '-',
      backend_url: 'http://localhost:8787',
      opencode_port: 4096,
      release_notes: [
        { title: 'Browser Control Mode (Playwright)', step: '35' },
        { title: 'About / Release Notes Panel', step: '34' },
        { title: 'Final Release Test', step: '33' },
        { title: 'First-Run Onboarding', step: '32' },
        { title: 'macOS App Packaging', step: '31' },
        { title: 'Menu Bar App + Notifications', step: '30' },
        { title: 'Service Controls / Restart Panel', step: '29' },
        { title: 'Provider + Model Settings', step: '28' },
        { title: 'Diagnostics / Bug Report Export', step: '27' },
        { title: 'AI Run Snapshots + Undo Safety', step: '26' },
        { title: 'Smart Fix Flow', step: '25' },
        { title: 'Project Profiles / Quick Switch', step: '24' },
        { title: 'Conversation History', step: '23' },
        { title: 'Custom Prompt Buttons', step: '22' },
        { title: 'Setup Permissions Checker', step: '21' },
        { title: 'Mac App Launcher', step: '20' },
      ],
    };
  }
});

