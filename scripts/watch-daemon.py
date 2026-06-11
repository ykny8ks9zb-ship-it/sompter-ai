#!/usr/bin/env python3
"""
Sompter AI Watch Daemon — 24/7 background screen observer.

Runs a continuous loop: screencapture → sips resize → read Apple Notes →
call backend /api/watch/analyze-screen → write AI response to Notes.
Designed to run as a launchd agent for always-on operation.
"""

import base64
import html as html_mod
import json
import logging
import os
import re
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

# ── Config (overridable via env vars) ──────────────────────────────────
BACKEND_URL = os.environ.get("SOMPTER_BACKEND_URL", "http://localhost:8787")
INTERVAL = int(os.environ.get("SOMPTER_WATCH_INTERVAL", "10"))
LOG_FILE = os.environ.get("SOMPTER_WATCH_LOG", "/tmp/sompter-watch-daemon.log")
NOTES_NOTE_NAME = os.environ.get("SOMPTER_NOTES_NOTE", "Sompter Chat")
PROJECT_DIR = os.environ.get(
    "SOMPTER_PROJECT_DIR",
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)
MEMORY_DB = os.environ.get("SOMPTER_MEMORY_DB",
                           os.path.join(PROJECT_DIR, ".sompter", "memory.db"))
PID_FILE = "/tmp/sompter-watch-daemon.pid"

# ── Logging ────────────────────────────────────────────────────────────
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("watch-daemon")

running = True
cycle_count = 0


# ── Signal handling ────────────────────────────────────────────────────
def signal_handler(signum, _frame):
    global running
    log.info(f"Received signal {signum}, shutting down...")
    running = False


# ── Helper runners ─────────────────────────────────────────────────────
def run_osascript(script: str, timeout: int = 15) -> str:
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result.stdout.strip()


# ── Screenshot ─────────────────────────────────────────────────────────
def take_screenshot() -> str:
    tmp_file = tempfile.mktemp(suffix=".png", prefix="sompter-watch-")
    resized_file = tempfile.mktemp(suffix=".png", prefix="sompter-watch-")
    try:
        subprocess.run(
            ["screencapture", "-x", "-t", "png", tmp_file],
            check=True,
            timeout=10,
        )
        result = subprocess.run(
            ["sips", "-Z", "800", tmp_file, "--out", resized_file],
            capture_output=True,
            timeout=10,
        )
        if result.returncode != 0:
            import shutil
            shutil.copy2(tmp_file, resized_file)
        with open(resized_file, "rb") as f:
            return base64.b64encode(f.read()).decode("ascii")
    finally:
        for f in [tmp_file, resized_file]:
            try:
                if os.path.exists(f):
                    os.unlink(f)
            except Exception:
                pass


# ── Active app ─────────────────────────────────────────────────────────
def get_active_app() -> str:
    script = (
        'tell application "System Events" '
        "to get name of first process whose frontmost is true"
    )
    return run_osascript(script, timeout=5)


# ── HTML helpers ───────────────────────────────────────────────────────
def strip_html(text: str) -> str:
    clean = re.sub(r"</?(div|br|p|li|tr|h[1-6])[^>]*>", "\n", text)
    clean = re.sub(r"<[^>]+>", "", clean)
    clean = html_mod.unescape(clean)
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
    return clean


def esc_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


# ── Apple Notes helpers ────────────────────────────────────────────────
def notes_ensure_exists():
    script = f"""
        try
            tell application "Notes"
                set noteExists to false
                repeat with n in every note
                    if name of n is "{NOTES_NOTE_NAME}" then
                        set noteExists to true
                        exit repeat
                    end if
                end repeat
                if not noteExists then
                    make new note at folder "Notes" with properties {{name:"{NOTES_NOTE_NAME}", body:"<div>Sompter Chat (watch mode)</div>"}}
                end if
            end tell
        end try
    """
    run_osascript(script, timeout=10)


def notes_read_latest() -> list[str]:
    tmp_file = tempfile.mktemp(suffix=".txt", prefix="sompter-notes-")
    clean = (
        tmp_file.replace("\\", "\\\\").replace('"', '\\"')
    )
    script = f"""
        try
            tell application "Notes"
                set n to first note whose name is "{NOTES_NOTE_NAME}"
                set noteBody to body of n
                set f to (POSIX file "{clean}")
                set fileRef to open for access f with write permission
                write noteBody to fileRef as text
                close access fileRef
            end tell
        end try
    """
    try:
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=15)
        if not os.path.exists(tmp_file):
            return []
        body = Path(tmp_file).read_text("utf-8").strip()
        plain = strip_html(body)
        lines = plain.split("\n")
        user_msgs = [
            l
            for l in lines
            if l.strip()
            and "[Sompter]:" not in l
            and l.strip() != NOTES_NOTE_NAME
        ]
        return user_msgs[-3:]
    finally:
        try:
            if os.path.exists(tmp_file):
                os.unlink(tmp_file)
        except Exception:
            pass


def notes_append(text: str):
    tmp_file = tempfile.mktemp(suffix=".txt", prefix="sompter-notes-")
    clean = (
        tmp_file.replace("\\", "\\\\").replace('"', '\\"')
    )
    safe_text = esc_html(text)
    html = f"<div><br><b>[Sompter]:</b> {safe_text}</div>"
    Path(tmp_file).write_text(html, "utf-8")
    script = f"""
        try
            tell application "Notes"
                set n to first note whose name is "{NOTES_NOTE_NAME}"
                set f to (POSIX file "{clean}")
                set fileContent to (read f) as string
                set body of n to (body of n) & fileContent
            end tell
        end try
    """
    try:
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=15)
    finally:
        try:
            if os.path.exists(tmp_file):
                os.unlink(tmp_file)
        except Exception:
            pass


# ── Backend communication ──────────────────────────────────────────────
def check_backend_health() -> bool:
    try:
        req = urllib.request.Request(f"{BACKEND_URL}/api/health")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("backend", False)
    except Exception:
        return False


def call_backend(
    screenshot_b64: str, active_app: str, notes_msg: str, memory_context: str = "",
) -> str:
    payload = json.dumps({
        "screenshot_b64": screenshot_b64 or "",
        "active_app": active_app or "",
        "notes_message": notes_msg or "",
        "search_web": True,
        "memory_context": memory_context or "",
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{BACKEND_URL}/api/watch/analyze-screen",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("reply", "")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        log.error(f"Backend HTTP {e.code}: {body[:300]}")
        return ""
    except Exception as e:
        log.error(f"Backend call failed: {e}")
        return ""


# ── Memory (SQLite) ────────────────────────────────────────────────────
def init_memory():
    db_dir = os.path.dirname(MEMORY_DB)
    os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(MEMORY_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            active_app TEXT,
            notes_message TEXT,
            screenshot_hash TEXT,
            search_results TEXT,
            ai_reply TEXT,
            summary TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL UNIQUE,
            summary TEXT NOT NULL,
            key_facts TEXT,
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()
    log.info(f"Memory DB initialized at {MEMORY_DB}")


# ── Context injection (memory) ────────────────────────────────────────
def build_context() -> str:
    try:
        conn = sqlite3.connect(MEMORY_DB)
        conn.row_factory = sqlite3.Row

        # Load last 3 daily summaries
        summaries = conn.execute(
            "SELECT * FROM daily_summaries ORDER BY date DESC LIMIT 3"
        ).fetchall()

        # Load last 5 observations from last 24h
        cutoff = (datetime.now() - timedelta(hours=24)).isoformat()
        recent = conn.execute(
            """SELECT timestamp, active_app, notes_message, ai_reply
               FROM observations
               WHERE timestamp >= ?
               ORDER BY timestamp DESC LIMIT 5""",
            (cutoff,),
        ).fetchall()

        conn.close()

        parts = []

        if summaries:
            parts.append("Recent Daily Summaries:")
            for s in summaries:
                date_label = s["date"]
                summary_text = strip_html(s["summary"])[:400]
                parts.append(f"  [{date_label}] {summary_text}")
                if s["key_facts"]:
                    parts.append(f"      Key facts: {s['key_facts'][:300]}")

        if recent:
            if parts:
                parts.append("")
            parts.append("Recent Observations (last 24h):")
            for r in recent:
                ts = r["timestamp"][:16] if r["timestamp"] else "?"
                app = r["active_app"] or "?"
                msg = r["notes_message"] or "(proactive)"
                reply = r["ai_reply"] or ""
                parts.append(f"  [{ts}] App: {app} | User: {msg[:200]}")
                if reply:
                    parts.append(f"      AI: {strip_html(reply)[:200]}")
        context = "\n".join(parts)
        return context
    except Exception as e:
        log.warning(f"Failed to build context: {e}")
        return ""


def save_observation(
    active_app: str,
    notes_msg: str,
    screenshot_b64: str,
    search_context: str,
    ai_reply: str,
):
    try:
        conn = sqlite3.connect(MEMORY_DB)
        conn.execute(
            """INSERT INTO observations
               (timestamp, active_app, notes_message, screenshot_hash, search_results, ai_reply)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                datetime.now().isoformat(),
                active_app or "",
                notes_msg or "",
                str(hash(screenshot_b64[:1000])) if screenshot_b64 else "",
                search_context[:500] if search_context else "",
                ai_reply or "",
            ),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        log.error(f"Failed to save observation: {e}")


# ── Main loop ──────────────────────────────────────────────────────────
def main():
    global running, cycle_count

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    # Write PID file
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))

    log.info("═" * 50)
    log.info("Watch Daemon starting")
    log.info(f"Backend URL: {BACKEND_URL}")
    log.info(f"Interval: {INTERVAL}s")
    log.info(f"PID: {os.getpid()}")
    log.info(f"Project dir: {PROJECT_DIR}")

    # Init memory
    init_memory()

    # Ensure Notes note exists
    try:
        notes_ensure_exists()
        log.info(f"Notes note '{NOTES_NOTE_NAME}' ensured")
    except Exception as e:
        log.error(f"Failed to ensure Notes note: {e}")

    # Wait for backend
    log.info("Waiting for backend...")
    for i in range(60):
        if check_backend_health():
            log.info("Backend is healthy")
            break
        time.sleep(1)
    else:
        log.warning("Backend not available after 60s, continuing anyway...")

    # Main loop
    while running:
        cycle_count += 1
        log.info(f"── Cycle {cycle_count} ──")

        # 1. Screenshot
        screenshot_b64 = ""
        try:
            screenshot_b64 = take_screenshot()
            log.info(
                f"Screenshot: {len(screenshot_b64)} bytes b64"
            )
        except Exception as e:
            log.error(f"Screenshot failed: {e}")

        # 2. Active app
        active_app = ""
        try:
            active_app = get_active_app()
            log.info(f"Active app: {active_app}")
        except Exception as e:
            log.error(f"Active app failed: {e}")

        # 3. Read notes
        notes_msgs: list[str] = []
        try:
            notes_msgs = notes_read_latest()
            if notes_msgs:
                log.info(f"Notes messages: {notes_msgs}")
        except Exception as e:
            log.error(f"Notes read failed: {e}")

        notes_msg = notes_msgs[0] if notes_msgs else ""

        # 4. Build memory context
        context = build_context()
        if context:
            log.info(f"Memory context: {len(context)} chars from daily summaries + recent observations")

        # 5. Call backend
        if not notes_msg and not screenshot_b64:
            log.info("Nothing to analyze, skipping cycle")
        else:
            preview = notes_msg[:120] if notes_msg else "(proactive observation)"
            log.info(f"Calling backend: {preview}...")
            reply = call_backend(screenshot_b64, active_app, notes_msg, context)
            if reply:
                log.info(f"Reply ({len(reply)} chars): {reply[:200]}...")
                try:
                    notes_append(reply)
                    log.info("Reply written to Notes")
                except Exception as e:
                    log.error(f"Notes append failed: {e}")
                save_observation(active_app, notes_msg, screenshot_b64, "", reply)
            else:
                log.warning("No reply from backend")

        # 5. Wait (check every second if we should stop)
        for _ in range(INTERVAL):
            if not running:
                break
            time.sleep(1)

    log.info("Watch daemon stopped")
    try:
        os.unlink(PID_FILE)
    except Exception:
        pass


if __name__ == "__main__":
    main()
