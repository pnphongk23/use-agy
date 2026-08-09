#!/usr/bin/env python3
"""Tests for preflight-agy.py."""

from __future__ import annotations

import json
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).parent.resolve()
PREFLIGHT_PATH = SCRIPTS_DIR / "preflight-agy.py"


class TestPreflightAgy(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="test-preflight-agy-")
        self.root = Path(self.temp_dir.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def fake_agy(
        self,
        *,
        installed: str = "1.1.11",
        latest: str = "1.1.12",
        changelog_exit: int = 0,
        help_on_stderr: bool = False,
    ) -> Path:
        path = self.root / "agy"
        script = textwrap.dedent(
            f"""\
            #!{sys.executable}
            import json
            import sys

            args = sys.argv[1:]
            if args == ["--version"]:
                print({installed!r})
            elif args == ["--help"]:
                print("--add-dir --dangerously-skip-permissions --json-schema --output-format --print-timeout", file=sys.stderr if {help_on_stderr!r} else sys.stdout)
            elif args == ["changelog"]:
                print({latest!r} + ": newest")
                raise SystemExit({changelog_exit})
            elif args == ["-p", "/skills", "--output-format", "json"]:
                print(json.dumps({{"status": "SUCCESS", "response": "alpha\\tA skill\\nbeta\\tB skill"}}))
            else:
                print("unexpected args", args, file=sys.stderr)
                raise SystemExit(9)
            """
        )
        path.write_text(script, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return path

    def run_preflight(self, agy: Path, *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(PREFLIGHT_PATH),
                "--workspace",
                str(self.workspace),
                "--runs-root",
                str(self.root / "runs"),
                "--agy-bin",
                str(agy),
                "--json",
                *extra,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )

    def test_initializes_run_and_reports_update(self) -> None:
        result = self.run_preflight(
            self.fake_agy(help_on_stderr=True), "--skill", "beta"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["installed_version"], "1.1.11")
        self.assertEqual(payload["latest_version"], "1.1.12")
        self.assertTrue(payload["update_available"])
        self.assertTrue(payload["native_skill_available"])
        run_dir = Path(payload["run_dir"])
        self.assertTrue((run_dir / "preflight.json").is_file())
        handoff = (run_dir / "handoff.md").read_text(encoding="utf-8")
        self.assertTrue(handoff.startswith("/beta\nGOAL\n"))
        self.assertIn("run-agy.py", payload["run_command"])
        self.assertIn('-p "$(<', payload["run_command"])
        self.assertIn("summarize-agy-stream.py", payload["summarize_command"])
        self.assertFalse((run_dir / "report.md").exists())

    def test_missing_native_skill_keeps_plain_handoff(self) -> None:
        result = self.run_preflight(self.fake_agy(), "--skill", "missing")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["native_skill_available"])
        handoff = Path(payload["handoff"]).read_text(encoding="utf-8")
        self.assertTrue(handoff.startswith("GOAL\n"))

    def test_latest_check_failure_is_advisory(self) -> None:
        result = self.run_preflight(self.fake_agy(changelog_exit=1))
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertIsNone(payload["latest_version"])
        self.assertFalse(payload["update_available"])
        self.assertTrue(payload["warnings"])

    def test_old_agy_fails_before_creating_run(self) -> None:
        result = self.run_preflight(self.fake_agy(installed="1.1.8"))
        self.assertEqual(result.returncode, 2)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "error")
        self.assertIn("too old", payload["error"])
        runs_root = self.root / "runs"
        self.assertFalse(runs_root.exists())


if __name__ == "__main__":
    unittest.main()
