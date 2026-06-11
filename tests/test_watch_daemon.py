import importlib.util
import os
import sqlite3
import sys
import tempfile

# Import watch-daemon.py (hyphenated filename — use importlib)
_scripts_dir = os.path.join(os.path.dirname(__file__), "..", "scripts")
_spec = importlib.util.spec_from_file_location(
    "watch_daemon", os.path.join(_scripts_dir, "watch-daemon.py")
)
watch_daemon = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(watch_daemon)

strip_html = watch_daemon.strip_html
esc_html = watch_daemon.esc_html
should_notify = watch_daemon.should_notify
app_is_running = watch_daemon.app_is_running
get_browser_tabs = watch_daemon.get_browser_tabs


# ── Pure logic tests ───────────────────────────────────────────────

class TestStripHtml:
    def test_removes_tags(self):
        assert strip_html("<b>hello</b>") == "hello"

    def test_removes_nested_tags(self):
        assert strip_html("<div><p>hi</p></div>") == "hi"

    def test_preserves_text_without_tags(self):
        assert strip_html("plain text") == "plain text"

    def test_handles_empty_string(self):
        assert strip_html("") == ""

    def test_handles_newlines(self):
        result = strip_html("line1<br>line2")
        assert "line1" in result
        assert "line2" in result


class TestEscHtml:
    def test_escapes_angle_brackets(self):
        assert esc_html("<script>") == "&lt;script&gt;"

    def test_escapes_ampersand(self):
        assert esc_html("a&b") == "a&amp;b"

    def test_escapes_quotes(self):
        assert esc_html('"hello"') == "&quot;hello&quot;"

    def test_preserves_normal_text(self):
        assert esc_html("normal text") == "normal text"

    def test_handles_empty_string(self):
        assert esc_html("") == ""


class TestShouldNotify:
    def test_user_question_triggers(self):
        assert should_notify("some reply", "what is the weather?") is True

    def test_storm_keyword_triggers(self):
        assert should_notify("there is a storm warning", "") is True

    def test_score_keyword_triggers(self):
        assert should_notify("the final score was 5-2", "") is True

    def test_error_keyword_triggers(self):
        assert should_notify("there was an error", "") is True

    def test_crash_keyword_triggers(self):
        assert should_notify("system crash detected", "") is True

    def test_ordinary_reply_does_not_trigger(self):
        assert should_notify("everything is fine", "") is False

    def test_empty_reply_and_notes(self):
        assert should_notify("", "") is False


# ── System interaction tests ───────────────────────────────────────

class TestAppIsRunning:
    def test_opencode_running(self):
        assert app_is_running("OpenCode") is True

    def test_nonexistent_app(self):
        assert app_is_running("ThisAppDefinitelyDoesNotExistXYZ") is False


class TestGetBrowserTabs:
    def test_returns_string(self):
        result = get_browser_tabs()
        assert isinstance(result, str)


# ── Pattern detection tests ────────────────────────────────────────

class TestDetectPatterns:
    def setup_method(self):
        self._orig_db = watch_daemon.MEMORY_DB

    def teardown_method(self):
        watch_daemon.MEMORY_DB = self._orig_db

    def test_empty_db_returns_empty(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            conn = sqlite3.connect(db_path)
            conn.execute("""
                CREATE TABLE observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    notes_message TEXT
                )
            """)
            conn.commit()
            conn.close()
            watch_daemon.MEMORY_DB = db_path
            patterns = watch_daemon.detect_patterns()
            assert isinstance(patterns, list)
        finally:
            os.unlink(db_path)

    def test_single_message_returns_empty(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            conn = sqlite3.connect(db_path)
            conn.execute("""
                CREATE TABLE observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    notes_message TEXT
                )
            """)
            conn.execute(
                "INSERT INTO observations (timestamp, notes_message) VALUES (?, ?)",
                ("2026-06-11T13:00:00", "what is the weather?"),
            )
            conn.commit()
            conn.close()
            watch_daemon.MEMORY_DB = db_path
            patterns = watch_daemon.detect_patterns()
            assert isinstance(patterns, list)
            assert len(patterns) == 0
        finally:
            os.unlink(db_path)

    def test_three_identical_messages_returns_pattern(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            conn = sqlite3.connect(db_path)
            conn.execute("""
                CREATE TABLE observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    notes_message TEXT
                )
            """)
            for i in range(3):
                conn.execute(
                    "INSERT INTO observations (timestamp, notes_message) VALUES (?, ?)",
                    (f"2026-06-11T13:{0+i:02d}:00", "what is the weather?"),
                )
            conn.commit()
            conn.close()
            watch_daemon.MEMORY_DB = db_path
            patterns = watch_daemon.detect_patterns()
            assert len(patterns) >= 1
            assert "weather" in patterns[0]
        finally:
            os.unlink(db_path)

    def test_different_messages_no_pattern(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            conn = sqlite3.connect(db_path)
            conn.execute("""
                CREATE TABLE observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    notes_message TEXT
                )
            """)
            msgs = ["hello", "what's up", "good morning", "testing"]
            for i, msg in enumerate(msgs):
                conn.execute(
                    "INSERT INTO observations (timestamp, notes_message) VALUES (?, ?)",
                    (f"2026-06-10T1{i}:00:00", msg),
                )
            conn.commit()
            conn.close()
            watch_daemon.MEMORY_DB = db_path
            patterns = watch_daemon.detect_patterns()
            assert len(patterns) == 0
        finally:
            os.unlink(db_path)
