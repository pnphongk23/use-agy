#!/usr/bin/env python3

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import classify_run
import orchestrate_herdr as orchestration


class OrchestrateHerdrTest(unittest.TestCase):
    def test_parse_json_output_uses_last_json_line(self) -> None:
        value = orchestration.parse_json_output('noise\n{"old": 1}\n{"new": 2}\n')
        self.assertEqual(value, {"new": 2})

    def test_attention_detection_covers_permissions_and_onboarding(self) -> None:
        self.assertEqual(
            orchestration.needs_attention("Requesting permission for: git diff"),
            "requesting permission for",
        )
        self.assertEqual(
            orchestration.needs_attention("Do you trust the contents of this project?"),
            "do you trust the contents of this project",
        )
        self.assertIsNone(orchestration.needs_attention("STATUS: done"))

    def test_safe_command_redacts_work_order(self) -> None:
        self.assertEqual(
            orchestration.safe_command(
                ["agy", "--prompt-interactive", "private task", "--sandbox"]
            ),
            "agy --prompt-interactive <WORK_ORDER> --sandbox",
        )
        self.assertEqual(
            orchestration.safe_command(
                ["herdr", "pane", "run", "w1:p1", "private task"]
            ),
            "herdr pane run w1:p1 <WORK_ORDER>",
        )

    def test_handoff_detection_requires_structured_status(self) -> None:
        self.assertTrue(orchestration.has_handoff("STATUS: done\nSUMMARY: ok"))
        self.assertTrue(orchestration.has_handoff(" STATUS: blocked"))
        self.assertFalse(orchestration.has_handoff("Everything is done"))
        self.assertFalse(orchestration.has_handoff("STATUS: done | partial | blocked"))

    def test_handoff_requires_job_token_without_echoing_exact_token_line(self) -> None:
        token = "abc123"
        prompt = orchestration.prompt_with_token("do work", token)
        self.assertNotRegex(prompt, r"(?m)^RUN_TOKEN:\s*abc123\s*$")
        self.assertNotRegex(prompt, r"(?m)^HANDOFF_BEGIN:\s*abc123\s*$")
        self.assertFalse(orchestration.has_handoff("STATUS: done\n", token))
        self.assertEqual(
            orchestration.handoff_status(
                "HANDOFF_BEGIN: abc123\nSTATUS: partial\nRUN_TOKEN: abc123\n",
                token,
            ),
            "partial",
        )

    def test_echoed_status_cannot_combine_with_later_token(self) -> None:
        token = "abc123"
        terminal = "echoed example\nSTATUS: done\nRUN_TOKEN: abc123\n"
        self.assertIsNone(orchestration.handoff_status(terminal, token))

    def test_handoff_uses_status_inside_final_marker_block(self) -> None:
        token = "abc123"
        terminal = (
            "STATUS: done\nHANDOFF_BEGIN: abc123\nSTATUS: blocked\nRUN_TOKEN: abc123\n"
        )
        self.assertEqual(orchestration.handoff_status(terminal, token), "blocked")

    def test_classifier_does_not_turn_partial_or_blocked_into_success(self) -> None:
        self.assertEqual(
            classify_run.classify("", "STATUS: partial", None, True, "partial")[0],
            "partial",
        )
        self.assertEqual(
            classify_run.classify("", "STATUS: blocked", None, True, "blocked")[0],
            "blocked",
        )

    def test_git_baseline_detects_tracked_and_untracked_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
            subprocess.run(
                ["git", "config", "user.email", "test@example.com"],
                cwd=workspace,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test"], cwd=workspace, check=True
            )
            tracked = workspace / "tracked.txt"
            tracked.write_text("base\n")
            subprocess.run(["git", "add", "tracked.txt"], cwd=workspace, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=workspace, check=True)
            before = orchestration.git_baseline(workspace, [])
            tracked.write_text("changed\n")
            (workspace / "new.txt").write_text("new\n")
            after = orchestration.git_baseline(workspace, [])

            self.assertNotEqual(
                before["tracked_diff_sha256"], after["tracked_diff_sha256"]
            )
            self.assertIn("new.txt", after["untracked_sha256"])

    def test_git_baseline_hashes_files_inside_untracked_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
            nested = workspace / "new" / "nested.txt"
            nested.parent.mkdir()
            nested.write_text("before\n")
            before = orchestration.git_baseline(workspace, [])
            nested.write_text("after\n")
            after = orchestration.git_baseline(workspace, [])

            self.assertIn("new/nested.txt", before["untracked_sha256"])
            self.assertNotEqual(
                before["untracked_sha256"]["new/nested.txt"],
                after["untracked_sha256"]["new/nested.txt"],
            )

    def test_directory_baseline_hashes_only_explicit_scopes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            scoped = workspace / "lib"
            scoped.mkdir()
            (scoped / "a.txt").write_text("a\n")
            (workspace / "outside.txt").write_text("outside\n")

            baseline = orchestration.git_baseline(workspace, ["lib"])

            self.assertIn("lib/a.txt", baseline["scoped_sha256"])
            self.assertNotIn("outside.txt", baseline["scoped_sha256"])
            self.assertFalse(baseline["scope_required_for_evidence"])

    def test_baseline_hashes_symlink_without_following_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory).resolve()
            outside = workspace.parent / f"{workspace.name}-secret.txt"
            outside.write_text("secret\n")
            try:
                link = workspace / "link.txt"
                link.symlink_to(outside)
                baseline = orchestration.git_baseline(workspace, ["link.txt"])
                self.assertIn("link.txt", baseline["scoped_sha256"])
                self.assertTrue(
                    baseline["scoped_sha256"]["link.txt"].startswith("symlink:")
                )
            finally:
                outside.unlink(missing_ok=True)

    def test_baseline_does_not_follow_symlink_ancestor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory).resolve()
            outside = workspace.parent / f"{workspace.name}-outside"
            outside.mkdir()
            (outside / "secret.txt").write_text("secret\n")
            try:
                (workspace / "link").symlink_to(outside, target_is_directory=True)
                baseline = orchestration.git_baseline(workspace, ["link/secret.txt"])
                self.assertEqual(set(baseline["scoped_sha256"]), {"link"})
                self.assertTrue(
                    baseline["scoped_sha256"]["link"].startswith("symlink:")
                )
            finally:
                (outside / "secret.txt").unlink(missing_ok=True)
                outside.rmdir()

    def test_manifest_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            orchestration.save_manifest(run_dir, {"phase": "prepared"})
            value = orchestration.load_manifest(run_dir)
            self.assertEqual(value["phase"], "prepared")
            self.assertIn("updated_at", value)

    def test_manifest_evidence_scopes_supports_current_and_legacy_runs(self) -> None:
        self.assertEqual(
            orchestration.manifest_evidence_scopes({"evidence_scopes": ["src"]}),
            ["src"],
        )
        self.assertEqual(
            orchestration.manifest_evidence_scopes({"scopes": ["legacy"]}),
            ["legacy"],
        )

    def test_prepare_scope_alias_is_named_as_evidence_not_access(self) -> None:
        parser = orchestration.build_parser()
        args = parser.parse_args(
            [
                "prepare",
                "--workspace",
                "/tmp/workspace",
                "--scope",
                "legacy",
                "--evidence-scope",
                "src",
            ]
        )
        self.assertEqual(args.evidence_scope, ["legacy", "src"])

    def test_baseline_signature_ignores_capture_time(self) -> None:
        before = {"captured_at": "before", "tracked_diff_sha256": "same"}
        after = {"captured_at": "after", "tracked_diff_sha256": "same"}
        self.assertEqual(
            orchestration.baseline_signature(before),
            orchestration.baseline_signature(after),
        )

    def test_appended_terminal_returns_only_new_job_output(self) -> None:
        before = "old prompt\nSTATUS: done\n"
        current = before + "new prompt\nSTATUS: blocked\n"
        self.assertEqual(
            orchestration.appended_terminal(before, current),
            "new prompt\nSTATUS: blocked\n",
        )

    def test_old_handoff_is_not_present_in_new_job_segment(self) -> None:
        before = "STATUS: done\nSUMMARY: old\n"
        current = before + "new prompt accepted\nworking\n"
        self.assertFalse(
            orchestration.has_handoff(orchestration.appended_terminal(before, current))
        )

    def test_appended_terminal_handles_rolling_terminal_buffer(self) -> None:
        before = "old header\nSTATUS: done\nshared tail\n"
        current = "STATUS: done\nshared tail\nnew prompt\nSTATUS: partial\n"
        self.assertEqual(
            orchestration.appended_terminal(before, current),
            "new prompt\nSTATUS: partial\n",
        )

    def test_prepare_writes_manifest_after_health_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            run_dir = root / "run"
            args = argparse.Namespace(
                workspace=str(workspace),
                run_dir=str(run_dir),
                model=orchestration.DEFAULT_MODEL,
                agent_name="test-agent",
                evidence_scope=["lib"],
                no_model_fallback=False,
            )

            def fake_run(argv, **kwargs):
                if argv == ["agy", "--version"]:
                    stdout = "1.1.3\n"
                elif argv == ["herdr", "--version"]:
                    stdout = "herdr 0.7.3\n"
                elif argv == ["agy", "models"]:
                    stdout = "Gemini 3.5 Flash (Medium)\nGemini 3.5 Flash (Low)\n"
                elif argv[0:2] == ["agy", "--model"]:
                    stdout = "AGY_OK\n"
                else:
                    self.fail(f"unexpected command: {argv}")
                return subprocess.CompletedProcess(argv, 0, stdout, "")

            with (
                mock.patch.object(
                    orchestration.shutil, "which", return_value="/bin/tool"
                ),
                mock.patch.object(orchestration, "run", side_effect=fake_run),
                mock.patch.object(
                    orchestration, "herdr_server_running", return_value=True
                ),
                mock.patch.object(
                    orchestration,
                    "git_baseline",
                    return_value={"kind": "git", "captured_at": "now"},
                ),
                mock.patch.object(orchestration, "record_smoke"),
                mock.patch("builtins.print"),
            ):
                self.assertEqual(orchestration.prepare(args), 0)

            manifest = orchestration.load_manifest(run_dir)
            self.assertEqual(manifest["phase"], "prepared")
            self.assertEqual(manifest["model"], orchestration.DEFAULT_MODEL)
            self.assertFalse(manifest["server_started"])
            self.assertEqual(manifest["evidence_scopes"], ["lib"])

    def test_prepare_stops_owned_server_when_baseline_capture_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            args = argparse.Namespace(
                workspace=str(workspace),
                run_dir=str(root / "run"),
                model=orchestration.DEFAULT_MODEL,
                agent_name="test-agent",
                evidence_scope=[],
                no_model_fallback=True,
            )

            def fake_run(argv, **kwargs):
                outputs = {
                    ("agy", "--version"): "1.1.3\n",
                    ("herdr", "--version"): "herdr 0.7.3\n",
                    ("agy", "models"): "Gemini 3.5 Flash (Medium)\n",
                }
                stdout = (
                    "AGY_OK\n"
                    if argv[0:2] == ["agy", "--model"]
                    else outputs[tuple(argv)]
                )
                return subprocess.CompletedProcess(argv, 0, stdout, "")

            with (
                mock.patch.object(
                    orchestration.shutil, "which", return_value="/bin/tool"
                ),
                mock.patch.object(orchestration, "run", side_effect=fake_run),
                mock.patch.object(
                    orchestration, "herdr_server_running", return_value=False
                ),
                mock.patch.object(
                    orchestration, "start_herdr_server", return_value=(123, "identity")
                ),
                mock.patch.object(
                    orchestration,
                    "git_baseline",
                    side_effect=orchestration.OrchestrationError("baseline failed"),
                ),
                mock.patch.object(orchestration, "record_smoke"),
                mock.patch.object(orchestration, "stop_herdr_server") as stop,
            ):
                with self.assertRaises(orchestration.OrchestrationError):
                    orchestration.prepare(args)
            stop.assert_called_once_with(123, "identity")

    def test_cleanup_keeps_owned_server_when_agent_is_kept(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            orchestration.save_manifest(
                run_dir,
                {
                    "phase": "snapshotted",
                    "agent_name": "agent",
                    "server_started": True,
                    "server_pid": 123,
                },
            )
            args = argparse.Namespace(
                run_dir=str(run_dir), keep_agent=True, keep_server=False
            )
            with (
                mock.patch.object(orchestration, "stop_herdr_server") as stop,
                mock.patch.object(
                    orchestration, "herdr_server_running", return_value=True
                ),
                mock.patch("builtins.print"),
            ):
                self.assertEqual(orchestration.cleanup(args), 0)
            stop.assert_not_called()

    def test_cleanup_refuses_reused_agent_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            orchestration.save_manifest(
                run_dir,
                {
                    "phase": "recorded",
                    "agent_name": "agent",
                    "pane_id": "owned-pane",
                    "terminal_id": "owned-terminal",
                    "server_started": False,
                },
            )
            args = argparse.Namespace(
                run_dir=str(run_dir), keep_agent=False, keep_server=False
            )
            reused = {
                "name": "agent",
                "pane_id": "other-pane",
                "terminal_id": "other-terminal",
            }
            with mock.patch.object(orchestration, "agent_info", return_value=reused):
                with self.assertRaises(orchestration.OrchestrationError):
                    orchestration.cleanup(args)

    def test_failed_start_does_not_reconcile_by_mutable_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            prompt = run_dir / "prompt.txt"
            prompt.write_text("JOB: EXECUTE\nSTATUS contract")
            orchestration.save_manifest(
                run_dir,
                {
                    "phase": "prepared",
                    "agent_name": "agent",
                    "workspace": str(run_dir),
                    "model": orchestration.DEFAULT_MODEL,
                    "job_sequence": 1,
                },
            )
            args = argparse.Namespace(
                run_dir=str(run_dir),
                prompt_file=str(prompt),
                mode="accept-edits",
                no_sandbox=False,
            )
            with (
                mock.patch.object(
                    orchestration, "list_agents", return_value=[]
                ) as listed,
                mock.patch.object(
                    orchestration,
                    "run",
                    side_effect=orchestration.OrchestrationError("start failed"),
                ),
            ):
                with self.assertRaises(orchestration.OrchestrationError):
                    orchestration.launch(args)
            self.assertEqual(listed.call_count, 1)

    def test_launch_binds_workspace_with_cwd_and_add_dir(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            prompt = run_dir / "prompt.txt"
            prompt.write_text("JOB: EXPLORE\nMISSION: map code")
            orchestration.save_manifest(
                run_dir,
                {
                    "phase": "prepared",
                    "agent_name": "agent",
                    "workspace": str(run_dir),
                    "model": orchestration.DEFAULT_MODEL,
                    "job_sequence": 1,
                },
            )
            args = argparse.Namespace(
                run_dir=str(run_dir),
                prompt_file=str(prompt),
                mode="plan",
                no_sandbox=True,
            )
            completed = subprocess.CompletedProcess(
                [],
                0,
                '{"result":{"agent":{"name":"agent","pane_id":"pane","terminal_id":"terminal"}}}',
                "",
            )
            with (
                mock.patch.object(orchestration, "list_agents", return_value=[]),
                mock.patch.object(orchestration, "run", return_value=completed) as run,
                mock.patch.object(orchestration, "read_terminal", return_value="ready"),
                mock.patch("builtins.print"),
            ):
                self.assertEqual(orchestration.launch(args), 0)

            argv = run.call_args_list[0].args[0]
            self.assertEqual(argv[0:3], ["herdr", "agent", "start"])
            self.assertIn("--cwd", argv)
            self.assertEqual(argv[argv.index("--cwd") + 1], str(run_dir))
            self.assertIn("--add-dir", argv)
            self.assertEqual(argv[argv.index("--add-dir") + 1], str(run_dir))
            self.assertLess(argv.index("--add-dir"), argv.index("--prompt-interactive"))


if __name__ == "__main__":
    unittest.main()
