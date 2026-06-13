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

import requests
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Lazy import web_search (avoids loading heavy backend/server.py at module level)
_web_search = None
def get_web_search():
    global _web_search
    if _web_search is None:
        try:
            from backend.server import web_search as ws
            _web_search = ws
        except Exception:
            _web_search = False
    return _web_search

# ── Config (overridable via env vars) ──────────────────────────────────
BACKEND_URL = os.environ.get("SOMPTER_BACKEND_URL", "http://localhost:8787")
INTERVAL = int(os.environ.get("SOMPTER_WATCH_INTERVAL", "10"))
LOG_FILE = os.environ.get("SOMPTER_WATCH_LOG", "/tmp/sompter-watch-daemon.log")
NOTES_NOTE_NAME = os.environ.get("SOMPTER_NOTES_NOTE", "Sompter Chat")
NOTIFICATIONS_ENABLED = os.environ.get("SOMPTER_NOTIFICATIONS", "1") == "1"
PROJECT_DIR = os.environ.get(
    "SOMPTER_PROJECT_DIR",
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)
MEMORY_DB = os.environ.get("SOMPTER_MEMORY_DB",
                           os.path.join(PROJECT_DIR, ".sompter", "memory.db"))
PID_FILE = "/tmp/sompter-watch-daemon.pid"
PROACTIVE_THRESHOLD = int(os.environ.get("SOMPTER_PROACTIVE_THRESHOLD", "4"))
STATUS_FILE = os.path.join(PROJECT_DIR, ".sompter", "daemon-status.json")

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
            ["sips", "-Z", "500", tmp_file, "--out", resized_file],
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


# ── Browser tab monitoring ─────────────────────────────────────────────
def app_is_running(name: str) -> bool:
    try:
        result = subprocess.run(
            ["osascript", "-e",
             f'tell application "System Events" to exists (processes where name is "{name}")'],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip() == "true"
    except Exception:
        return False


def get_chrome_tabs(max_tabs: int = 5) -> list[str]:
    if not app_is_running("Google Chrome"):
        return []
    script = f"""
        tell application "Google Chrome"
            set tabList to {{}}
            set windowIndex to 1
            repeat with w in windows
                set tabIndex to 1
                repeat with t in tabs of w
                    if tabIndex > {max_tabs} then exit repeat
                    set end of tabList to (title of t) & " | " & (URL of t)
                    set tabIndex to tabIndex + 1
                end repeat
                set windowIndex to windowIndex + 1
                if windowIndex > 2 then exit repeat
            end repeat
            return tabList
        end tell
    """
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return []
        return [l.strip() for l in result.stdout.strip().split(",") if l.strip()][:max_tabs]
    except Exception:
        return []


def get_safari_tabs(max_tabs: int = 5) -> list[str]:
    if not app_is_running("Safari"):
        return []
    script = f"""
        tell application "Safari"
            set tabList to {{}}
            set windowIndex to 1
            repeat with w in windows
                set tabIndex to 1
                repeat with t in tabs of w
                    if tabIndex > {max_tabs} then exit repeat
                    set end of tabList to (name of t) & " | " & (URL of t)
                    set tabIndex to tabIndex + 1
                end repeat
                set windowIndex to windowIndex + 1
                if windowIndex > 2 then exit repeat
            end repeat
            return tabList
        end tell
    """
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return []
        return [l.strip() for l in result.stdout.strip().split(",") if l.strip()][:max_tabs]
    except Exception:
        return []


def get_browser_tabs() -> str:
    parts = []
    chrome = get_chrome_tabs()
    if chrome:
        parts.append("Chrome tabs:")
        for t in chrome:
            parts.append(f"  {t}")
    safari = get_safari_tabs()
    if safari:
        parts.append("Safari tabs:")
        for t in safari:
            parts.append(f"  {t}")
    return "\n".join(parts)


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
        resp = requests.get(f"{BACKEND_URL}/api/health", timeout=5)
        return resp.json().get("backend", False)
    except Exception:
        return False


def call_backend(
    screenshot_b64: str, active_app: str, notes_msg: str,
    memory_context: str = "", system_override: str = "",
) -> str:
    payload_data: dict = {
        "screenshot_b64": screenshot_b64 or "",
        "active_app": active_app or "",
        "notes_message": notes_msg or "",
        "search_web": not bool(system_override),
        "memory_context": memory_context or "",
    }
    if system_override:
        payload_data["system_prompt"] = system_override

    try:
        resp = requests.post(
            f"{BACKEND_URL}/api/watch/analyze-screen",
            json=payload_data,
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json().get("reply", "")
    except requests.exceptions.Timeout:
        log.error("Backend request timed out (120s)")
        return ""
    except requests.exceptions.ConnectionError as e:
        log.error(f"Backend connection failed: {e}")
        return ""
    except requests.exceptions.RequestException as e:
        log.error(f"Backend request failed: {e}")
        return ""


# ── Notifications ──────────────────────────────────────────────────────
IMPORTANT_KEYWORDS = [
    "storm", "warning", "advisory", "hurricane", "tornado", "flood",
    "earthquake", "wildfire", "evacuation", "shelter", "emergency",
    "score", "win", "loss", "trade", "champion", "playoff",
    "deadline", "meeting", "reminder", "alert", "important", "urgent",
    "error", "crash", "failed", "outage", "breach", "critical",
    "your question", "you asked", "you wanted to know",
]

# Only these trigger notifications for proactive (auto-generated) observations
PROACTIVE_ALERT_KEYWORDS = [
    "storm", "warning", "advisory", "hurricane", "tornado", "flood",
    "earthquake", "wildfire", "evacuation", "shelter", "emergency",
    "deadline", "urgent", "critical", "crash", "outage", "breach",
]


def get_notification_prefs() -> dict:
    settings_path = os.path.join(PROJECT_DIR, ".sompter", "settings.json")
    try:
        with open(settings_path) as f:
            s = json.load(f)
            return s.get("notifications", {})
    except Exception:
        return {}


def should_notify(reply: str, notes_msg: str, proactive: bool = False) -> bool:
    if not NOTIFICATIONS_ENABLED:
        return False
    prefs = get_notification_prefs()
    user_questions_enabled = prefs.get("user_questions", True)
    proactive_enabled = prefs.get("proactive", True)
    custom_keywords = prefs.get("keywords", None)

    # User questions always trigger unless explicitly disabled
    if notes_msg.strip():
        return user_questions_enabled

    kw_list = custom_keywords if custom_keywords else (
        PROACTIVE_ALERT_KEYWORDS if proactive else IMPORTANT_KEYWORDS
    )
    lower = reply.lower()
    has_keyword = any(kw in lower for kw in kw_list)

    if proactive:
        if not proactive_enabled:
            return False
        return has_keyword
    return has_keyword


def send_notification(title: str, body: str, sound: bool = False):
    safe_title = title.replace('"', '\\"').replace("'", "'\\''")
    safe_body = body.replace('"', '\\"').replace("'", "'\\''")[:200]
    script = f'display notification "{safe_body}" with title "{safe_title}"'
    if sound:
        script += ' sound name "default"'
    try:
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=5)
    except Exception as e:
        log.warning(f"Notification failed: {e}")


# ── Memory (SQLite) ────────────────────────────────────────────────────
def init_memory():
    db_dir = os.path.dirname(MEMORY_DB)
    os.makedirs(db_dir, exist_ok=True)
    # Screenshots directory
    ss_dir = os.path.join(db_dir, "screenshots")
    os.makedirs(ss_dir, exist_ok=True)
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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS entities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            type TEXT NOT NULL,
            mentions INTEGER DEFAULT 1,
            first_seen TEXT,
            last_seen TEXT,
            context TEXT DEFAULT ''
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS relationships (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity1_id INTEGER NOT NULL,
            entity2_id INTEGER NOT NULL,
            relationship_type TEXT DEFAULT 'related_to',
            strength INTEGER DEFAULT 1,
            last_seen TEXT,
            FOREIGN KEY (entity1_id) REFERENCES entities(id),
            FOREIGN KEY (entity2_id) REFERENCES entities(id),
            UNIQUE(entity1_id, entity2_id, relationship_type)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS screenshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            observation_id INTEGER,
            filename TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            active_app TEXT,
            FOREIGN KEY (observation_id) REFERENCES observations(id)
        )
    """)
    conn.commit()
    conn.close()
    log.info(f"Memory DB initialized at {MEMORY_DB}")


# ── Pattern detection ──────────────────────────────────────────────────
def detect_patterns() -> list[str]:
    try:
        conn = sqlite3.connect(MEMORY_DB)
        conn.row_factory = sqlite3.Row
        # Get user questions from last 7 days with their hour-of-day
        rows = conn.execute(
            """SELECT notes_message, timestamp
               FROM observations
               WHERE notes_message IS NOT NULL
                 AND notes_message != ''
                 AND timestamp >= ?
               ORDER BY timestamp""",
            ((datetime.now() - timedelta(days=7)).isoformat(),),
        ).fetchall()
        conn.close()

        if len(rows) < 3:
            return []

        # Group similar messages by approximate time pattern
        from collections import defaultdict
        hourly: dict[str, list[int]] = defaultdict(list)
        for r in rows:
            msg = r["notes_message"].strip().lower()[:60]
            if not msg:
                continue
            hour = datetime.fromisoformat(r["timestamp"]).hour
            hourly[msg].append(hour)

        # Find messages that appear 3+ times within a 2-hour window
        now_hour = datetime.now().hour
        patterns = []
        for msg, hours in hourly.items():
            if len(hours) < 3:
                continue
            # Check if there's a cluster around the current hour
            for target_hour in range(max(0, now_hour - 2), min(24, now_hour + 3)):
                count = sum(1 for h in hours if abs(h - target_hour) <= 1)
                if count >= 2:
                    patterns.append(msg)
                    break
        return patterns[:3]
    except Exception as e:
        log.warning(f"Pattern detection failed: {e}")
        return []


# ── System Stats ───────────────────────────────────────────────────────
def get_system_stats() -> str:
    try:
        import subprocess
        parts = []
        # CPU load (1 min average)
        try:
            r = subprocess.run(["ps", "-A", "-o", "%cpu"], capture_output=True, text=True, timeout=5)
            lines = r.stdout.strip().split("\n")[1:]
            cpus = [float(l.strip()) for l in lines if l.strip()]
            if cpus:
                avg = sum(cpus) / len(cpus)
                parts.append(f"CPU: {avg:.1f}%")
        except Exception:
            pass
        # Memory
        try:
            r = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5)
            for line in r.stdout.split("\n"):
                if "page size of" in line:
                    page_size = int(line.split()[-2])
                if "Pages free" in line:
                    free_pages = int(line.split()[-1].rstrip("."))
            total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
            free = page_size * free_pages if 'page_size' in dir() else 0
            used_pct = (1 - free / total) * 100 if total else 0
            parts.append(f"Memory: {used_pct:.0f}%")
        except Exception:
            pass
        # Disk
        try:
            r = subprocess.run(["df", "-h", "/"], capture_output=True, text=True, timeout=5)
            line = r.stdout.strip().split("\n")[1]
            cols = line.split()
            if len(cols) >= 5:
                parts.append(f"Disk: {cols[4]}")
        except Exception:
            pass
        # Battery
        try:
            r = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True, timeout=5)
            for line in r.stdout.split("\n"):
                if "InternalBattery" in line:
                    m = re.search(r'(\d+)%', line)
                    if m:
                        parts.append(f"Battery: {m.group(1)}%")
                    if "charging" in line.lower() or "charged" in line.lower():
                        parts.append("(plugged in)")
                    break
        except Exception:
            pass
        return " | ".join(parts) if parts else ""
    except Exception:
        return ""


# ── Calendar Events ────────────────────────────────────────────────────
def get_calendar_events() -> list[str]:
    script = """
    tell application "Calendar"
        set output to ""
        set todayStart to (current date)
        set hours of todayStart to 0
        set minutes of todayStart to 0
        set seconds of todayStart to 0
        set todayEnd to todayStart + 86400
        repeat with c in calendars
            try
                set evts to events of c whose start date is greater than todayStart and start date is less than todayEnd
                repeat with e in evts
                    set startStr to (start date of e as string)
                    set summaryStr to summary of e
                    set output to output & startStr & " | " & summaryStr & linefeed
                end repeat
            end try
        end repeat
        return output
    end tell
    """
    try:
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=10)
        lines = [l.strip() for l in r.stdout.strip().split("\n") if l.strip()]
        return lines[:5]
    except Exception as e:
        log.warning(f"Calendar read failed: {e}")
        return []


def load_settings() -> dict:
    """Load .sompter/settings.json"""
    try:
        p = os.path.join(PROJECT_DIR, ".sompter", "settings.json")
        if os.path.exists(p):
            with open(p) as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def detect_focus_state(active_app: str = "", browser_tabs: str = "") -> str:
    """Detect user focus state: 'meeting', 'deep_work', 'idle', or 'normal'.
    Uses active app name, browser tabs, calendar events, and settings config."""
    try:
        # Check screen saver / idle
        try:
            r = subprocess.run(["pmset", "-g", "assertions"], capture_output=True, text=True, timeout=5)
            if "PreventUserIdleDisplaySleep" not in r.stdout and "PreventUserIdleSystemSleep" not in r.stdout:
                r2 = subprocess.run(["iohide", "state"], capture_output=True, text=True, timeout=3)
                if r2.returncode == 0 and "1" in r2.stdout.strip():
                    return "idle"
        except Exception:
            pass

        settings = load_settings()
        focus_cfg = settings.get("focus_mode", {})
        if not focus_cfg.get("enabled", True):
            return "normal"

        meeting_apps = [a.lower() for a in focus_cfg.get("meeting_apps", [
            "zoom.us", "microsoft teams", "slack", "facetime", "google meet"
        ])]
        focus_apps = [a.lower() for a in focus_cfg.get("focus_apps", [
            "code", "cursor", "windsurf", "xcode", "terminal", "iterm2", "obsidian"
        ])]

        app_lower = active_app.lower().strip()
        browser_lower = browser_tabs.lower()

        # Meeting check
        for ma in meeting_apps:
            if ma in app_lower or ma in browser_lower:
                log.info(f"Focus: meeting detected ({ma})")
                return "meeting"

        # Check calendar for current events
        try:
            events = get_calendar_events()
            if events:
                now = datetime.now()
                for ev in events:
                    m = re.match(r'(\d{1,2}:\d{2}:\d{2}\s+[AP]M)', ev)
                    if m:
                        try:
                            start = datetime.strptime(m.group(1), "%I:%M:%S %p").replace(
                                year=now.year, month=now.month, day=now.day)
                            # Assume 1-hour events
                            if start <= now <= (start + timedelta(hours=1)):
                                log.info(f"Focus: meeting from calendar ({ev})")
                                return "meeting"
                        except Exception:
                            pass
        except Exception:
            pass

        # Deep work check
        for fa in focus_apps:
            if fa in app_lower:
                log.info(f"Focus: deep work ({fa})")
                return "deep_work"

        return "normal"
    except Exception as e:
        log.warning(f"Focus detection failed: {e}")
        return "normal"


# ── Context injection (memory) ────────────────────────────────────────
def build_context(focus_state: str = "normal") -> str:
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

        if focus_state != "normal":
            parts.append(f"Focus state: {focus_state.upper()} — user is {'in a meeting' if focus_state == 'meeting' else 'deep in focused work' if focus_state == 'deep_work' else 'away from computer'}")

        sys_stats = get_system_stats()
        if sys_stats:
            parts.append(f"System: {sys_stats}")

        cal_events = get_calendar_events()
        if cal_events:
            parts.append("Today's Calendar:")
            for ev in cal_events:
                parts.append(f"  - {ev}")

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

        # Add detected patterns
        patterns = detect_patterns()
        if patterns:
            pattern_lines = "\n".join(f"  - \"{p}\"" for p in patterns)
            context += (
                f"\n\nRecurring patterns detected (you often ask about these "
                f"around this time of day):\n{pattern_lines}\n"
                "Proactively address these if relevant."
            )

        graph_ctx = get_graph_context()
        if graph_ctx:
            if context:
                context += "\n"
            context += f"\nKnowledge Graph:\n{graph_ctx}"

        return context
    except Exception as e:
        log.warning(f"Failed to build context: {e}")
        return ""


def save_screenshot_file(screenshot_b64: str, obs_id: int, active_app: str = "") -> str | None:
    """Save screenshot base64 to disk, return filename or None."""
    if not screenshot_b64:
        return None
    try:
        ss_dir = os.path.join(os.path.dirname(MEMORY_DB), "screenshots")
        os.makedirs(ss_dir, exist_ok=True)
        # Strip data URI prefix if present
        raw = screenshot_b64
        if "," in raw:
            raw = raw.split(",", 1)[1]
        img_data = base64.b64decode(raw)
        filename = f"obs_{obs_id}.jpg"
        path = os.path.join(ss_dir, filename)
        with open(path, "wb") as f:
            f.write(img_data)
        # Record in screenshots table
        conn = sqlite3.connect(MEMORY_DB)
        conn.execute(
            "INSERT OR IGNORE INTO screenshots (observation_id, filename, timestamp, active_app) VALUES (?, ?, ?, ?)",
            (obs_id, filename, datetime.now().isoformat(), active_app or ""),
        )
        conn.commit()
        conn.close()
        log.info(f"Screenshot saved: {filename} ({len(img_data)} bytes)")
        return filename
    except Exception as e:
        log.warning(f"Failed to save screenshot: {e}")
        return None


def save_observation(
    active_app: str,
    notes_msg: str,
    screenshot_b64: str,
    search_context: str,
    ai_reply: str,
):
    try:
        conn = sqlite3.connect(MEMORY_DB)
        cur = conn.execute(
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
        obs_id = cur.lastrowid
        conn.commit()
        conn.close()
        # Save screenshot file
        if screenshot_b64:
            save_screenshot_file(screenshot_b64, obs_id, active_app)
        update_entities_and_relationships(notes_msg, ai_reply, active_app)
    except Exception as e:
        log.error(f"Failed to save observation: {e}")


# ── Knowledge Graph (Entities + Relationships) ─────────────────────────
ENTITY_TYPES = ["person", "technology", "project", "organization", "location", "sports_team", "topic"]

TECH_KEYWORDS = {
    "python", "javascript", "typescript", "rust", "go", "react", "node.js", "electron",
    "fastapi", "flask", "django", "sqlite", "postgresql", "redis", "docker",
    "opencode", "ollama", "gemma", "moondream", "gemini", "openai", "playwright",
    "launchd", "pytest", "git", "github", "sqlalchemy", "uvicorn", "sse",
    "apple script", "osascript", "sips", "screencapture",
}

LOCATION_KEYWORDS = {
    "new york", "los angeles", "san francisco", "chicago", "seattle", "austin",
    "boston", "london", "tokyo", "berlin", "paris", "sydney", "toronto",
}


def classify_entity(name: str) -> str:
    lower = name.lower()
    if lower in TECH_KEYWORDS:
        return "technology"
    if lower in LOCATION_KEYWORDS:
        return "location"
    for league, teams in TEAM_DATABASE.items():
        for team in teams:
            if lower == team:
                return "sports_team"
    if lower in ("sompter", "sompter ai"):
        return "project"
    return "topic"


def extract_entities(text: str) -> list[tuple[str, str]]:
    entities = []
    seen = set()
    lower = text.lower()

    for kw in TECH_KEYWORDS:
        if kw in lower:
            if kw not in seen:
                seen.add(kw)
                entities.append((kw.title(), "technology"))
    for kw in LOCATION_KEYWORDS:
        if kw in lower:
            if kw not in seen:
                seen.add(kw)
                entities.append((kw.title(), "location"))
    for league, teams in TEAM_DATABASE.items():
        for team in teams:
            if team in lower:
                if team not in seen:
                    seen.add(team)
                    entities.append((team.title(), "sports_team"))

    matches = re.findall(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,4})\b', text)
    for m in matches:
        m_lower = m.lower()
        if m_lower in seen or len(m) < 4:
            continue
        if m_lower in ("the", "this", "that", "these", "those", "what", "when", "where", "why", "how"):
            continue
        seen.add(m_lower)
        etype = classify_entity(m_lower)
        entities.append((m, etype))

    return entities


def update_entities_and_relationships(notes_msg: str, ai_reply: str, active_app: str):
    all_text = f"{notes_msg} {ai_reply} {active_app}"
    entities = extract_entities(all_text)
    if not entities:
        return
    now = datetime.now().isoformat()
    try:
        conn = sqlite3.connect(MEMORY_DB)
        entity_ids = {}
        for name, etype in entities:
            row = conn.execute(
                "SELECT id FROM entities WHERE name = ?", (name,)
            ).fetchone()
            if row:
                eid = row[0]
                conn.execute(
                    """UPDATE entities SET mentions = mentions + 1, last_seen = ?
                       WHERE id = ?""",
                    (now, eid),
                )
            else:
                conn.execute(
                    """INSERT INTO entities (name, type, mentions, first_seen, last_seen, context)
                       VALUES (?, ?, 1, ?, ?, '')""",
                    (name, etype, now, now),
                )
                eid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            entity_ids[name] = eid

        names = list(entity_ids.keys())
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                e1 = entity_ids[names[i]]
                e2 = entity_ids[names[j]]
                row = conn.execute(
                    """SELECT id, strength FROM relationships
                       WHERE (entity1_id = ? AND entity2_id = ?)
                          OR (entity1_id = ? AND entity2_id = ?)""",
                    (e1, e2, e2, e1),
                ).fetchone()
                if row:
                    rid, strength = row
                    conn.execute(
                        """UPDATE relationships SET strength = ?, last_seen = ?
                           WHERE id = ?""",
                        (strength + 1, now, rid),
                    )
                else:
                    conn.execute(
                        """INSERT INTO relationships
                           (entity1_id, entity2_id, relationship_type, strength, last_seen)
                           VALUES (?, ?, ?, 1, ?)""",
                        (e1, e2, "related_to", now),
                    )
        conn.commit()
        conn.close()
    except Exception as e:
        log.warning(f"Knowledge graph update failed: {e}")


def get_top_entities(limit: int = 20) -> list[dict]:
    try:
        conn = sqlite3.connect(MEMORY_DB)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT name, type, mentions, last_seen FROM entities
               ORDER BY mentions DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_top_relationships(limit: int = 20) -> list[dict]:
    try:
        conn = sqlite3.connect(MEMORY_DB)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT e1.name AS entity1, e1.type AS type1, e2.name AS entity2,
                      e2.type AS type2, r.strength, r.relationship_type
               FROM relationships r
               JOIN entities e1 ON r.entity1_id = e1.id
               JOIN entities e2 ON r.entity2_id = e2.id
               ORDER BY r.strength DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_graph_context(max_entities: int = 8) -> str:
    entities = get_top_entities(max_entities)
    rels = get_top_relationships(6)
    parts = []
    if entities:
        parts.append("Known entities: " + ", ".join(
            f"{e['name']} ({e['type']})" for e in entities
        ))
    if rels:
        parts.append("Relationships: " + "; ".join(
            f"{r['entity1']} → {r['entity2']} ({r['relationship_type']}, strength {r['strength']})"
            for r in rels
        ))
    return " | ".join(parts) if parts else ""


def ai_enhance_entities():
    """Batch-call the AI every ~20 observations to extract high-quality entities.
    Uses the backend /api/chat to analyze recent observation text."""
    try:
        conn = sqlite3.connect(MEMORY_DB)
        rows = conn.execute(
            """SELECT id, notes_message, ai_reply FROM observations
               WHERE ai_reply != '' ORDER BY id DESC LIMIT 20"""
        ).fetchall()
        conn.close()
        if len(rows) < 5:
            return
    except Exception:
        return

    obs_text = ""
    for oid, msg, reply in rows:
        obs_text += f"\nObs #{oid}: User: {msg[:200]} | AI: {reply[:300]}"

    prompt = (
        "Extract named entities from these AI chat observations. "
        "For each entity, give: name, type (person/technology/project/organization/location/topic). "
        "Also identify relationships between entities using '->' with a type. "
        "Return in this exact format:\n"
        "ENTITIES:\n- Person: Name\n- Technology: Name\n...\n"
        "RELATIONSHIPS:\n- Name1 -> Name2 (works_with)\n...\n\n"
        "Only include clear, specific entities. Skip generic words.\n"
        f"Observations:\n{obs_text}"
    )

    try:
        resp = requests.post(
            f"{BACKEND_URL}/api/chat",
            json={"prompt": prompt, "screenshot": "", "search_web": False},
            timeout=120,
        )
        result = resp.json().get("message", "")
    except Exception as e:
        log.warning(f"AI entity enhancement failed: {e}")
        return

    # Parse entities from response
    current_type = None
    ai_entities: list[tuple[str, str]] = []
    ai_relationships: list[tuple[str, str, str]] = []
    in_entities = False
    in_relationships = False

    for line in result.split("\n"):
        line = line.strip()
        if line.upper().startswith("ENTITIES"):
            in_entities = True
            in_relationships = False
            continue
        if line.upper().startswith("RELATIONSHIPS"):
            in_entities = False
            in_relationships = True
            continue
        if in_entities and line.startswith("-"):
            # Parse "- Type: Name"
            m = re.match(r'-\s*(person|technology|project|organization|location|topic|sports_team):\s*(.+)', line, re.I)
            if m:
                etype = m.group(1).lower()
                ename = m.group(2).strip().rstrip('.')
                if ename and len(ename) >= 3:
                    ai_entities.append((ename, etype))
            # Also try "- Name (type)" format
            m = re.match(r'-\s*(.+?)\s*\((\w+)\)', line)
            if m and not any(n == m.group(1).strip() for n, _ in ai_entities):
                ai_entities.append((m.group(1).strip(), m.group(2).lower()))
        if in_relationships and "->" in line:
            parts = line.split("->")
            if len(parts) == 2:
                e1 = parts[0].strip().lstrip("- ")
                rest = parts[1].strip()
                m = re.match(r'(.+?)\s*\((.+?)\)', rest)
                if m:
                    e2 = m.group(1).strip()
                    rel = m.group(2).strip()
                    ai_relationships.append((e1, e2, rel))

    if not ai_entities:
        return

    now = datetime.now().isoformat()
    try:
        conn = sqlite3.connect(MEMORY_DB)
        # Upsert AI-identified entities
        for ename, etype in ai_entities:
            row = conn.execute(
                "SELECT id FROM entities WHERE name = ?", (ename,)
            ).fetchone()
            if row:
                conn.execute(
                    """UPDATE entities SET mentions = mentions + 5, type = ?,
                       last_seen = ?, context = 'ai-enhanced'
                       WHERE id = ?""",
                    (etype, now, row[0]),
                )
            else:
                conn.execute(
                    """INSERT INTO entities (name, type, mentions, first_seen, last_seen, context)
                       VALUES (?, ?, 5, ?, ?, 'ai-enhanced')""",
                    (ename, etype, now, now),
                )
        # Upsert AI-identified relationships
        for e1_name, e2_name, rel_type in ai_relationships:
            r1 = conn.execute("SELECT id FROM entities WHERE name = ?", (e1_name,)).fetchone()
            r2 = conn.execute("SELECT id FROM entities WHERE name = ?", (e2_name,)).fetchone()
            if r1 and r2:
                e1_id, e2_id = r1[0], r2[0]
                existing = conn.execute(
                    """SELECT id, strength FROM relationships
                       WHERE (entity1_id = ? AND entity2_id = ?)
                          OR (entity1_id = ? AND entity2_id = ?)""",
                    (e1_id, e2_id, e2_id, e1_id),
                ).fetchone()
                if existing:
                    conn.execute(
                        "UPDATE relationships SET strength = strength + 3, relationship_type = ?, last_seen = ? WHERE id = ?",
                        (rel_type, now, existing[0]),
                    )
                else:
                    conn.execute(
                        """INSERT INTO relationships
                           (entity1_id, entity2_id, relationship_type, strength, last_seen)
                           VALUES (?, ?, ?, 3, ?)""",
                        (e1_id, e2_id, rel_type, now),
                    )
        conn.commit()
        conn.close()
        log.info(f"AI entity enhancement: {len(ai_entities)} entities, {len(ai_relationships)} relationships")
    except Exception as e:
        log.warning(f"AI entity DB update failed: {e}")


# ── Sports team auto-discovery ──────────────────────────────────────────
TEAM_DATABASE: dict[str, list[str]] = {
    "nfl": [
        "arizona cardinals", "atlanta falcons", "baltimore ravens", "buffalo bills",
        "carolina panthers", "chicago bears", "cincinnati bengals", "cleveland browns",
        "dallas cowboys", "denver broncos", "detroit lions", "green bay packers",
        "houston texans", "indianapolis colts", "jacksonville jaguars", "kansas city chiefs",
        "las vegas raiders", "los angeles chargers", "los angeles rams", "miami dolphins",
        "minnesota vikings", "new england patriots", "new orleans saints", "new york giants",
        "new york jets", "philadelphia eagles", "pittsburgh steelers", "san francisco 49ers",
        "seattle seahawks", "tampa bay buccaneers", "tennessee titans", "washington commanders",
    ],
    "mlb": [
        "arizona diamondbacks", "atlanta braves", "baltimore orioles", "boston red sox",
        "chicago cubs", "chicago white sox", "cincinnati reds", "cleveland guardians",
        "colorado rockies", "detroit tigers", "houston astros", "kansas city royals",
        "los angeles angels", "los angeles dodgers", "miami marlins", "milwaukee brewers",
        "minnesota twins", "new york mets", "new york yankees", "oakland athletics",
        "philadelphia phillies", "pittsburgh pirates", "san diego padres",
        "san francisco giants", "seattle mariners", "st. louis cardinals",
        "tampa bay rays", "texas rangers", "toronto blue jays", "washington nationals",
    ],
    "nba": [
        "atlanta hawks", "boston celtics", "brooklyn nets", "charlotte hornets",
        "chicago bulls", "cleveland cavaliers", "dallas mavericks", "denver nuggets",
        "detroit pistons", "golden state warriors", "houston rockets", "indiana pacers",
        "los angeles clippers", "los angeles lakers", "memphis grizzlies", "miami heat",
        "milwaukee bucks", "minnesota timberwolves", "new orleans pelicans",
        "new york knicks", "oklahoma city thunder", "orlando magic", "philadelphia 76ers",
        "phoenix suns", "portland trail blazers", "sacramento kings", "san antonio spurs",
        "toronto raptors", "utah jazz", "washington wizards",
    ],
    "nhl": [
        "anaheim ducks", "boston bruins", "buffalo sabres", "calgary flames",
        "carolina hurricanes", "chicago blackhawks", "colorado avalanche",
        "columbus blue jackets", "dallas stars", "detroit red wings", "edmonton oilers",
        "florida panthers", "los angeles kings", "minnesota wild", "montreal canadiens",
        "nashville predators", "new jersey devils", "new york islanders",
        "new york rangers", "ottawa senators", "philadelphia flyers", "phoenix coyotes",
        "pittsburgh penguins", "san jose sharks", "seattle kraken", "st. louis blues",
        "tampa bay lightning", "toronto maple leafs", "vancouver canucks",
        "vegas golden knights", "washington capitals", "winnipeg jets",
    ],
}

def make_team_index() -> dict[str, str]:
    """Build a lowercase-name -> formatted name lookup."""
    idx = {}
    for league, teams in TEAM_DATABASE.items():
        for team in teams:
            idx[team] = team.title()
    return idx

TEAM_INDEX = make_team_index()


def auto_discover_teams() -> list[str]:
    """Scan recent observations for mentions of known sports teams.
    Returns newly discovered team names (not already tracked)."""
    existing = get_tracked_teams()
    existing_lower = {t.lower().strip() for t in existing}
    discovered = []
    try:
        conn = sqlite3.connect(MEMORY_DB)
        rows = conn.execute(
            "SELECT notes_message, ai_reply FROM observations ORDER BY id DESC LIMIT 100"
        ).fetchall()
        conn.close()
    except Exception:
        return []

    seen_team_leagues: dict[str, list[str]] = {}
    for msg, reply in rows:
        text = (msg + " " + reply).lower()
        for team_lower, team_title in TEAM_INDEX.items():
            if team_lower in text:
                # Map to a league
                league = next(
                    (l for l, teams in TEAM_DATABASE.items() if team_lower in teams), "sports"
                )
                if team_lower not in existing_lower and team_lower not in seen_team_leagues:
                    seen_team_leagues[team_lower] = [team_title, league]
                break

    for team_lower, (team_title, league) in seen_team_leagues.items():
        discovered.append(team_title)
        log.info(f"Auto-discovered team: {team_title} ({league})")

    if discovered:
        save_teams_to_settings(existing + discovered)

    return discovered


def save_teams_to_settings(teams: list[str]):
    settings_path = os.path.join(PROJECT_DIR, ".sompter", "settings.json")
    try:
        with open(settings_path) as f:
            s = json.load(f)
        s["tracked_teams"] = teams
        with open(settings_path, "w") as f:
            json.dump(s, f, indent=2)
    except Exception:
        pass
def get_tracked_teams() -> list[str]:
    settings_path = os.path.join(PROJECT_DIR, ".sompter", "settings.json")
    try:
        with open(settings_path) as f:
            s = json.load(f)
            return s.get("tracked_teams", [])
    except Exception:
        return []


def detect_interests() -> list[str]:
    """Analyze recent observations to detect recurring topics of interest.
    Uses weighted scoring (user questions count more) and recency decay."""
    interests = []
    try:
        conn = sqlite3.connect(MEMORY_DB)
        rows = conn.execute(
            "SELECT notes_message, ai_reply FROM observations WHERE ai_reply != '' ORDER BY id DESC LIMIT 50"
        ).fetchall()
        conn.close()

        from collections import Counter
        topics = Counter()
        now = time.time()
        interest_keywords = {
            "weather": ["weather", "temperature", "forecast", "rain", "°f", "°c", "humidity", "heat"],
            "mlb": ["dodger", "mlb", "baseball", "world series", "playoff", "standings", "mets", "yankees"],
            "nfl": ["nfl", "football", "super bowl", "touchdown", "nfc", "afc"],
            "nba": ["nba", "basketball", "playoffs", "nba finals", "lakers", "celtics"],
            "coding": ["python", "javascript", "typescript", "code", "opencode", "app", "daemon", "backend", "api", "npm", "git", "rust", "react"],
            "news": ["news", "headline", "breaking", "election", "market", "report", "announce"],
            "stocks": ["stock", "market", "crypto", "bitcoin", "trading", "invest", "s&p"],
            "sports": ["score", "game", "win", "loss", "champion", "tournament", "match"],
            "music": ["song", "album", "concert", "music", "spotify", "playlist"],
            "movies": ["movie", "film", "netflix", "streaming", "watch", "show"],
        }

        for i, (msg, reply) in enumerate(rows):
            text = (msg + " " + reply).lower()
            # User questions count 3x more than proactive observations
            weight = 3 if msg.strip() else 1
            # Recency decay: first row (newest) = full weight, last row = 0.5x
            recency = 1.0 - (i / len(rows)) * 0.5
            score = weight * recency
            for topic, keywords in interest_keywords.items():
                for kw in keywords:
                    if kw in text:
                        topics[topic] += score
                        break

        for topic, count in topics.most_common(5):
            if count >= 2.0:
                interests.append(topic)
    except Exception:
        pass
    return interests


def save_interests_to_settings(interests: list[str]):
    settings_path = os.path.join(PROJECT_DIR, ".sompter", "settings.json")
    try:
        with open(settings_path) as f:
            s = json.load(f)
        s["tracked_interests"] = interests
        with open(settings_path, "w") as f:
            json.dump(s, f, indent=2)
    except Exception:
        pass


def proactive_observation(context: str, active_app: str, screenshot_b64: str = "") -> str | None:
    # Always do a web search for interesting current info
    search_results = ""
    try:
        ws = get_web_search()
        if ws:
            teams = get_tracked_teams()
        settings_path = os.path.join(PROJECT_DIR, ".sompter", "settings.json")
        interests = []
        try:
            with open(settings_path) as f:
                s = json.load(f)
                interests = s.get("tracked_interests", [])
        except Exception:
            pass
        query_parts = []
        if interests:
            interest_topics = {"weather": "weather forecast", "mlb": "MLB baseball scores", "nfl": "NFL football",
                               "nba": "NBA basketball", "coding": "programming tech news", "news": "breaking news",
                               "stocks": "stock market"}
            for i in interests:
                if i in interest_topics:
                    query_parts.append(interest_topics[i])
        if teams:
            query_parts.append(" OR ".join(teams) + " scores, news, standings")
        if query_parts:
            query = ", ".join(query_parts)
            log.info(f"Proactive web search (interests: {', '.join(interests)}, teams: {', '.join(teams)}): {query}...")
        else:
            query = "latest news, sports scores, current events today"
            log.info(f"Proactive web search: {query}...")
        sr = ws(query)
        if sr and sr != "No results found.":
            search_results = sr[:1500]
            log.info(f"Proactive web search: got {len(sr)} chars of results")
        else:
            log.info("Proactive web search: no results")
    except Exception as e:
        log.warning(f"Proactive web search failed: {e}")

    # Build observation: combine moondream screen description with web search facts
    moondream_reply = ""
    try:
        user_msg = {"role": "user", "content": "Describe what I'm doing on screen in one short sentence."}
        if screenshot_b64:
            user_msg["images"] = [screenshot_b64]
        messages = [{"role": "system", "content": "You are a fast screen observer. One sentence only. Be specific."}, user_msg]
        resp = requests.post(
            "http://localhost:11434/api/chat",
            json={"model": "moondream", "messages": messages, "stream": False},
            timeout=15,
        )
        if resp.status_code == 200:
            moondream_reply = resp.json().get("message", {}).get("content", "").strip()
    except Exception as e:
        log.warning(f"Moondream vision failed: {e}")

    # Build observation: combine screen observation with web search facts
    parts = []
    if moondream_reply and len(moondream_reply) >= 10:
        parts.append(f"Screen: {moondream_reply}")
    if search_results:
        # Extract first concrete fact from web results
        lines = search_results.split("\n")[:5]
        facts = [l.strip() for l in lines if l.strip() and len(l.strip()) > 20][:2]
        if facts:
            parts.append(f"Web: {' | '.join(facts)}")
        else:
            parts.append(f"Web: {search_results[:200].strip()}")
    if parts:
        return " | ".join(parts)
    return ""


def write_daemon_status(
    cycle: int, status: str, active_app: str = "", notes_msg: str = "",
    obs_count: int = 0, pattern_count: int = 0, patterns: list[str] | None = None,
    last_obs_time: str = "", focus_state: str = "normal",
):
    data = {
        "pid": os.getpid(),
        "status": status,
        "cycle": cycle,
        "active_app": active_app[:100],
        "notes_message": notes_msg[:100] if notes_msg else "",
        "last_observation_time": last_obs_time,
        "observation_count": obs_count,
        "pattern_count": pattern_count,
        "recent_patterns": (patterns or [])[:3],
        "memory_db": MEMORY_DB,
        "interval": INTERVAL,
        "last_heartbeat": datetime.now().isoformat(),
        "system": get_system_stats(),
        "focus_state": focus_state,
    }
    try:
        os.makedirs(os.path.dirname(STATUS_FILE), exist_ok=True)
        with open(STATUS_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        log.warning(f"Failed to write status: {e}")


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

    # State tracking for change detection
    last_notes_messages: tuple[str, ...] = ()
    last_active_app = ""
    idle_cycles = 0
    interest_check_cycles = 0
    last_cycle_ts = time.time()

    # Initial interest detection
    initial_interests = detect_interests()
    if initial_interests:
        save_interests_to_settings(initial_interests)
        log.info(f"Detected interests at startup: {initial_interests}")

    # Initial team auto-discovery
    new_teams = auto_discover_teams()
    if new_teams:
        msg = f"Auto-discovered teams: {', '.join(new_teams)}"
        log.info(msg)
        send_notification("Sompter Teams", msg)

    # Main loop
    while running:
        cycle_count += 1
        log.info(f"── Cycle {cycle_count} ──")

        # Sleep/wake detection: if time delta > 2x interval, Mac was asleep
        now_ts = time.time()
        if cycle_count > 1 and now_ts - last_cycle_ts > INTERVAL * 2:
            missed = int((now_ts - last_cycle_ts) / INTERVAL)
            log.warning(f"!! Sleep/wake detected: missed ~{missed} cycles (gap was {now_ts - last_cycle_ts:.0f}s)")
        last_cycle_ts = now_ts

        # 1. Screenshot
        screenshot_b64 = ""
        try:
            screenshot_b64 = take_screenshot()
            log.info(f"Screenshot: {len(screenshot_b64)} bytes b64")
        except Exception as e:
            log.error(f"Screenshot failed: {e}")

        # 2. Active app + browser tabs
        active_app = ""
        try:
            active_app = get_active_app()
            browser_tabs = get_browser_tabs()
            if browser_tabs:
                active_app += f"\n{browser_tabs}"
            log.info(f"Active app: {active_app[:200]}")
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
        notes_messages_tuple = tuple(notes_msgs)

        # ── Focus detection ────────────────────────────────────────
        focus_state = detect_focus_state(active_app, browser_tabs if 'browser_tabs' in dir() else "")
        if focus_state != "normal":
            log.info(f"Focus state: {focus_state}")

        # ── Change detection ─────────────────────────────────────
        something_changed = (
            notes_messages_tuple != last_notes_messages
            or active_app != last_active_app
        )
        last_notes_messages = notes_messages_tuple
        last_active_app = active_app

        if not something_changed:
            idle_cycles += 1
            log.info(f"No changes detected (idle {idle_cycles}/{PROACTIVE_THRESHOLD})")
            # Write status so UI stays fresh
            _patterns_list = detect_patterns()
            conn = sqlite3.connect(MEMORY_DB)
            obs_count = conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
            conn.close()
            write_daemon_status(
                cycle_count, "running, no changes", active_app, "",
                obs_count, len(_patterns_list), _patterns_list,
                focus_state=focus_state,
            )
            # Proactive web search on prolonged idle (suppressed during focus)
            if idle_cycles >= PROACTIVE_THRESHOLD:
                if focus_state in ("meeting", "deep_work"):
                    log.info(f"Focus state is {focus_state}, suppressing proactive observation")
                    # Still do periodic interest/entity checks in background
                    interest_check_cycles += 1
                    if interest_check_cycles >= 10:
                        interest_check_cycles = 0
                        fresh = detect_interests()
                        if fresh:
                            save_interests_to_settings(fresh)
                        new_teams = auto_discover_teams()
                        if new_teams:
                            msg = f"Auto-discovered teams: {', '.join(new_teams)}"
                            log.info(msg)
                        ai_enhance_entities()
                else:
                    idle_cycles = 0
                    interest_check_cycles += 1
                    # Re-detect interests and discover teams every 10 proactive cycles
                    if interest_check_cycles >= 10:
                        interest_check_cycles = 0
                        fresh = detect_interests()
                        if fresh:
                            save_interests_to_settings(fresh)
                            log.info(f"Re-detected interests: {fresh}")
                        new_teams = auto_discover_teams()
                        if new_teams:
                            msg = f"Auto-discovered teams: {', '.join(new_teams)}"
                            log.info(msg)
                        ai_enhance_entities()
                    log.info("Idle threshold reached — generating proactive observation")
                    context = build_context(focus_state=focus_state)
                    pro_reply = proactive_observation(context, active_app, screenshot_b64)
                    if pro_reply:
                        log.info(f"Proactive reply ({len(pro_reply)} chars): {pro_reply[:200]}...")
                        try:
                            notes_append(pro_reply)
                            log.info("Proactive reply written to Notes")
                        except Exception as e:
                            log.error(f"Proactive append failed: {e}")
                        if should_notify(pro_reply, "", proactive=True):
                            send_notification("Sompter", strip_html(pro_reply)[:200], sound=True)
                            log.info("Proactive notification sent")
                        save_observation(active_app, "", "", "", pro_reply)
            # Wait and continue
            for _ in range(INTERVAL):
                if not running:
                    break
                time.sleep(1)
            continue

        idle_cycles = 0
        interest_check_cycles = 0  # reset so entity checks don't run mid-session

        # 4. Build memory context
        context = build_context(focus_state=focus_state)
        if context:
            log.info(f"Memory context: {len(context)} chars")

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
                if should_notify(reply, notes_msg):
                    send_notification("Sompter", strip_html(reply)[:200], sound=bool(notes_msg.strip()))
                    log.info("Notification sent")
                save_observation(active_app, notes_msg, screenshot_b64, "", reply)
            else:
                log.warning("No reply from backend")

        # Write daemon status
        _patterns_list = detect_patterns()
        conn = sqlite3.connect(MEMORY_DB)
        obs_count = conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
        conn.close()
        write_daemon_status(
            cycle_count, "running", active_app, notes_msg,
            obs_count, len(_patterns_list), _patterns_list,
            focus_state=focus_state,
        )

        # 6. Wait (check every second if we should stop)
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
