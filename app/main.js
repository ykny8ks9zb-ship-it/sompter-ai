const { app, BrowserWindow, ipcMain, screen, globalShortcut, dialog } = require('electron');
const path = require('path');
const { execFile } = require('child_process');
const fs = require('fs');

let mainWindow;
let isSnapping = false;

const COLLAPSED_W = 56;
const COLLAPSED_H = 120;
const EXPANDED_W = 380;
const EXPANDED_H = 680;

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

app.whenReady().then(createWindow);

app.on('will-quit', () => {
  globalShortcut.unregisterAll();
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
    return { success: false, runs: [], message: `Error: ${err.message}` };
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

