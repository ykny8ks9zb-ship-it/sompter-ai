# Sompter AI

A floating always-on-top macOS sidebar AI assistant that screenshots your screen and answers questions about it.

## Daily Run

```bash
cd /Users/charliekrason/Documents/desk/untitled\ folder/sompter-ai
npm run dev
```

Or use the launcher script:

```bash
npm run start:app
```

Or use the packaged app (if built):

```bash
npm run package:mac
open dist/Sompter\ AI-*.dmg
# Then drag Sompter AI.app to /Applications
# Open from Finder, Spotlight, or Dock
```

## Build & Package

Sompter AI can be packaged as a standalone macOS `.app` bundle using `electron-builder`.

### Prerequisites

Before packaging, ensure you have:
- The project fully set up (`.venv`, `npm install`, `.env`)
- Python 3 installed with all backend dependencies
- `opencode` CLI available in PATH

### Package Scripts

| Command | Description |
|---------|-------------|
| `npm run package:mac` | Build `.app` + `.dmg` installer |
| `npm run dist:mac` | Build `.app` only (no DMG) |
| `npm run open:app` | Open the built DMG installer |

### Build the .app

```bash
npm run package:mac
```

The `.app` and `.dmg` appear in `dist/`:
```
dist/
  Sompter AI-1.0.0-arm64.dmg
  mac/
    Sompter AI.app
```

### Install

1. Open the `.dmg` (or run `npm run open:app`)
2. Drag **Sompter AI.app** to **/Applications**
3. Launch from Finder, Spotlight, or Dock

Alternatively, run the `.app` directly from `dist/mac/`:
```bash
npm run dist:mac
open dist/mac/Sompter\ AI.app
```

### What the Packaged App Does

On launch, the `.app`:
- Starts the Electron sidebar UI with the tray menu bar icon
- Automatically starts the FastAPI backend (`uvicorn backend.server:app`)
- Automatically starts `opencode serve`
- All existing features work: screen AI, control, smart fix, diagnostics,
  service controls, provider settings, and notifications

Service startup logs are written to `/tmp/sompter-backend.log` and
`/tmp/sompter-opencode.log`.

### Permissions for the Packaged App

The packaged `.app` is a **different process** from Terminal or `npx electron`.
You may need to grant Screen Recording and Accessibility permissions
separately for `Sompter AI.app`:

1. Open **System Settings → Privacy & Security**
2. **Screen Recording** → Add `Sompter AI.app` and enable it
3. **Accessibility** → Add `Sompter AI.app` and enable it
4. Restart the app after granting permissions

### Login Items

To auto-launch on login:

1. Open **System Settings → General → Login Items**
2. Click **+** and select `Sompter AI.app` from `/Applications`
3. The app will start on next login

### Troubleshooting

- **"Backend offline" in menu bar** — The backend may not have started.
  Check `/tmp/sompter-backend.log`. Run `npm run start:app` from the project
  directory as a fallback.
- **"opencode not found"** — Install or update: `npm install -g opencode`
- **.app shows briefly then quits** — Check Console.app for crash logs.
  Ensure `.venv` and Python dependencies are installed.
- **Permission issues** — Grant Screen Recording + Accessibility to
  `Sompter AI.app` separately (see above).

### Fallback: Run without Packaging

If the packaged app has issues, always fall back to the dev launcher:

```bash
npm run start:app
```

This runs everything from Terminal with full logs visible.

## Setup Checklist

1. Run `npm run start:app`
2. Click **⚙ Setup** in the sidebar
3. If **Screen Recording** shows red, click "Screen Recording" button and grant permission
4. If **Accessibility** shows red, click "Accessibility" button and grant permission
5. Confirm all indicators turn green
6. Click **Test Screenshot** to verify screen capture works
7. Click **Test Control** to verify mouse reading works
8. Click **Test OpenCode** to verify OpenCode connectivity
9. Set a project path via **Fix Project with OpenCode** > Save
10. Confirm footer shows green dots for Backend/Ollama/OpenCode

## Package Scripts

| Command | Description |
|---------|-------------|
| `npm run dev` | Start all services in dev mode (concurrently) |
| `npm run start:app` | Launch script with health checks per service |
| `npm run stop` | Stop all Sompter processes |
| `npm run health` | Check backend/Ollama/OpenCode status |

## Permissions Required

- **Screen Recording** — needed for screenshot capture (macOS will prompt on first use)
- **Accessibility** — needed for keyboard shortcuts and window management

> **Note for packaged app:** If using `Sompter AI.app`, you may need to grant
> permissions separately. See the Packaging section above.

## Troubleshooting

### Port 4096 in use
```bash
lsof -i :4096
kill <PID>
# Or: npm run stop
```

### Port 8787 in use
```bash
lsof -i :8787
kill <PID>
# Or: npm run stop
```

### Ollama not running
```bash
ollama serve
```

### OpenCode session not found
The app falls back to saving the prompt to `.sompter/opencode-prompt.txt`. Open OpenCode desktop and run the prompt manually.

### Screen Recording permission denied
Go to System Settings > Privacy & Security > Screen Recording and enable Terminal/Electron.

## Global Shortcut

Press **Cmd+Shift+A** to toggle the sidebar between collapsed and expanded mode.

## Quick Start

```bash
source .venv/bin/activate
npm run dev
```

## AI Providers

You can choose which AI provider to use from the **🧠 Models** panel in the sidebar.

### Modes

| Mode | Behavior |
|------|----------|
| **Auto** (default) | Tries Ollama first, falls back to Gemini, then OpenAI |
| **Ollama only** | Uses only local Ollama. Free, private, no API key needed. |
| **Gemini only** | Uses only Google Gemini. Free tier available with API key. |
| **OpenAI only** | Uses only OpenAI. Requires API credits (separate from ChatGPT Plus). |

### Provider Setup

**Ollama (free, local, no API key)**
```bash
brew install ollama
ollama serve                    # start the server
ollama pull gemma3:12b         # pull a vision model (default)
```
Ollama runs 100% locally — no data leaves your machine.

**Google Gemini (free tier)**
1. Get a key at https://aistudio.google.com/apikey
2. Open the **🧠 Models** panel
3. Enter your key in the Gemini Key field
4. Click **Save Settings**
5. Set mode to **Gemini only** or stay on **Auto**

**OpenAI (paid)**
1. Get a key at https://platform.openai.com/api-keys
2. Open the **🧠 Models** panel
3. Enter your key in the OpenAI Key field
4. Click **Save Settings**
5. Set mode to **OpenAI only** or stay on **Auto**

> **Note:** ChatGPT Plus subscription does **not** include API credits. You need a separate OpenAI API account with billing enabled.

### Key Safety

- API keys are stored **only** in `.env` — never in localStorage, history, or diagnostics
- Keys are always masked in the UI (e.g. `sk-...abcd`)
- The Diagnostics report never includes full keys
- Changing providers takes effect immediately after saving

### Changing Models

Each provider has a text field for the model name:
- **Ollama:** `gemma3:12b`, `llama3.2:3b`, `llama3.2-vision:11b`, etc.
- **Gemini:** `gemini-2.0-flash`, `gemini-1.5-pro`, `gemini-1.5-flash`, etc.
- **OpenAI:** `gpt-4o-mini`, `gpt-4o`, `gpt-4-turbo`, etc.
- **OpenCode:** `llama3.2:3b` (tool-calling model — change only if you know the new model supports tool calling)

### Provider Status

The footer shows the current mode (Auto, Ollama, Gemini, OpenAI) and the active provider. The **🧠 Models** panel shows live health status for each provider and lets you test connectivity.

### Troubleshooting

- **"Selected provider is offline"** — Ensure Ollama is running (`ollama serve`) or the API key is correct
- **"Key is masked"** — Full keys are never shown after saving; click **Open .env** to verify or edit directly
- **Model not found** — Check the model name is installed (Ollama: `ollama list`) or available in the provider's API

## Custom Prompt Buttons

Click **✏️ Prompts** to edit, add, or delete preset buttons. Each preset has:
- **Label** — Button text
- **Mode** — Screen (screenshot + AI), Chat (just ask), Control Mac, or Fix Project with OpenCode
- **Prompt** — What the AI should do

Presets are saved in localStorage and persist across app restarts. Click **↺ Reset** to restore defaults.

Built-in presets:
- **Fix screen** — Analyze screen for bugs/errors
- **Explain screen** — Describe what's on screen
- **Use web** — Identify web-related content
- **Fix code** — Review visible code
- **Control Mac** — Plan and execute actions
- **Fix Project with OpenCode** — Run OpenCode project edits

## Conversation History

Click **🕘 History** to view recent conversations. Each entry shows:
- Mode icon (screen, chat, control, opencode)
- Time since the interaction
- Prompt and response (truncated)
- **Copy** — Copy the full prompt and response to clipboard
- **Re-run** — Run the same prompt again

History is saved locally in localStorage (up to 50 entries). Click **🗑 Clear** to erase all history. Secrets (API keys, passwords) are masked before saving.

## Project Profiles

Save and quickly switch between coding projects. In the **Fix Project with OpenCode** section:

- **Project dropdown** — Select a saved project profile
- **Choose** — Open macOS folder picker dialog (automatically offers to save as profile)
- **Save As** — Save current path as a named profile
- **✕** — Delete the selected profile
- **★** — First profile saved becomes default; reorder by saving

On launch, the last-used or default project is automatically loaded. Profiles persist in localStorage.

## Smart Fix

Click **Smart Fix** (green button) to run a one-click fix flow:
1. Takes a screenshot of your current screen
2. AI analyzes the visible error/problem
3. Builds a contextual OpenCode task with the screen context + project profile
4. Runs OpenCode against your selected project
5. Shows Review Changes / Show Diff / Run Tests

Requires a saved project profile. Works with or without an active OpenCode serve instance (falls back to prompt file saving).

## Architecture

- `app/` — Electron frontend (main, renderer, HTML, CSS)
- `backend/` — FastAPI Python server

## Menu Bar App

Sompter AI lives in your macOS menu bar for quick access.

### Menu Bar Icon

- A purple **S** icon appears in the menu bar when Sompter is running
- **Click** the icon to toggle the sidebar show/hide
- **Right-click** for the context menu

### Context Menu

| Menu Item | Action |
|-----------|--------|
| **Show/Hide Sidebar** | Toggle the floating sidebar |
| **Smart Fix** | Opens sidebar and runs Smart Fix (requires a project profile) |
| **Open Setup** | Opens the Setup panel |
| **Open Services** | Opens the Service Controls panel |
| **Open Diagnostics** | Opens the Diagnostics panel |
| **Backend ● / Ollama ● / OpenCode ●** | Live status readout (disabled) |
| **Restart Services** | Confirms and restarts all services |
| **Quit Sompter AI** | Exits the app entirely |

### Status Tooltip

Hover over the menu bar icon to see live status:
```
Sompter AI — Backend: OK | Ollama: OK | OpenCode: OFF
```

Status refreshes every 10 seconds.

### Global Shortcut

**Cmd+Shift+A** toggles the sidebar between collapsed and expanded mode — always works, even when the window is hidden.

### Native Notifications

Sompter sends macOS native notifications for:
- **Smart Fix** — When a Smart Fix run completes or fails
- **OpenCode** — When an OpenCode run completes
- **Diagnostics** — When a diagnostics report is saved
- **Service offline** — When Backend, Ollama, or OpenCode goes down

Notifications are rate-limited to avoid spam (30s cooldown per service).

### Dock Icon

By default, Sompter hides from the Dock (accessible only from the menu bar and **Cmd+Shift+A**). To show in the Dock:

1. Click **⚙ Setup**
2. Check **Show in Dock**
3. The app will appear in your Dock immediately

You can also disable notifications from the Setup panel.

### Quitting

- **Menu bar > Right-click > Quit Sompter AI**
- Or run `npm run stop` from the terminal

## Architecture
