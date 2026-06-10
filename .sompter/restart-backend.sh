#!/bin/bash
sleep 2
pkill -f 'uvicorn backend.server:app' 2>/dev/null
cd /Users/charliekrason/Documents/desk/untitled folder/sompter-ai
exec /Users/charliekrason/Documents/desk/untitled folder/sompter-ai/.venv/bin/python3 -m uvicorn backend.server:app --port 8787 > /tmp/sompter-backend.log 2>&1 &
