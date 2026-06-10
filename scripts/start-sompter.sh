#!/bin/bash
# Sompter AI Launcher
# Usage: bash scripts/start-sompter.sh
# Or: npm run start:app

set -e

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND_PORT=8787
OPENCODE_PORT=4096
VENV_DIR="$PROJECT_DIR/.venv"

echo "=== Sompter AI Launcher ==="
echo "Project: $PROJECT_DIR"
echo ""

# Check if backend is already running
if curl -sf http://localhost:$BACKEND_PORT/api/health > /dev/null 2>&1; then
  echo "✅ Backend already running on :$BACKEND_PORT"
else
  echo "🚀 Starting backend..."
  cd "$PROJECT_DIR"
  "$VENV_DIR/bin/uvicorn" backend.server:app --reload --port "$BACKEND_PORT" > /tmp/sompter-backend.log 2>&1 &
  BACKEND_PID=$!
  sleep 3
  if curl -sf http://localhost:$BACKEND_PORT/api/health > /dev/null 2>&1; then
    echo "✅ Backend started (PID: $BACKEND_PID)"
  else
    echo "❌ Backend failed to start. Check /tmp/sompter-backend.log"
    exit 1
  fi
fi

# Check if OpenCode serve is already running
if curl -sf http://localhost:$OPENCODE_PORT/global/health > /dev/null 2>&1; then
  echo "✅ OpenCode serve already running on :$OPENCODE_PORT"
else
  echo "🚀 Starting OpenCode serve..."
  opencode serve --port "$OPENCODE_PORT" > /tmp/sompter-opencode.log 2>&1 &
  OPENCODE_PID=$!
  sleep 3
  if curl -sf http://localhost:$OPENCODE_PORT/global/health > /dev/null 2>&1; then
    echo "✅ OpenCode serve started (PID: $OPENCODE_PID)"
  else
    echo "⚠️  OpenCode serve may not have started. Check /tmp/sompter-opencode.log"
    echo "   (Fallback prompt saving will still work)"
  fi
fi

# Check if Ollama is running
if curl -sf http://localhost:11434/api/tags > /dev/null 2>&1; then
  echo "✅ Ollama is running"
else
  echo "⚠️  Ollama is not running. Screen AI features may not work."
  echo "   Start it: ollama serve"
fi

# Check Playwright availability for browser control mode
echo ""
if "$PROJECT_DIR/.venv/bin/python3" -c "import playwright" 2>/dev/null; then
  echo "✅ Playwright is available (browser control mode ready)"
else
  echo "⚠️  Playwright not found. Install: pip install playwright && python -m playwright install chromium"
fi

# Start Electron
echo "🚀 Starting Electron sidebar..."
cd "$PROJECT_DIR"
npx electron app/main.js > /tmp/sompter-electron.log 2>&1 &
ELECTRON_PID=$!
echo "✅ Electron started (PID: $ELECTRON_PID)"
echo ""
echo "=== Sompter AI is running ==="
echo "  Backend:  http://localhost:$BACKEND_PORT"
echo "  OpenCode: http://localhost:$OPENCODE_PORT"
echo "  Health:   http://localhost:$BACKEND_PORT/api/health"
echo ""
echo "Press Ctrl+C to stop all processes."
echo "Or run: npm run stop"

# Wait for any process to exit
trap "echo 'Stopping...'" INT TERM
wait
