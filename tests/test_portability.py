"""Exercise configuration and CLI boundaries without touching personal sessions."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from stepagent.source import default_sessions_dir, expand_path


class ConfigurationTests(unittest.TestCase):
    def test_home_fallback_and_empty_variables(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            for env in ({}, {"CODEX_HOME": "", "CODEX_SESSIONS_DIR": ""}):
                with patch.dict(os.environ, env, clear=True), patch("pathlib.Path.home", return_value=home):
                    self.assertEqual(default_sessions_dir(), home / ".codex" / "sessions")

    def test_environment_precedence_and_dynamic_resolution(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "Codex home"
            sessions = Path(directory) / "sesje 世界"
            with patch.dict(os.environ, {"CODEX_HOME": str(home), "CODEX_SESSIONS_DIR": ""}, clear=True):
                self.assertEqual(default_sessions_dir(), home / "sessions")
                os.environ["CODEX_SESSIONS_DIR"] = str(sessions)
                self.assertEqual(default_sessions_dir(), sessions)

    def test_environment_variables_and_tilde(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"STEPAGENT_TEST_ROOT": directory}):
                self.assertEqual(expand_path("$STEPAGENT_TEST_ROOT/logs"), Path(directory) / "logs")
                if os.name == "nt":
                    self.assertEqual(expand_path(r"%STEPAGENT_TEST_ROOT%\logs"), Path(directory) / "logs")
            self.assertEqual(expand_path("~/logs"), Path.home() / "logs")


class CLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="stepagent cli ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sessions = self.root / "sesje 世界"
        self.sessions.mkdir()
        self.log = self.sessions / "rollout żółć.jsonl"
        self.log.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in [
            {"type": "session_meta", "payload": {"id": "portable-session", "cwd": str(self.root)}},
            {"type": "event_msg", "payload": {"type": "user_message", "message": "Zażółć 世界"}},
        ]) + "\n", encoding="utf-8")
        self.launcher = Path(__file__).resolve().parents[1] / "run.py"
        self.env = {**os.environ, "CODEX_HOME": str(self.root / "unused"),
                    "CODEX_SESSIONS_DIR": str(self.root / "missing"), "PYTHONIOENCODING": "ascii"}

    def run_cli(self, *args, env=None, launcher=None):
        return subprocess.run([*(launcher or [sys.executable, str(self.launcher)]), *args],
                              cwd=self.root, env=env or self.env, capture_output=True,
                              encoding="utf-8", timeout=15)

    def test_cli_directory_overrides_environment_and_preserves_unicode(self):
        result = self.run_cli("--sessions-dir", str(self.sessions), "--list")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("portable-session", result.stdout)
        self.assertIn("Zażółć 世界", result.stdout)

    def test_file_independent_of_discovery_and_relative_to_caller(self):
        result = self.run_cli("--file", str(self.log.relative_to(self.root)), "--snapshot")
        self.assertEqual(result.returncode, 0, result.stderr)
        doc = json.loads(result.stdout)
        self.assertEqual(doc["session_id"], "portable-session")
        self.assertEqual(doc["raw_records"], 2)
        self.assertIn("Zażółć 世界", result.stdout)

    def test_environment_directory_and_session_selection(self):
        env = {**self.env, "CODEX_SESSIONS_DIR": str(self.sessions)}
        for selector in (["--latest"], ["--session", "portable"]):
            result = self.run_cli("--snapshot", *selector, env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["session_id"], "portable-session")

    def test_missing_directory_and_missing_file_report_errors(self):
        result = self.run_cli("--list")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Brak katalogu", result.stderr)
        result = self.run_cli("--snapshot", "--file", "missing.jsonl")
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("Traceback", result.stderr)

    def test_help_version_and_noninteractive_error(self):
        for option in ("--help", "--version"):
            result = self.run_cli(option)
            self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_cli()
        self.assertEqual(result.returncode, 2)
        self.assertIn("interaktywnym terminalu", result.stderr)

    def test_invalid_intervals(self):
        for interval in ("nan", "inf", "0", "-1", "0.049"):
            self.assertEqual(self.run_cli("--interval", interval, "--list").returncode, 2)

    def test_platform_launcher_preserves_relative_paths(self):
        root = self.launcher.parent
        if os.name == "nt":
            launcher = ["powershell.exe", "-NoProfile", "-File", str(root / "run.ps1")]
        else:
            launcher = ["sh", str(root / "run.sh")]
        result = self.run_cli("--snapshot", "--file", str(self.log.relative_to(self.root)), launcher=launcher)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["session_id"], "portable-session")


if __name__ == "__main__":
    unittest.main()
