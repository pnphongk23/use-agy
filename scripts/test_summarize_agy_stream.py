#!/usr/bin/env python3
"""Contract tests for summarize-agy-stream.py."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("summarize-agy-stream.py")


def base_handoff(**overrides: object) -> dict:
    handoff = {
        "status": "done",
        "summary": "Complete",
        "evidence": ["checked"],
        "changes": [],
        "checks": ["pass"],
        "uncertainty": [],
        "next": "none",
    }
    handoff.update(overrides)
    return handoff


class TestSummarizeAgyStream(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="test-summarize-agy-")
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def run_summary(self, payloads: list[dict], stderr_text: str = "") -> tuple:
        events = self.root / "events.ndjson"
        stderr = self.root / "stderr.txt"
        summary = self.root / "summary.json"
        report = self.root / "report.md"
        events.write_text("\n".join(json.dumps(item) for item in payloads) + "\n", encoding="utf-8")
        stderr.write_text(stderr_text, encoding="utf-8")
        command = [
            sys.executable,
            str(SCRIPT),
            "--events",
            str(events),
            "--stderr",
            str(stderr),
            "--summary-out",
            str(summary),
            "--report-out",
            str(report),
        ]
        result = subprocess.run(command, capture_output=True, text=True)
        return result, json.loads(summary.read_text(encoding="utf-8")), report

    def test_basic_handoff_renders_report(self) -> None:
        result, summary, report = self.run_summary(
            [
                {"event": "init", "init": {"conversation_id": "conv-1"}},
                {
                    "event": "result",
                    "result": {
                        "status": "SUCCESS",
                        "conversation_id": "conv-1",
                        "structured_output": base_handoff(),
                    },
                },
            ]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(summary["stream_valid"])
        self.assertTrue(report.exists())
        self.assertNotIn("Context Receipt", report.read_text(encoding="utf-8"))

    def test_extra_handoff_fields_are_rejected(self) -> None:
        result, summary, report = self.run_summary(
            [
                {"event": "init", "init": {"conversation_id": "conv-1"}},
                {
                    "event": "result",
                    "result": {
                        "status": "SUCCESS",
                        "conversation_id": "conv-1",
                        "structured_output": base_handoff(unexpected_field={"unexpected": True}),
                    },
                },
            ]
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unexpected handoff fields: unexpected_field", summary["diagnostics"])
        self.assertFalse(report.exists())

    def test_active_then_error_is_not_unfinished(self) -> None:
        result, summary, report = self.run_summary(
            [
                {"event": "init", "init": {"conversation_id": "conv-1"}},
                {
                    "event": "step_update",
                    "step_update": {
                        "step_index": 1,
                        "step_type": "tool",
                        "tool_name": "run_command",
                        "state": "ACTIVE",
                    },
                },
                {
                    "event": "step_update",
                    "step_update": {
                        "step_index": 1,
                        "step_type": "tool",
                        "tool_name": "run_command",
                        "state": "ERROR",
                        "tool_info": {"name": "run_command", "error": "permission denied"},
                    },
                },
                {
                    "event": "result",
                    "result": {
                        "status": "SUCCESS",
                        "conversation_id": "conv-1",
                        "structured_output": base_handoff(status="partial"),
                    },
                },
            ]
        )
        self.assertEqual(result.returncode, 0, summary["diagnostics"])
        self.assertEqual(summary["unfinished_tools"], [])
        self.assertTrue(report.exists())

    def test_active_without_terminal_event_still_fails(self) -> None:
        result, summary, report = self.run_summary(
            [
                {"event": "init", "init": {"conversation_id": "conv-1"}},
                {
                    "event": "step_update",
                    "step_update": {
                        "step_index": 1,
                        "step_type": "tool",
                        "tool_name": "run_command",
                        "state": "ACTIVE",
                    },
                },
                {
                    "event": "result",
                    "result": {
                        "status": "SUCCESS",
                        "conversation_id": "conv-1",
                        "structured_output": base_handoff(),
                    },
                },
            ]
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unfinished ACTIVE tools: 1", summary["diagnostics"])
        self.assertEqual(len(summary["unfinished_tools"]), 1)
        self.assertFalse(report.exists())

    def test_stderr_is_reported_for_supervisor_review(self) -> None:
        result, summary, report = self.run_summary(
            [
                {"event": "init", "init": {"conversation_id": "conv-1"}},
                {
                    "event": "result",
                    "result": {
                        "status": "SUCCESS",
                        "conversation_id": "conv-1",
                        "structured_output": base_handoff(),
                    },
                },
            ],
            stderr_text="permission denied",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(any(item.startswith("stderr_preview: ") for item in summary["diagnostics"]))
        self.assertTrue(report.exists())


if __name__ == "__main__":
    unittest.main()
