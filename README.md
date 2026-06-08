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

## Making a Clickable Mac App

To launch Sompter AI like a normal Mac app:

1. Open **Automator** (from Applications)
2. Choose **Application** type
3. Add **Run Shell Script** action
4. Enter:
   ```bash
   cd /Users/charliekrason/Documents/desk/untitled\ folder/sompter-ai && npm run start:app
   ```
5. File > Save as **Sompter AI.app**
6. (Optional) Add to **Login Items** in System Settings > General > Login Items

## Permissions Required

## Permissions Required

- **Screen Recording** — needed for screenshot capture (macOS will prompt on first use)
- **Accessibility** — needed for keyboard shortcuts and window management

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

The backend auto-detects which provider to use (tried in order):

### 1. Ollama (free, local, no API key)
```bash
brew install ollama
ollama serve                    # start the server
ollama pull gemma3:12b         # pull a vision model
```

### 2. Google Gemini (free tier)
Get a key at https://aistudio.google.com/apikey, then set in `.env`:
```
GEMINI_API_KEY=your_key_here
```

### 3. OpenAI (paid)
Set in `.env`:
```
OPENAI_API_KEY=sk-...
```

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

## Architecture

- `app/` — Electron frontend (main, renderer, HTML, CSS)
- `backend/` — FastAPI Python server
