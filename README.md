# Sompter AI

A floating always-on-top macOS sidebar AI assistant that screenshots your screen and answers questions about it.

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
