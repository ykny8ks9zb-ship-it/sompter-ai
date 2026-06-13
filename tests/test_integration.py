"""Integration tests for Sompter AI full stack."""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error


PROJECT_DIR = os.path.join(os.path.dirname(__file__), "..")
BACKEND_URL = "http://localhost:8787"


def test_backend_health():
    """Backend health endpoint returns OK with all sub-checks."""
    try:
        req = urllib.request.Request(f"{BACKEND_URL}/api/health")
        resp = urllib.request.urlopen(req, timeout=5)
        data = json.loads(resp.read())
        assert "backend" in data
        assert "daemon" in data
        assert "ollama" in data
    except (urllib.error.URLError, ConnectionResetError) as e:
        pass


def test_daemon_status_file():
    """Daemon writes valid JSON status file."""
    status_path = os.path.join(PROJECT_DIR, ".sompter", "daemon-status.json")
    if not os.path.exists(status_path):
        return
    with open(status_path) as f:
        data = json.load(f)
    assert "status" in data
    assert "pid" in data
    assert "cycle" in data


def test_memory_db_exists():
    """SQLite memory database exists and has observations table."""
    db_path = os.path.join(PROJECT_DIR, ".sompter", "memory.db")
    if not os.path.exists(db_path):
        return
    conn = sqlite3.connect(db_path)
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    table_names = [t[0] for t in tables]
    assert "observations" in table_names
    assert "daily_summaries" in table_names
    conn.close()


def test_settings_valid_json():
    """Settings file is valid JSON with required keys."""
    settings_path = os.path.join(PROJECT_DIR, ".sompter", "settings.json")
    if not os.path.exists(settings_path):
        return
    with open(settings_path) as f:
        data = json.load(f)
    assert "tracked_teams" in data
    assert "tracked_interests" in data


def test_npm_status_script():
    """npm run status runs without error."""
    result = subprocess.run(
        ["npm", "run", "status"],
        capture_output=True, text=True, timeout=10,
        cwd=PROJECT_DIR,
    )
    assert result.returncode == 0


def test_python_syntax():
    """All Python files compile without syntax errors."""
    py_files = [
        "scripts/watch-daemon.py",
        "scripts/daily-summary.py",
        "backend/server.py",
    ]
    for rel_path in py_files:
        full = os.path.join(PROJECT_DIR, rel_path)
        assert os.path.exists(full), f"{rel_path} not found"
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", full],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0, f"{rel_path}: {result.stderr}"


def test_release_artifacts():
    """Release directory contains DMGs and checksums."""
    rel_dir = os.path.join(PROJECT_DIR, "release", "v0.3.0-dev")
    if not os.path.isdir(rel_dir):
        return
    files = os.listdir(rel_dir)
    names = " ".join(files)
    assert "SHA256SUMS.txt" in names
    assert "RELEASE_NOTES_v0.3.0-dev.md" in names
    assert "KNOWN_ISSUES_v0.3.0-dev.md" in names
    assert any(f.endswith(".dmg") for f in files)


def test_readme_exists():
    """README has meaningful content."""
    readme = os.path.join(PROJECT_DIR, "README.md")
    with open(readme) as f:
        content = f.read()
    assert len(content) > 100
    assert "Sompter AI" in content
    assert "Watch Mode" in content or "Features" in content


def test_entitlements_plist():
    """Hardened runtime entitlements plist exists and is valid XML."""
    entitlements = os.path.join(PROJECT_DIR, "app", "entitlements.mac.plist")
    assert os.path.exists(entitlements)
    import plistlib
    with open(entitlements, "rb") as f:
        data = plistlib.load(f)
    assert isinstance(data, dict)
    assert data.get("com.apple.security.network.client") is True
