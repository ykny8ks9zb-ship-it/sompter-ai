const { app, BrowserWindow, ipcMain, screen, globalShortcut, dialog, Tray, Menu, Notification, nativeImage, shell } = require('electron');
const path = require('path');
const { execFile, execFileSync, spawn } = require('child_process');
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
    {
      label: watchModeActive ? '⏸ Pause Watch Mode' : '▶ Start Watch Mode',
      click: () => {
        if (mainWindow) {
          mainWindow.show();
          mainWindow.webContents.send('menu-action', watchModeActive ? 'stop-watch' : 'start-watch');
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

ipcMain.handle('startBackend', async () => {
  try {
    const r = await fetch(`${BACKEND}/api/health`, { timeout: 2000 });
    if (r.ok) return { success: true, message: 'Backend already running' };
  } catch {}
  try {
    const projectRoot = getProjectRoot();
    const venvPython = path.join(projectRoot, '.venv', 'bin', 'python3');
    const proc = spawn(venvPython, ['-m', 'uvicorn', 'backend.server:app', '--port', '8787'], {
      cwd: projectRoot,
      stdio: ['ignore', 'pipe', 'pipe'],
      detached: true,
    });
    proc.unref();
    return { success: true, message: 'Backend starting...' };
  } catch (err) {
    return { success: false, message: `Error: ${err.message}` };
  }
});

ipcMain.handle('checkOllamaModels', async () => {
  try {
    const r = await fetch('http://localhost:11434/api/tags', { timeout: 3000 });
    const data = await r.json();
    const models = (data.models || []).map(m => m.name);
    return {
      success: true,
      models,
      has_gemma: models.some(m => m.startsWith('gemma3:12b')),
      has_moondream: models.some(m => m.startsWith('moondream')),
    };
  } catch (err) {
    return { success: false, models: [], has_gemma: false, has_moondream: false, error: err.message };
  }
});

ipcMain.handle('pullOllamaModel', async (_event, model) => {
  try {
    const r = await fetch('http://localhost:11434/api/pull', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: model, stream: false }),
      timeout: 600000,
    });
    return r.ok ? { success: true } : { success: false, message: `HTTP ${r.status}` };
  } catch (err) {
    return { success: false, message: err.message };
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

ipcMain.handle('startAskStream', async (_event, { prompt }) => {
  try {
    const base64 = await takeScreenshot();
    const response = await fetch(`${BACKEND}/api/chat/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt, screenshot: base64 }),
    });
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';
      for (const line of lines) {
        if (line.startsWith('data: ')) {
          try {
            const data = JSON.parse(line.slice(6));
            if (mainWindow && !mainWindow.isDestroyed()) {
              mainWindow.webContents.send('chat-chunk', data);
            }
          } catch {}
        }
      }
    }
    // Process any remaining data
    if (buffer.startsWith('data: ')) {
      try {
        const data = JSON.parse(buffer.slice(6));
        if (mainWindow && !mainWindow.isDestroyed()) {
          mainWindow.webContents.send('chat-chunk', data);
        }
      } catch {}
    }
  } catch (err) {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send('chat-chunk', { chunk: `\n\nError: ${err.message}`, done: true });
    }
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

ipcMain.handle('openExternalURL', async (_event, url) => {
  shell.openExternal(url);
});

ipcMain.handle('writeOnboardingState', async (_event, val) => {
  const statePath = path.join(getProjectRoot(), '.sompter', '.onboarding-done');
  if (val) {
    fs.writeFileSync(statePath, 'done', 'utf-8');
  } else {
    try { fs.unlinkSync(statePath); } catch {}
  }
});

ipcMain.handle('checkOnboardingState', async () => {
  const statePath = path.join(getProjectRoot(), '.sompter', '.onboarding-done');
  return { done: fs.existsSync(statePath) };
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
    browser_start: '/api/browser/start',
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

ipcMain.handle('checkPathExists', async (_event, { path: checkPath }) => {
  try {
    const exists = fs.existsSync(checkPath);
    const isDir = exists ? fs.statSync(checkPath).isDirectory() : false;
    return { success: true, exists, isDirectory: isDir };
  } catch (err) {
    return { success: false, exists: false, isDirectory: false, error: err.message };
  }
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

ipcMain.handle('listScreenshots', async () => {
  const dbPath = path.join(__dirname, '..', '.sompter', 'memory.db');
  const ssDir = path.join(__dirname, '..', '.sompter', 'screenshots');
  try {
    if (!fs.existsSync(dbPath)) return [];
    const tmpScript = path.join(app.getPath('temp'), `sompter-ss-list-${Date.now()}.py`);
    const pyScript = `import json, sqlite3, sys, os
db = sys.argv[1]
ss_dir = sys.argv[2]
con = sqlite3.connect(db)
con.row_factory = sqlite3.Row
rows = con.execute("SELECT s.id, s.observation_id, s.filename, s.timestamp, s.active_app, o.notes_message FROM screenshots s LEFT JOIN observations o ON s.observation_id = o.id ORDER BY s.id DESC LIMIT 50").fetchall()
con.close()
result = []
for r in rows:
    d = dict(r)
    fpath = os.path.join(ss_dir, d["filename"])
    if os.path.exists(fpath):
        d["file_exists"] = True
    else:
        d["file_exists"] = False
    result.append(d)
print(json.dumps(result))
`;
    fs.writeFileSync(tmpScript, pyScript, 'utf-8');
    const r = execFileSync(path.join(__dirname, '..', '.venv', 'bin', 'python3'), [tmpScript, dbPath, ssDir], { timeout: 10000, maxBuffer: 1024 * 1024 });
    try { fs.unlinkSync(tmpScript); } catch {}
    return JSON.parse(r.toString().trim());
  } catch (err) {
    try { fs.unlinkSync(tmpScript); } catch {}
    return { error: err.message };
  }
});

ipcMain.handle('getScreenshot', async (_event, obsId) => {
  const ssDir = path.join(__dirname, '..', '.sompter', 'screenshots');
  try {
    const files = fs.readdirSync(ssDir).filter(f => f.startsWith(`obs_${obsId}.`) || f === `obs_${obsId}.jpg`);
    if (files.length === 0) return null;
    const img = fs.readFileSync(path.join(ssDir, files[0]));
    return img.toString('base64');
  } catch { return null; }
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

ipcMain.handle('quitApp', async () => {
  app.quit();
});

// ---- Watch Mode ----

let watchModeInterval = null;
let watchModeActive = false;

function notesCreateNote() {
  return new Promise((resolve, reject) => {
    const script = `
      try
        tell application "Notes"
          activate
          set noteExists to false
          repeat with n in every note
            if name of n is "Sompter Chat" then
              set noteExists to true
              exit repeat
            end if
          end repeat
          if not noteExists then
            make new note at folder "Notes" with properties {name:"Sompter Chat", body:"Sompter Chat (watch mode)"}
          end if
        end tell
      end try
    `;
    execFile('osascript', ['-e', script], (err) => {
      if (err) reject(err);
      else resolve();
    });
  });
}

function notesAppend(text) {
  return new Promise((resolve, reject) => {
    // Write text to a temp file to avoid AppleScript string escaping issues
    const tmpFile = path.join(os.tmpdir(), 'sompter-notes-' + Date.now() + '.txt');
    fs.writeFileSync(tmpFile, '\n' + text, 'utf-8');
    const script = `
      try
        tell application "Notes"
          set n to first note whose name is "Sompter Chat"
          set f to (POSIX file "${tmpFile.replace(/"/g, '\\"')}")
          set fileContent to (read f) as string
          set body of n to (body of n) & fileContent
        end tell
      end try
    `;
    execFile('osascript', ['-e', script], (err) => {
      fs.unlink(tmpFile, () => {});
      if (err) reject(err);
      else resolve();
    });
  });
}

function notesReadLatest() {
  return new Promise((resolve, reject) => {
    const tmpFile = path.join(os.tmpdir(), 'sompter-notes-read-' + Date.now() + '.txt');
    const script = `
      try
        tell application "Notes"
          set n to first note whose name is "Sompter Chat"
          set noteBody to body of n
          set f to (POSIX file "${tmpFile.replace(/"/g, '\\"')}")
          set fileRef to open for access f with write permission
          write noteBody to fileRef as text
          close access fileRef
        end tell
      end try
    `;
    execFile('osascript', ['-e', script], { maxBuffer: 1024 * 1024 }, (err) => {
      if (err) {
        reject(err);
        return;
      }
      let body = '';
      try {
        body = fs.readFileSync(tmpFile, 'utf-8').trim();
      } catch {}
      fs.unlink(tmpFile, () => {});
      const lines = body.split('\n');
      // Filter out assistant lines, keep user-written lines (last 3)
      const userMessages = lines
        .filter(l => l.trim() && !l.match(/^🤖\s*Sompter:/) && !l.match(/^Sompter Chat/))
        .slice(-3);
      resolve(userMessages);
    });
  });
}

ipcMain.handle('startWatchMode', async () => {
  if (watchModeActive) return { success: true, message: 'Already running' };
  try {
    await notesCreateNote();
    watchModeActive = true;
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send('watch-status', { active: true });
    }
    watchModeInterval = setInterval(async () => {
      try {
        const tmpFile = path.join(os.tmpdir(), 'sompter-watch-' + Date.now() + '.png');
        await new Promise((res, rej) => {
          execFile('screencapture', ['-x', '-t', 'png', tmpFile], (err) => {
            if (err) rej(err); else res();
          });
        });
        const resizedFile = path.join(os.tmpdir(), 'sompter-watch-resized-' + Date.now() + '.png');
        try {
          await new Promise((res, rej) => {
            execFile('sips', ['-Z', '800', tmpFile, '--out', resizedFile], (err) => {
              if (err) rej(err); else res();
            });
          });
        } catch { fs.copyFileSync(tmpFile, resizedFile); }
        const buf = fs.readFileSync(resizedFile);
        fs.unlink(tmpFile, () => {});
        fs.unlink(resizedFile, () => {});
        const b64 = buf.toString('base64');
        let activeApp = '';
        try {
          const appResult = await new Promise((res, rej) => {
            execFile('osascript', ['-e', 'tell application "System Events" to get name of first process whose frontmost is true'], (err, stdout) => {
              if (err) rej(err); else res(stdout.trim());
            });
          });
          activeApp = appResult;
        } catch {}
        let notesMsg = '';
        try {
          const msgs = await notesReadLatest();
          if (msgs.length > 0) notesMsg = msgs.join('\n');
        } catch {}
        const r = await fetch(`${BACKEND}/api/watch/analyze-screen`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ screenshot_b64: b64, active_app: activeApp, notes_message: notesMsg, search_web: true }),
        });
        const data = await r.json();
        if (data.reply && mainWindow && !mainWindow.isDestroyed()) {
          mainWindow.webContents.send('watch-chunk', { reply: data.reply, timestamp: Date.now() });
          try {
            await notesAppend('\n🤖 Sompter: ' + data.reply);
          } catch {}
        }
      } catch (err) {
        if (mainWindow && !mainWindow.isDestroyed()) {
          mainWindow.webContents.send('watch-chunk', { reply: `Watch error: ${err.message}`, timestamp: Date.now() });
        }
      }
    }, 5000);
    return { success: true, message: 'Watch mode started' };
  } catch (err) {
    return { success: false, message: err.message };
  }
});

ipcMain.handle('stopWatchMode', async () => {
  watchModeActive = false;
  if (watchModeInterval) {
    clearInterval(watchModeInterval);
    watchModeInterval = null;
  }
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send('watch-status', { active: false });
  }
  return { success: true, message: 'Watch mode stopped' };
});

ipcMain.handle('getWatchStatus', async () => {
  return { active: watchModeActive };
});

ipcMain.handle('getDaemonStatus', async () => {
  const statusPath = path.join(__dirname, '..', '.sompter', 'daemon-status.json');
  try {
    const data = JSON.parse(fs.readFileSync(statusPath, 'utf-8'));
    let stale = true;
    if (data.last_heartbeat) {
      const hb = new Date(data.last_heartbeat).getTime();
      stale = (Date.now() - hb) > 120000;
    }
    data.stale = stale;
    if (stale && data.status !== 'stopped') data.status = 'stale';
    return data;
  } catch {
    return { status: 'stopped', pid: 0, cycle: 0, observation_count: 0, stale: true };
  }
});

const DAEMON_SCRIPT = path.join(__dirname, '..', 'scripts', 'manage-daemon.sh');

ipcMain.handle('startDaemon', async () => {
  try {
    const { execSync } = require('child_process');
    execSync(`bash "${DAEMON_SCRIPT}" start`, { timeout: 10000 });
    return { success: true };
  } catch (err) {
    return { success: false, message: err.message };
  }
});

ipcMain.handle('stopDaemon', async () => {
  try {
    const { execSync } = require('child_process');
    execSync(`bash "${DAEMON_SCRIPT}" stop`, { timeout: 10000 });
    return { success: true };
  } catch (err) {
    return { success: false, message: err.message };
  }
});

ipcMain.handle('restartDaemon', async () => {
  try {
    const { execSync } = require('child_process');
    execSync(`bash "${DAEMON_SCRIPT}" restart`, { timeout: 15000 });
    return { success: true };
  } catch (err) {
    return { success: false, message: err.message };
  }
});

ipcMain.handle('notesSend', async (_event, text) => {
  try {
    await notesAppend(text);
    return { success: true };
  } catch (err) {
    return { success: false, message: err.message };
  }
});

ipcMain.handle('notesRead', async () => {
  try {
    const msgs = await notesReadLatest();
    return { success: true, messages: msgs };
  } catch (err) {
    return { success: false, message: err.message, messages: [] };
  }
});

ipcMain.handle('notesOpenNote', async () => {
  try {
    // Try to activate Notes app using AppleScript
    execFile('osascript', ['-e', 'tell application "Notes" to activate']);
    return { success: true };
  } catch (err) {
    try {
      // Fallback: try to open a notes URL
      execFile('open', ['x-apple-notes://']);
      return { success: true };
    } catch (err2) {
      return { success: false, message: err2.message };
    }
  }
});

// ---- Memory / Learning IPC ----

ipcMain.handle('getMemoryData', async () => {
  const memDir = path.join(__dirname, '..', '.sompter');
  const dbPath = path.join(memDir, 'memory.db');
  const settingsPath = path.join(memDir, 'settings.json');
  try {
    let data = { observations: [], summaries: [], patterns: [], stats: { observations: 0, summaries: 0, patterns: 0 }, interests: [] };
    try {
      const s = JSON.parse(fs.readFileSync(settingsPath, 'utf-8'));
      data.interests = s.tracked_interests || [];
    } catch {}
    const tmpScript = path.join(app.getPath('temp'), `sompter-memory-${Date.now()}.py`);
    const pyScript = `import json,sqlite3,os,sys
db = sys.argv[1]
con = sqlite3.connect(db)
con.row_factory = sqlite3.Row
o = [dict(r) for r in con.execute("SELECT id,timestamp,active_app,substr(notes_message,1,80)as m,substr(ai_reply,1,100)as r FROM observations ORDER BY id DESC LIMIT 15")]
s = [dict(r) for r in con.execute("SELECT date,substr(summary,1,200)as summary FROM daily_summaries ORDER BY date DESC LIMIT 5")]
st = {"observations":con.execute("SELECT COUNT(*)FROM observations").fetchone()[0],"summaries":con.execute("SELECT COUNT(*)FROM daily_summaries").fetchone()[0]}
e = [dict(r) for r in con.execute("SELECT name,type,mentions,substr(last_seen,1,10)as last_seen FROM entities ORDER BY mentions DESC LIMIT 15")]
rels = [dict(r) for r in con.execute("SELECT e1.name AS entity1,e2.name AS entity2,r.strength FROM relationships r JOIN entities e1 ON r.entity1_id=e1.id JOIN entities e2 ON r.entity2_id=e2.id ORDER BY r.strength DESC LIMIT 10")]
con.close()
print(json.dumps({"observations":o,"summaries":s,"stats":st,"entities":e,"relationships":rels}))
`;
    fs.writeFileSync(tmpScript, pyScript, 'utf-8');
    const r = execFileSync(path.join(__dirname, '..', '.venv', 'bin', 'python3'), [tmpScript, dbPath], { timeout: 5000, maxBuffer: 1024 * 1024 });
    const result = JSON.parse(r.toString().trim());
    data.observations = result.observations || [];
    data.summaries = result.summaries || [];
    data.stats = result.stats || {};
    try { fs.unlinkSync(tmpScript); } catch {}
    return data;
  } catch (err) {
    return { observations: [], summaries: [], patterns: [], stats: { observations: 0, summaries: 0, patterns: 0 }, interests: [] };
  }
});

ipcMain.handle('searchMemory', async (_event, query) => {
  const memDir = path.join(__dirname, '..', '.sompter');
  const dbPath = path.join(memDir, 'memory.db');
  if (!query || !query.trim()) return { observations: [], entities: [], summaries: [] };
  const q = query.trim().replace(/'/g, "''");
  const tmpScript = path.join(app.getPath('temp'), `sompter-search-${Date.now()}.py`);
  const pyScript = `import json,sqlite3,sys
db = sys.argv[1]
q = sys.argv[2]
con = sqlite3.connect(db)
con.row_factory = sqlite3.Row
o = [dict(r) for r in con.execute("SELECT timestamp,active_app,substr(notes_message,1,120)as msg,substr(ai_reply,1,150)as reply FROM observations WHERE notes_message LIKE ? OR ai_reply LIKE ? ORDER BY id DESC LIMIT 20", (f"%{q}%", f"%{q}%"))]
e = [dict(r) for r in con.execute("SELECT name,type,mentions FROM entities WHERE name LIKE ? ORDER BY mentions DESC LIMIT 10", (f"%{q}%",))]
s = [dict(r) for r in con.execute("SELECT date,substr(summary,1,200)as summary FROM daily_summaries WHERE summary LIKE ? ORDER BY date DESC LIMIT 5", (f"%{q}%",))]
con.close()
print(json.dumps({"observations":o,"entities":e,"summaries":s}))
`;
  try {
    fs.writeFileSync(tmpScript, pyScript, 'utf-8');
    const r = execFileSync(path.join(__dirname, '..', '.venv', 'bin', 'python3'), [tmpScript, dbPath, q], { timeout: 5000, maxBuffer: 1024 * 1024 });
    const result = JSON.parse(r.toString().trim());
    try { fs.unlinkSync(tmpScript); } catch {}
    return result;
  } catch (err) {
    try { fs.unlinkSync(tmpScript); } catch {}
    return { observations: [], entities: [], summaries: [] };
  }
});

ipcMain.handle('exportMemory', async () => {
  const memDir = path.join(__dirname, '..', '.sompter');
  const dbPath = path.join(memDir, 'memory.db');
  const result = await dialog.showSaveDialog(mainWindow, {
    title: 'Export Memory Data',
    defaultPath: path.join(os.homedir(), `sompter-memory-export-${new Date().toISOString().slice(0,10)}.json`),
    filters: [{ name: 'JSON', extensions: ['json'] }],
  });
  if (result.canceled || !result.filePath) return { success: false, message: 'Cancelled' };
  const tmpScript = path.join(app.getPath('temp'), `sompter-export-${Date.now()}.py`);
  const pyScript = `import json, sqlite3, sys
db = sys.argv[1]
out = sys.argv[2]
con = sqlite3.connect(db)
con.row_factory = sqlite3.Row
data = {
  "observations": [dict(r) for r in con.execute("SELECT * FROM observations ORDER BY id")],
  "daily_summaries": [dict(r) for r in con.execute("SELECT * FROM daily_summaries ORDER BY date")],
  "entities": [dict(r) for r in con.execute("SELECT * FROM entities ORDER BY mentions DESC")],
  "relationships": [dict(r) for r in con.execute("SELECT e1.name AS entity1, e2.name AS entity2, r.relationship_type, r.strength FROM relationships r JOIN entities e1 ON r.entity1_id = e1.id JOIN entities e2 ON r.entity2_id = e2.id ORDER BY r.strength DESC")],
}
con.close()
with open(out, "w") as f:
  json.dump(data, f, indent=2)
print(f"Exported {len(data['observations'])} observations, {len(data['entities'])} entities, {len(data['relationships'])} relationships")
`;
  try {
    fs.writeFileSync(tmpScript, pyScript, 'utf-8');
    const r = execFileSync(path.join(__dirname, '..', '.venv', 'bin', 'python3'), [tmpScript, dbPath, result.filePath], { timeout: 10000, maxBuffer: 1024 * 1024 });
    try { fs.unlinkSync(tmpScript); } catch {}
    return { success: true, path: result.filePath, message: r.toString().trim() };
  } catch (err) {
    try { fs.unlinkSync(tmpScript); } catch {}
    return { success: false, message: err.message };
  }
});

// ---- Notification Preferences IPC ----

function getSettingsJson() {
  const settingsPath = path.join(__dirname, '..', '.sompter', 'settings.json');
  try {
    return JSON.parse(fs.readFileSync(settingsPath, 'utf-8'));
  } catch { return {}; }
}

function saveSettingsJson(data) {
  const settingsPath = path.join(__dirname, '..', '.sompter', 'settings.json');
  const existing = getSettingsJson();
  Object.assign(existing, data);
  fs.writeFileSync(settingsPath, JSON.stringify(existing, null, 2));
}

ipcMain.handle('getNotifPrefs', async () => {
  const s = getSettingsJson();
  const n = s.notifications || {};
  return {
    proactive: n.proactive !== false,
    user_questions: n.user_questions !== false,
    keywords: n.keywords || ['storm', 'outage', 'crash', 'error', 'fire', 'earthquake', 'tornado', 'warning'],
  };
});

ipcMain.handle('setNotifPrefs', async (_event, prefs) => {
  try {
    saveSettingsJson({
      notifications: {
        proactive: prefs.proactive !== false,
        user_questions: prefs.user_questions !== false,
        keywords: Array.isArray(prefs.keywords) ? prefs.keywords : ['storm', 'outage', 'crash', 'error', 'fire', 'earthquake', 'tornado', 'warning'],
      },
    });
    return { success: true };
  } catch (e) {
    return { success: false, message: e.message };
  }
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
          { title: 'Watch Mode + Apple Notes Chat', step: '36' },
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

