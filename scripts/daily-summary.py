#!/usr/bin/env python3
"""
Sompter AI Daily Summary — compresses observations into bullet points.

Runs daily (midnight via launchd): queries today's observations from memory.db,
calls the AI to summarize, saves to daily_summaries table, writes to a
"Sompter Daily Log" note in Apple Notes, and injects context back into
future AI analyses via the daemon's context injection.
"""

import html as html_mod
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import urllib.request
from datetime import datetime, date, timedelta
from pathlib import Path

# ── Config ─────────────────────────────────────────────────────────────
BACKEND_URL = os.environ.get("SOMPTER_BACKEND_URL", "http://localhost:8787")
PROJECT_DIR = os.environ.get(
    "SOMPTER_PROJECT_DIR",
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)
MEMORY_DB = os.environ.get(
    "SOMPTER_MEMORY_DB",
    os.path.join(PROJECT_DIR, ".sompter", "memory.db"),
)
LOG_FILE = os.environ.get(
    "SOMPTER_DAILY_LOG",
    "/tmp/sompter-daily-summary.log",
)
NOTES_LOG_NAME = "Sompter Daily Log"

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("daily-summary")


# ── Helpers ────────────────────────────────────────────────────────────
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


def run_osascript(script: str, timeout: int = 15) -> str:
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result.stdout.strip()


# ── Memory DB ──────────────────────────────────────────────────────────
def query_observations(target_date: date) -> list[dict]:
    conn = sqlite3.connect(MEMORY_DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT * FROM observations
           WHERE date(timestamp) = ?
           ORDER BY timestamp ASC""",
        (target_date.isoformat(),),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def save_daily_summary(target_date: date, summary: str, key_facts: str):
    conn = sqlite3.connect(MEMORY_DB)
    conn.execute(
        """INSERT OR REPLACE INTO daily_summaries
           (date, summary, key_facts, created_at)
           VALUES (?, ?, ?, ?)""",
        (target_date.isoformat(), summary, key_facts, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def get_recent_summaries(days: int = 3) -> list[dict]:
    conn = sqlite3.connect(MEMORY_DB)
    conn.row_factory = sqlite3.Row
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    rows = conn.execute(
        """SELECT * FROM daily_summaries
           WHERE date >= ?
           ORDER BY date DESC""",
        (cutoff,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_recent_observations(hours: int = 4) -> list[dict]:
    conn = sqlite3.connect(MEMORY_DB)
    conn.row_factory = sqlite3.Row
    cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
    rows = conn.execute(
        """SELECT timestamp, active_app, notes_message, ai_reply, summary
           FROM observations
           WHERE timestamp >= ?
           ORDER BY timestamp DESC
           LIMIT 10""",
        (cutoff,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Apple Notes ────────────────────────────────────────────────────────
def notes_ensure_log_exists():
    script = f"""
        try
            tell application "Notes"
                set noteExists to false
                repeat with n in every note
                    if name of n is "{NOTES_LOG_NAME}" then
                        set noteExists to true
                        exit repeat
                    end if
                end repeat
                if not noteExists then
                    make new note at folder "Notes" with properties {{name:"{NOTES_LOG_NAME}", body:"<div>Sompter Daily Log</div>"}}
                end if
            end tell
        end try
    """
    run_osascript(script, timeout=10)


def notes_append(note_name: str, text: str):
    tmp_file = tempfile.mktemp(suffix=".txt", prefix="sompter-daily-")
    clean = tmp_file.replace("\\", "\\\\").replace('"', '\\"')
    html = f"<div><br><b>[Daily Summary {date.today().isoformat()}]:</b><br>{text}</div>"
    Path(tmp_file).write_text(html, "utf-8")
    script = f"""
        try
            tell application "Notes"
                set n to first note whose name is "{note_name}"
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


# ── AI Summarizer ──────────────────────────────────────────────────────
def call_ai_summarize(observations: list[dict]) -> tuple[str, str]:
    if not observations:
        return "No observations recorded today.", ""

    obs_text = ""
    for o in observations:
        ts = o.get("timestamp", "?")
        app = o.get("active_app", "?")
        msg = o.get("notes_message", "")
        reply = o.get("ai_reply", "")
        obs_text += f"\n--- {ts} | App: {app} ---"
        if msg:
            obs_text += f"\nUser: {msg[:300]}"
        if reply:
            obs_text += f"\nAI: {reply[:500]}"

    prompt = (
        "You are a daily summarization assistant. "
        "Below is a log of observations from today's screen-watching AI sessions. "
        "Compress this into 3-5 bullet points covering: the key work done, "
        "questions asked, answers received, and any recurring themes. "
        "Be specific with names, scores, numbers, and outcomes.\n\n"
        f"Today's date: {date.today().isoformat()}\n"
        f"Total observations: {len(observations)}\n"
        f"Observations:\n{obs_text}\n\n"
        "First, write a 2-3 sentence daily summary. Then list 3-5 bullet points. "
        "Finally, extract 3-5 key facts (specific names, numbers, scores, decisions) "
        "as a comma-separated list.\n\n"
        "FORMAT:\n"
        "SUMMARY: <2-3 sentences>\n"
        "BULLETS:\n"
        "- <bullet 1>\n"
        "- <bullet 2>\n"
        "...\n"
        "KEY_FACTS: <comma-separated list>"
    )

    payload = json.dumps({
        "prompt": prompt,
        "screenshot": "",
        "search_web": False,
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{BACKEND_URL}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            result = data.get("message", "")
    except Exception as e:
        log.error(f"AI call failed: {e}")
        return f"Summary generation failed: {e}", ""

    # Parse structured output
    summary = result
    key_facts = ""

    m = re.search(r"SUMMARY:\s*(.+?)(?:\n|$)", result, re.DOTALL)
    if m:
        summary = m.group(1).strip()

    m = re.search(r"KEY_FACTS:\s*(.+)", result, re.DOTALL)
    if m:
        key_facts = m.group(1).strip()

    # Format as HTML for the Notes log
    bullets_match = re.search(r"BULLETS:\s*(.+?)(?:\nKEY_FACTS|$)", result, re.DOTALL)
    if bullets_match:
        bullets_text = bullets_match.group(1).strip()
        bullets_html = bullets_text.replace("\n", "<br>")
        summary = f"<b>Summary:</b> {summary}<br><br><b>What happened:</b><br>{bullets_html}"

    return summary, key_facts


# ── Main ───────────────────────────────────────────────────────────────
def main():
    target = date.today()

    log.info("═" * 50)
    log.info(f"Daily Summary for {target.isoformat()}")
    log.info(f"Memory DB: {MEMORY_DB}")

    # 1. Query today's observations
    observations = query_observations(target)
    log.info(f"Found {len(observations)} observations for today")

    if not observations:
        # Check yesterday too
        yesterday = target - timedelta(days=1)
        observations = query_observations(yesterday)
        log.info(f"Found {len(observations)} observations for {yesterday.isoformat()}")
        if observations:
            target = yesterday

    if not observations:
        log.info("No observations found, nothing to summarize")
        return

    # 2. Call AI to summarize
    log.info("Generating summary...")
    summary, key_facts = call_ai_summarize(observations)
    log.info(f"Summary generated ({len(summary)} chars)")
    log.info(f"Key facts: {key_facts[:200] if key_facts else '(none)'}")

    # 3. Save to daily_summaries table
    save_daily_summary(target, summary, key_facts)
    log.info("Saved to daily_summaries table")

    # 4. Write to Apple Notes Daily Log
    notes_ensure_log_exists()
    notes_append(NOTES_LOG_NAME, summary)
    log.info(f"Appended to Notes note '{NOTES_LOG_NAME}'")

    log.info("Daily summary complete!")


if __name__ == "__main__":
    main()
