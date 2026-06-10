#!/bin/bash
# Sompter AI Bundled Launcher
# Called by the packaged .app to start backend services
# Arguments: <project_root> <resources_path>

set -e

PROJECT_ROOT="$1"
RESOURCES_PATH="$2"
BACKEND_PORT=8787
OPENCODE_PORT=4096

# Find project .venv or system Python3
VENV_PYTHON=""
if [ -f "$PROJECT_ROOT/.venv/bin/python3" ]; then
  VENV_PYTHON="$PROJECT_ROOT/.venv/bin/python3"
elif [ -f "$PROJECT_ROOT/.venv/bin/python" ]; then
  VENV_PYTHON="$PROJECT_ROOT/.venv/bin/python"
elif command -v python3 &>/dev/null; then
  VENV_PYTHON="$(command -v python3)"
fi

if [ -z "$VENV_PYTHON" ]; then
  echo '{"success":false,"message":"No Python found"}'
  exit 1
fi

# Start backend if not already running
if ! curl -sf http://localhost:$BACKEND_PORT/api/health > /dev/null 2>&1; then
  BACKEND_DIR="$RESOURCES_PATH/backend"
  if [ ! -d "$BACKEND_DIR" ]; then
    BACKEND_DIR="$PROJECT_ROOT/backend"
  fi

  cd "$PROJECT_ROOT"
  "$VENV_PYTHON" -m uvicorn backend.server:app --port "$BACKEND_PORT" > /tmp/sompter-backend.log 2>&1 &
  BACKEND_PID=$!
  sleep 3
  if curl -sf http://localhost:$BACKEND_PORT/api/health > /dev/null 2>&1; then
    echo "Backend started (PID: $BACKEND_PID)"
  else
    echo "Backend may not have started. Check /tmp/sompter-backend.log"
  fi
fi

# Ensure Playwright is available for browser control mode
if "$VENV_PYTHON" -c "import playwright" 2>/dev/null; then
  echo "Playwright is available"
else
  echo "Playwright not found — browser control mode will not work."
  echo "To install: run inside the project: pip install playwright && python -m playwright install chromium"
fi

# Start OpenCode serve if not already running
if ! curl -sf http://localhost:$OPENCODE_PORT/global/health > /dev/null 2>&1; then
  if command -v opencode &>/dev/null; then
    opencode serve --port "$OPENCODE_PORT" > /tmp/sompter-opencode.log 2>&1 &
    echo "OpenCode serve started"
  else
    echo "opencode not found in PATH"
  fi
fi
