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


def should_notify(reply: str, notes_msg: str, proactive: bool = False) -> bool:
    if not NOTIFICATIONS_ENABLED:
        return False
    if notes_msg.strip():
        return True
    if proactive:
        lower = reply.lower()
        return any(kw in lower for kw in PROACTIVE_ALERT_KEYWORDS)
    lower = reply.lower()
    return any(kw in lower for kw in IMPORTANT_KEYWORDS)


def send_notification(title: str, body: str):
    safe_title = title.replace('"', '\\"').replace("'", "'\\''")
    safe_body = body.replace('"', '\\"').replace("'", "'\\''")[:200]
    script = f'display notification "{safe_body}" with title "{safe_title}"'
    try:
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=5)
    except Exception as e:
        log.warning(f"Notification failed: {e}")


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

        # Add detected patterns
        patterns = detect_patterns()
        if patterns:
            pattern_lines = "\n".join(f"  - \"{p}\"" for p in patterns)
            context += (
                f"\n\nRecurring patterns detected (you often ask about these "
                f"around this time of day):\n{pattern_lines}\n"
                "Proactively address these if relevant."
            )
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
# ── Proactive suggestions ──────────────────────────────────────────────
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


def proactive_observation(context: str, active_app: str) -> str | None:
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

    teams = get_tracked_teams()
    interests = []
    try:
        settings_path = os.path.join(PROJECT_DIR, ".sompter", "settings.json")
        with open(settings_path) as f:
            s = json.load(f)
            interests = s.get("tracked_interests", [])
    except Exception:
        pass
    prompt = (
        "Generate one brief observation (1 sentence max) based on the web search results below. "
        "Pick the most specific and concrete item from the search results: "
        "a sports score, weather forecast, news headline, or tech story. "
        "Format: 'Noticed: <specific detail from search results>.'. "
        "Do NOT make up facts. Only use what is in the web search results."
    )
    system = (
        "You are a proactive AI assistant. Your job is to find the single most "
        "interesting specific fact from the web search results and report it."
        f"\n\nActive app: {active_app}"
    )
    if interests:
        system += f"\n\nThe user is interested in: {', '.join(interests)}. Prioritize these topics."
    if teams:
        system += f"\n\nThe user follows these sports teams: {', '.join(teams)}. Prioritize their scores, standings, and news."
    if context:
        system += f"\n\n[RECENT CONTEXT]\n{context}"
    if search_results:
        tag = ", ".join(teams) + " scores, news" if teams else "latest news, sports, events"
        system += f"\n\n[WEB SEARCH RESULTS — {tag}]\n{search_results}"
    else:
        prompt = (
            "Generate one brief observation about the user's current screen and "
            "what they might be working on. Be specific about what you see on screen. "
            "1 sentence max. Format: 'Noticed: <observation>.'. "
            "Do not mention web search results if none are available."
        )
        system = (
            "You are a proactive AI assistant. Examine the user's screen and "
            "generate one specific observation about what they are doing."
            f"\n\nActive app: {active_app}"
        )
    if interests:
        system += f"\n\nThe user is interested in: {', '.join(interests)}. Prioritize these topics."
    if teams:
        system += f"\n\nThe user follows these sports teams: {', '.join(teams)}. Prioritize their scores, standings, and news."
    if context:
        system += f"\n\n[RECENT CONTEXT]\n{context}"
    if search_results:
        tag = ", ".join(teams) + " scores, news" if teams else "latest news, sports, events"
        system += f"\n\n[WEB SEARCH RESULTS — {tag}]\n{search_results}"

    # Use fast moondream model for proactive observations (2s vs 57s)
    try:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
        resp = requests.post(
            "http://localhost:11434/api/chat",
            json={"model": "moondream", "messages": messages, "stream": False},
            timeout=30,
        )
        if resp.status_code == 200:
            return resp.json().get("message", {}).get("content", "")
    except Exception as e:
        log.warning(f"Moondream proactive call failed, falling back to backend: {e}")
    return call_backend("", active_app, "", context, system_override=system)


def write_daemon_status(
    cycle: int, status: str, active_app: str = "", notes_msg: str = "",
    obs_count: int = 0, pattern_count: int = 0, patterns: list[str] | None = None,
    last_obs_time: str = "",
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

    # Initial interest detection
    initial_interests = detect_interests()
    if initial_interests:
        save_interests_to_settings(initial_interests)
        log.info(f"Detected interests at startup: {initial_interests}")

    # Main loop
    while running:
        cycle_count += 1
        log.info(f"── Cycle {cycle_count} ──")

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
            )
            # Proactive web search on prolonged idle
            if idle_cycles >= PROACTIVE_THRESHOLD:
                idle_cycles = 0
                interest_check_cycles += 1
                # Re-detect interests every 10 proactive cycles
                if interest_check_cycles >= 10:
                    interest_check_cycles = 0
                    fresh = detect_interests()
                    if fresh:
                        save_interests_to_settings(fresh)
                        log.info(f"Re-detected interests: {fresh}")
                log.info("Idle threshold reached — generating proactive observation")
                context = build_context()
                pro_reply = proactive_observation(context, active_app)
                if pro_reply:
                    log.info(f"Proactive reply ({len(pro_reply)} chars): {pro_reply[:200]}...")
                    try:
                        notes_append(pro_reply)
                        log.info("Proactive reply written to Notes")
                    except Exception as e:
                        log.error(f"Proactive append failed: {e}")
                    if should_notify(pro_reply, "", proactive=True):
                        send_notification("Sompter", strip_html(pro_reply)[:200])
                        log.info("Proactive notification sent")
                    save_observation(active_app, "", "", "", pro_reply)
            # Wait and continue
            for _ in range(INTERVAL):
                if not running:
                    break
                time.sleep(1)
            continue

        idle_cycles = 0

        # 4. Build memory context
        context = build_context()
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
                    send_notification("Sompter", strip_html(reply)[:200])
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
