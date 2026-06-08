# Sompter AI

A floating always-on-top macOS sidebar AI assistant that screenshots your screen and answers questions about it.

## Daily Run

```bash
cd /Users/charliekrason/Documents/desk/untitled\ folder/sompter-ai
npm run dev
```

## Permissions Required

- **Screen Recording** — needed for screenshot capture (macOS will prompt on first use)
- **Accessibility** — needed for keyboard shortcuts and window management

## Troubleshooting

### Port 4096 in use
```bash
lsof -i :4096
kill <PID>
```

### Port 8787 in use
```bash
lsof -i :8787
kill <PID>
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

## Preset Buttons

- **Fix screen** — Analyze screen for bugs/errors
- **Explain screen** — Describe what's on screen
- **Use web** — Identify web-related content
- **Fix code** — Review visible code

## Architecture

- `app/` — Electron frontend (main, renderer, HTML, CSS)
- `backend/` — FastAPI Python server
