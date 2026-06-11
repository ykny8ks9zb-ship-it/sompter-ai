#!/bin/bash
# Sompter Watch Daemon — install / uninstall / start / stop / status / logs
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST_SRC="$DIR/com.sompter-ai.watch.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.sompter-ai.watch.plist"
LABEL="com.sompter-ai.watch"
LOG_FILE="/tmp/sompter-watch-daemon.log"
PID_FILE="/tmp/sompter-watch-daemon.pid"

cmd="${1:-help}"

case "$cmd" in
    install)
        echo "Installing watch daemon launchd plist..."
        mkdir -p "$HOME/Library/LaunchAgents"
        cp "$PLIST_SRC" "$PLIST_DST"
        launchctl load "$PLIST_DST"
        echo "Done. Daemon will auto-start on login."
        echo "Status:"
        launchctl list "$LABEL" 2>/dev/null || echo "(not running)"
        ;;

    uninstall)
        echo "Uninstalling watch daemon..."
        launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
        launchctl unload "$PLIST_DST" 2>/dev/null || true
        rm -f "$PLIST_DST"
        rm -f "$PID_FILE"
        echo "Done."
        ;;

    start)
        echo "Starting watch daemon..."
        if [ -f "$PLIST_DST" ]; then
            launchctl kickstart -k "gui/$(id -u)/$LABEL" 2>/dev/null || \
            launchctl load "$PLIST_DST"
        else
            echo "Error: plist not installed. Run '$0 install' first."
            exit 1
        fi
        echo "Done."
        ;;

    stop)
        echo "Stopping watch daemon..."
        launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
        rm -f "$PID_FILE"
        echo "Done."
        ;;

    restart)
        "$0" stop
        sleep 1
        "$0" start
        ;;

    status)
        if launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null | grep -q "state = running"; then
            echo "● Watch daemon is RUNNING"
            if [ -f "$PID_FILE" ]; then
                echo "  PID: $(cat "$PID_FILE")"
            fi
            echo "  Log: $LOG_FILE"
        else
            echo "○ Watch daemon is NOT running"
            echo "  Run '$0 start' to start it."
        fi
        ;;

    logs)
        if [ -f "$LOG_FILE" ]; then
            tail -f "$LOG_FILE"
        else
            echo "No log file found at $LOG_FILE"
            echo "Daemon may not have started yet."
        fi
        ;;

    logs-tail)
        if [ -f "$LOG_FILE" ]; then
            tail -30 "$LOG_FILE"
        else
            echo "No log file found."
        fi
        ;;

    install-summary)
    echo "Installing daily summary launchd timer..."
    mkdir -p "$HOME/Library/LaunchAgents"
    cp "$DIR/com.sompter-ai.daily-summary.plist" "$HOME/Library/LaunchAgents/"
    launchctl load "$HOME/Library/LaunchAgents/com.sompter-ai.daily-summary.plist"
    echo "Done. Summary will run daily at 12:05 AM."
    launchctl list "com.sompter-ai.daily-summary" 2>/dev/null | head -5
    ;;

    uninstall-summary)
    echo "Uninstalling daily summary timer..."
    launchctl bootout "gui/$(id -u)/com.sompter-ai.daily-summary" 2>/dev/null || true
    launchctl unload "$HOME/Library/LaunchAgents/com.sompter-ai.daily-summary.plist" 2>/dev/null || true
    rm -f "$HOME/Library/LaunchAgents/com.sompter-ai.daily-summary.plist"
    echo "Done."
    ;;

    *)
    echo "Usage: $0 <command>"
    echo ""
    echo "Watch Daemon commands:"
    echo "  install          Install plist and start daemon (auto-start on login)"
    echo "  uninstall        Stop and remove daemon"
    echo "  start            Start daemon (plist must be installed)"
    echo "  stop             Stop daemon"
    echo "  restart          Stop then start daemon"
    echo "  status           Check if daemon is running"
    echo "  logs             Tail daemon log file"
    echo ""
    echo "Daily Summary commands:"
    echo "  install-summary  Install plist timer (runs daily at 12:05 AM)"
    echo "  uninstall-summary Remove summary timer"
        ;;
esac
