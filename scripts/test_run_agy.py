#!/usr/bin/env python3
"""Tests for run-agy.py watchdog runner."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

# Path to script under test
SCRIPTS_DIR = Path(__file__).parent.resolve()
RUN_AGY_PATH = SCRIPTS_DIR / "run-agy.py"


class TestRunAgy(unittest.TestCase):

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="test-run-agy-")
        self.test_dir = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def get_paths(self, suffix: str) -> tuple[Path, Path]:
        events = self.test_dir / f"events_{suffix}.ndjson"
        stderr = self.test_dir / f"stderr_{suffix}.txt"
        if events.exists():
            events.unlink()
        if stderr.exists():
            stderr.unlink()
        return events, stderr

    def run_runner(
        self,
        suffix: str,
        cmd: list[str],
        *,
        liveness_timeout: float = 5,
        overall_timeout: float = 10,
        heartbeat_interval: float = 0.2,
    ) -> tuple[subprocess.CompletedProcess[str], Path, Path, float]:
        events_path, stderr_path = self.get_paths(suffix)
        runner_cmd = [
            sys.executable,
            str(RUN_AGY_PATH),
            "--events",
            str(events_path),
            "--stderr",
            str(stderr_path),
            "--liveness-timeout",
            str(liveness_timeout),
            "--overall-timeout",
            str(overall_timeout),
            "--heartbeat-interval",
            str(heartbeat_interval),
            "--",
        ] + cmd
        started = time.monotonic()
        result = subprocess.run(runner_cmd, capture_output=True, text=True, timeout=15)
        return result, events_path, stderr_path, time.monotonic() - started

    @staticmethod
    def read_events(path: Path) -> list[dict]:
        return [json.loads(line) for line in path.read_text().splitlines()]

    def test_synthetic_success(self) -> None:
        cmd = [
            sys.executable,
            "-c",
            "import sys; "
            'print(\'{"event": "init", "init": {"model": "gemini-3.5-flash-medium"}}\', flush=True); '
            'print(\'{"event": "step_update", "step_update": {"state": "ACTIVE", "tool_name": "read_file", "step_index": 1}}\', flush=True); '
            'print(\'{"event": "step_update", "step_update": {"state": "DONE", "tool_name": "read_file", "step_index": 1}}\', flush=True); '
            'print(\'{"event": "result", "result": {"status": "SUCCESS"}}\', flush=True);',
            "PRIVATE_HANDOFF_MARKER",
        ]

        res, events_path, _, _ = self.run_runner("success", cmd)
        self.assertEqual(res.returncode, 0, f"stdout: {res.stdout}, stderr: {res.stderr}")
        self.assertNotIn("PRIVATE_HANDOFF_MARKER", res.stdout)

        self.assertTrue(events_path.exists())
        events = self.read_events(events_path)
        self.assertEqual(len(events), 4)
        self.assertEqual(events[0]["event"], "init")
        self.assertEqual(events[1]["step_update"]["state"], "ACTIVE")
        self.assertEqual(events[2]["step_update"]["state"], "DONE")
        self.assertEqual(events[3]["event"], "result")
        self.assertEqual(events[3]["result"]["status"], "SUCCESS")

    def test_terminal_error(self) -> None:
        cmd = [
            sys.executable,
            "-c",
            "import sys; "
            'print(\'{"event": "init"}\', flush=True); '
            'print(\'{"event": "result", "result": {"status": "ERROR", "error": "Something went wrong"}}\', flush=True); '
            "sys.exit(1)",
        ]

        res, events_path, _, _ = self.run_runner("error", cmd)
        self.assertEqual(res.returncode, 1)
        events = self.read_events(events_path)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["event"], "init")
        self.assertEqual(events[1]["event"], "result")
        self.assertEqual(events[1]["result"]["status"], "ERROR")
        self.assertEqual(events[1]["result"]["error"], "Something went wrong")

    def test_stalled_active_tool(self) -> None:
        cmd = [
            sys.executable,
            "-c",
            "import time, sys; "
            'print(\'{"event": "init"}\', flush=True); '
            'print(\'{"event": "step_update", "step_update": {"state": "ACTIVE", "tool_name": "run_command", "step_index": 1}}\', flush=True); '
            "time.sleep(10); "
            'print(\'{"event": "step_update", "step_update": {"state": "DONE", "tool_name": "run_command", "step_index": 1}}\', flush=True); '
            'print(\'{"event": "result", "result": {"status": "SUCCESS"}}\', flush=True);',
        ]

        res, events_path, _, elapsed = self.run_runner(
            "stalled", cmd, liveness_timeout=0.5, overall_timeout=5
        )
        self.assertEqual(res.returncode, 1)
        self.assertLess(elapsed, 3)
        self.assertIn("[heartbeat]", res.stdout)

        events = self.read_events(events_path)
        self.assertEqual(len(events), 3)
        self.assertEqual(events[0]["event"], "init")
        self.assertEqual(events[1]["event"], "step_update")
        self.assertEqual(events[1]["step_update"]["state"], "ACTIVE")
        self.assertEqual(events[2]["event"], "result")
        self.assertEqual(events[2]["result"]["status"], "ERROR")
        self.assertIn("liveness timeout", events[2]["result"]["error"])

    def test_overall_timeout_does_not_require_active_tool(self) -> None:
        cmd = [
            sys.executable,
            "-c",
            "import time; "
            'print(\'{"event": "init"}\', flush=True); '
            "time.sleep(10)",
        ]
        res, events_path, _, elapsed = self.run_runner(
            "overall",
            cmd,
            liveness_timeout=0.3,
            overall_timeout=0.8,
            heartbeat_interval=0.2,
        )
        self.assertEqual(res.returncode, 1)
        self.assertLess(elapsed, 3)
        self.assertIn("[heartbeat]", res.stdout)
        events = self.read_events(events_path)
        self.assertEqual(events[-1]["event"], "result")
        self.assertIn("overall timeout", events[-1]["result"]["error"])
        self.assertNotIn("liveness timeout", events[-1]["result"]["error"])

    def test_terminal_result_stops_lingering_child_without_duplicate(self) -> None:
        cmd = [
            sys.executable,
            "-c",
            "import time; "
            'print(\'{"event": "init"}\', flush=True); '
            'print(\'{"event": "result", "result": {"status": "SUCCESS"}}\', flush=True); '
            "time.sleep(10)",
        ]
        res, events_path, _, elapsed = self.run_runner("linger", cmd)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertLess(elapsed, 3)
        events = self.read_events(events_path)
        results = [event for event in events if event.get("event") == "result"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["result"]["status"], "SUCCESS")

    def test_long_reasoning_heartbeats_without_liveness_failure(self) -> None:
        cmd = [
            sys.executable,
            "-c",
            "import time; "
            'print(\'{"event": "init"}\', flush=True); '
            "time.sleep(0.7); "
            'print(\'{"event": "result", "result": {"status": "SUCCESS"}}\', flush=True);',
        ]
        res, events_path, _, _ = self.run_runner(
            "reasoning",
            cmd,
            liveness_timeout=0.2,
            overall_timeout=3,
            heartbeat_interval=0.15,
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("[heartbeat]", res.stdout)
        events = self.read_events(events_path)
        self.assertEqual(events[-1]["result"]["status"], "SUCCESS")


if __name__ == "__main__":
    unittest.main()
