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
HASH_A = "a" * 64
HASH_B = "b" * 64


def acceptance_checks(status: str = "pass") -> list[dict]:
    return [
        {
            "name": "project-check-id",
            "status": status,
            "evidence": "project-supplied acceptance evidence",
        }
    ]


def requirement_row(**overrides: object) -> dict:
    row = {
        "requirement_id": "12b",
        "role": "PROJECT_DEFINED_ROLE",
        "planned_requirement": "Project-defined output requirement",
        "resolved_output": "build/output.json",
        "checks": acceptance_checks(),
        "fallback": {"used": False, "approved": False, "description": ""},
        "coverage": "pass",
    }
    row.update(overrides)
    return row


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


def manifest(**overrides: object) -> dict:
    value = {
        "require_context_receipt": True,
        "require_requirement_matrix": True,
        "corrective_run": False,
        "native_skill": {
            "slug": "project-domain-skill",
            "activation": "native-slash",
            "version_hash": HASH_A,
        },
        "references": [{"path": "references/domain-rules.md", "sha256": HASH_B}],
        "critical_rules": ["Project fallback requires prior approval"],
        "requirements": [
            {
                "requirement_id": "12b",
                "role": "PROJECT_DEFINED_ROLE",
                "required_checks": ["project-check-id"],
            }
        ],
    }
    value.update(overrides)
    return value


def receipt(**overrides: object) -> dict:
    value = {
        "skills_activated": [
            {
                "name": "project-domain-skill",
                "version_hash": HASH_A,
                "activation": "native-slash",
            }
        ],
        "references_loaded": [
            {"path": "references/domain-rules.md", "sha256": HASH_B}
        ],
        "critical_rules": ["Project fallback requires prior approval"],
    }
    value.update(overrides)
    return value


class TestSummarizeAgyStream(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="test-summarize-agy-")
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def run_summary(self, handoff: dict, context: dict | None = None) -> tuple:
        events = self.root / "events.ndjson"
        stderr = self.root / "stderr.txt"
        summary = self.root / "summary.json"
        report = self.root / "report.md"
        payloads = [
            {"event": "init", "init": {"conversation_id": "conv-1"}},
            {
                "event": "result",
                "result": {
                    "status": "SUCCESS",
                    "conversation_id": "conv-1",
                    "structured_output": handoff,
                },
            },
        ]
        events.write_text("\n".join(json.dumps(item) for item in payloads) + "\n")
        stderr.write_text("")
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
        if context is not None:
            context_path = self.root / "context-manifest.json"
            context_path.write_text(json.dumps(context))
            command.extend(["--context-manifest", str(context_path)])
        result = subprocess.run(command, capture_output=True, text=True)
        return result, json.loads(summary.read_text()), report

    def test_basic_handoff_remains_compatible_without_manifest(self) -> None:
        result, summary, report = self.run_summary(base_handoff())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(summary["context_manifest_applied"])
        self.assertTrue(report.exists())

    def test_manifest_requires_context_receipt(self) -> None:
        result, summary, report = self.run_summary(
            base_handoff(requirement_matrix=[requirement_row()]), manifest()
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing required context_receipt", summary["diagnostics"])
        self.assertFalse(report.exists())

    def test_native_skill_activation_and_hash_must_match(self) -> None:
        bad_receipt = receipt(
            skills_activated=[
                {
                    "name": "project-domain-skill",
                    "version_hash": HASH_B,
                    "activation": "contract-pack",
                }
            ]
        )
        result, summary, _ = self.run_summary(
            base_handoff(
                context_receipt=bad_receipt,
                requirement_matrix=[requirement_row()],
            ),
            manifest(),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(any("activation mismatch" in item for item in summary["diagnostics"]))
        self.assertTrue(any("version_hash mismatch" in item for item in summary["diagnostics"]))

    def test_exact_native_skill_must_be_present_in_receipt(self) -> None:
        result, summary, _ = self.run_summary(
            base_handoff(
                context_receipt=receipt(skills_activated=[]),
                requirement_matrix=[requirement_row()],
            ),
            manifest(),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "context_receipt missing skill: project-domain-skill",
            summary["diagnostics"],
        )

    def test_valid_contract_receipt_and_matrix_pass(self) -> None:
        result, summary, report = self.run_summary(
            base_handoff(
                context_receipt=receipt(),
                requirement_matrix=[requirement_row()],
            ),
            manifest(),
        )
        self.assertEqual(result.returncode, 0, summary["diagnostics"])
        self.assertTrue(summary["context_manifest_applied"])
        self.assertIn("## Requirement Matrix", report.read_text())

    def test_manifest_declared_check_cannot_be_omitted(self) -> None:
        result, summary, report = self.run_summary(
            base_handoff(
                context_receipt=receipt(),
                requirement_matrix=[
                    requirement_row(
                        checks=[
                            {
                                "name": "different-project-check",
                                "status": "pass",
                                "evidence": "checked",
                            }
                        ],
                    )
                ],
            ),
            manifest(),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "requirement_matrix 12b missing check: project-check-id",
            summary["diagnostics"],
        )
        self.assertFalse(report.exists())

    def test_unapproved_fallback_is_rejected_generically(self) -> None:
        result, summary, report = self.run_summary(
            base_handoff(
                context_receipt=receipt(),
                requirement_matrix=[
                    requirement_row(
                        fallback={
                            "used": True,
                            "approved": False,
                            "description": "project-specific alternative",
                        }
                    )
                ],
            ),
            manifest(),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requirement_matrix[0] uses an unapproved fallback", summary["diagnostics"])
        self.assertFalse(report.exists())

    def test_corrective_done_cannot_hide_coverage_deficit(self) -> None:
        corrective = manifest(corrective_run=True)
        result, summary, _ = self.run_summary(
            base_handoff(
                context_receipt=receipt(),
                requirement_matrix=[
                    requirement_row(
                        resolved_output="",
                        coverage="unresolved",
                        checks=acceptance_checks("unresolved"),
                    )
                ],
            ),
            corrective,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(any("coverage='unresolved'" in item for item in summary["diagnostics"]))

        partial, partial_summary, report = self.run_summary(
            base_handoff(
                status="partial",
                context_receipt=receipt(),
                requirement_matrix=[
                    requirement_row(
                        resolved_output="",
                        coverage="unresolved",
                        checks=acceptance_checks("unresolved"),
                    )
                ],
            ),
            corrective,
        )
        # Partial is the honest terminal handoff for unresolved corrective coverage.
        self.assertEqual(partial.returncode, 0, partial_summary["diagnostics"])
        self.assertTrue(report.exists())


if __name__ == "__main__":
    unittest.main()
