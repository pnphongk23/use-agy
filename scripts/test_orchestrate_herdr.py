#!/usr/bin/env python3

from __future__ import annotations

import argparse
import fcntl
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import classify_run
import orchestrate_herdr as orchestration


class OrchestrateHerdrTest(unittest.TestCase):
    def test_run_lock_fails_fast_when_another_helper_owns_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            lock_path = run_dir / ".orchestration.lock"
            lock_path.write_text(
                '{"pid": 4321, "command": "observe", "started_at": "now"}\n'
            )
            with mock.patch.object(
                orchestration.fcntl,
                "flock",
                side_effect=BlockingIOError,
            ) as flock:
                with self.assertRaisesRegex(
                    orchestration.OrchestrationError,
                    r'"pid": 4321.*"command": "observe"',
                ):
                    with orchestration.run_lock(run_dir, command="snapshot"):
                        self.fail("contended lock must not be acquired")
            self.assertEqual(
                flock.call_args.args[1], fcntl.LOCK_EX | fcntl.LOCK_NB
            )

    def test_run_lock_records_diagnostic_owner_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            with orchestration.run_lock(run_dir, command="observe"):
                owner = orchestration.parse_json_output(
                    (run_dir / ".orchestration.lock").read_text()
                )
                self.assertEqual(owner["command"], "observe")
                self.assertEqual(owner["pid"], orchestration.os.getpid())
                self.assertIn("started_at", owner)

    def test_parse_json_output_uses_last_json_line(self) -> None:
        value = orchestration.parse_json_output('noise\n{"old": 1}\n{"new": 2}\n')
        self.assertEqual(value, {"new": 2})

    def test_terminal_read_accepts_empty_raw_stdout(self) -> None:
        completed = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch.object(
            orchestration, "run_herdr", return_value=completed
        ) as herdr:
            self.assertEqual(
                orchestration.read_terminal_capture({"pane_id": "w1:p1"}),
                ("", False),
            )
        self.assertEqual(
            herdr.call_args.args[1],
            [
                "pane",
                "read",
                "w1:p1",
                "--source",
                "recent-unwrapped",
                "--lines",
                "180",
                "--format",
                "text",
            ],
        )

    def test_terminal_read_preserves_populated_raw_stdout(self) -> None:
        terminal = "AGY working\nprogress: 50%\n"
        completed = subprocess.CompletedProcess([], 0, terminal, "")
        with mock.patch.object(
            orchestration, "run_herdr", return_value=completed
        ):
            self.assertEqual(
                orchestration.read_terminal_capture(
                    {"pane_id": "w1:p1"}, lines=120
                ),
                (terminal, False),
            )

    def test_terminal_read_rejects_nonzero_result(self) -> None:
        completed = subprocess.CompletedProcess([], 7, "", "pane unavailable\n")
        with mock.patch.object(
            orchestration, "run_herdr", return_value=completed
        ):
            with self.assertRaisesRegex(
                orchestration.OrchestrationError,
                r"pane read failed \(7\).*pane unavailable",
            ):
                orchestration.read_terminal_capture({"pane_id": "w1:p1"})

    def test_launch_terminal_capture_failure_preserves_launched_phase(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            manifest = {
                "phase": "launched",
                "job_sequence": 1,
                "pane_id": "w1:p1",
            }
            orchestration.save_manifest(run_dir, manifest)
            with mock.patch.object(
                orchestration,
                "read_terminal",
                side_effect=orchestration.OrchestrationError("diagnostic failed"),
            ):
                self.assertFalse(
                    orchestration.capture_launch_terminal(run_dir, manifest)
                )

            saved = orchestration.load_manifest(run_dir)
            self.assertEqual(saved["phase"], "launched")
            self.assertEqual(saved["launch_terminal_capture"]["status"], "error")
            self.assertTrue((run_dir / "terminal-launch-1.error.json").exists())
            self.assertFalse((run_dir / "terminal-launch-1.txt").exists())

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

    def test_default_capabilities_open_skills_network_and_browser(self) -> None:
        profile = orchestration.capability_profile()
        self.assertEqual(profile["skill_loading"]["mode"], "allow")
        self.assertEqual(profile["network"]["mode"], "allow")
        self.assertEqual(profile["network"]["permission"], "read_url(*)")
        self.assertEqual(profile["browser"]["mode"], "allow")
        self.assertIn("execute_url(*)", profile["browser"]["permissions"])
        self.assertEqual(profile["mcp"]["allow"], [])
        self.assertEqual(profile["mcp"]["unmatched"], "ask")
        self.assertEqual(
            profile["runtime_permissions"]["required_allow"],
            ["read_url(*)", "execute_url(*)"],
        )
        self.assertFalse(profile["runtime_permissions"]["mutates_settings"])

    def test_capabilities_validate_and_sort_mcp_grants(self) -> None:
        args = argparse.Namespace(
            network="allow",
            browser="allow",
            mcp_allow=["docs/search", "code/*", "docs/search"],
        )
        profile = orchestration.capability_profile(args)
        self.assertEqual(profile["mcp"]["allow"], ["code/*", "docs/search"])
        self.assertEqual(
            profile["runtime_permissions"]["required_allow"],
            [
                "read_url(*)",
                "execute_url(*)",
                "mcp(code/*)",
                "mcp(docs/search)",
            ],
        )
        with self.assertRaisesRegex(
            orchestration.OrchestrationError, "expected server/tool"
        ):
            orchestration.capability_profile(
                argparse.Namespace(
                    network="allow",
                    browser="allow",
                    mcp_allow=["mcp(*)"],
                )
            )

    def test_permission_attention_classifies_open_web_as_config_mismatch(self) -> None:
        event = orchestration.attention_event(
            "Requesting permission for: read_url(example.com)"
        )
        self.assertEqual(event["kind"], "network")
        self.assertEqual(event["classification"], "configuration_mismatch")

        event = orchestration.attention_event(
            "Requesting permission for: execute_url(example.com)"
        )
        self.assertEqual(event["kind"], "browser")
        self.assertEqual(event["classification"], "configuration_mismatch")

    def test_permission_attention_respects_restricted_web_profile(self) -> None:
        profile = orchestration.capability_profile(
            argparse.Namespace(network="ask", browser="deny", mcp_allow=[])
        )
        network = orchestration.attention_event(
            "Requesting permission for: read_url(example.com)", profile
        )
        browser = orchestration.attention_event(
            "Requesting permission for: execute_url(example.com)", profile
        )
        self.assertEqual(network["classification"], "new_authority")
        self.assertEqual(browser["classification"], "new_authority")

        browser_only = orchestration.capability_profile(
            argparse.Namespace(network="deny", browser="allow", mcp_allow=[])
        )
        browser_load = orchestration.attention_event(
            "Requesting permission for: read_url(example.com)", browser_only
        )
        self.assertEqual(browser_load["classification"], "configuration_mismatch")

    def test_permission_attention_scopes_mcp_by_tool_or_server(self) -> None:
        profile = orchestration.capability_profile(
            argparse.Namespace(
                network="allow",
                browser="allow",
                mcp_allow=["docs/search", "index/*"],
            )
        )
        exact = orchestration.attention_event(
            "Requesting permission for: mcp(docs/search)", profile
        )
        wildcard = orchestration.attention_event(
            "Requesting permission for: mcp(index/query)", profile
        )
        unmatched = orchestration.attention_event(
            "Requesting permission for: mcp(database/mutate)", profile
        )
        self.assertEqual(exact["classification"], "configuration_mismatch")
        self.assertEqual(wildcard["classification"], "configuration_mismatch")
        self.assertEqual(unmatched["classification"], "new_authority")

    def test_complete_terminal_read_is_bounded_without_truncation_metadata(self) -> None:
        with mock.patch.object(
            orchestration,
            "read_terminal_capture",
            return_value=("bounded terminal", False),
        ) as capture:
            self.assertEqual(
                orchestration.read_complete_terminal(
                    "agent", lines=orchestration.MAX_TERMINAL_LINES + 1
                ),
                "bounded terminal",
            )
        capture.assert_called_once_with(
            "agent", orchestration.MAX_TERMINAL_LINES, "recent-unwrapped"
        )

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
        self.assertIn(orchestration.REPOSITORY_STANDARD, prompt)
        self.assertIn(orchestration.HANDOFF_FIELDS, prompt)
        self.assertIn("Skill loading is always allowed", prompt)
        self.assertIn("Network=allow; browser=allow", prompt)
        self.assertIn("unmatched MCP tools=ask", prompt)
        self.assertNotIn("120 lines", prompt)
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

    def test_prompt_does_not_duplicate_repository_standard(self) -> None:
        source = f"do work\n\n{orchestration.REPOSITORY_STANDARD}"
        prompt = orchestration.prompt_with_token(source, "abc123")
        self.assertEqual(prompt.count(orchestration.REPOSITORY_STANDARD), 1)

    def test_repository_standard_reframes_arbitrary_read_limits(self) -> None:
        source = "Do not read more than 3 files. Only read files listed in context."
        prompt = orchestration.prompt_with_token(source, "abc123")
        self.assertLess(prompt.index(source), prompt.index(orchestration.REPOSITORY_STANDARD))
        self.assertIn("file lists and counts as starting context", prompt)

    def test_work_order_template_uses_repository_standard(self) -> None:
        template = (
            Path(__file__).parents[1] / "references" / "work-orders.md"
        ).read_text(encoding="utf-8")
        self.assertIn(orchestration.REPOSITORY_STANDARD, template)

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
        self.assertEqual(
            orchestration.handoff_block(terminal, token),
            "HANDOFF_BEGIN: abc123\nSTATUS: blocked\nRUN_TOKEN: abc123",
        )

    def test_raw_conversation_handoff_survives_terminal_hard_wrap(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = root / "conversations"
            store.mkdir()
            conversation_id = "d934dcac-bd5b-4064-b67e-31b9b2c282f1"
            token = "bab4600464b34d5d844674f5816432ba"
            block = (
                f"HANDOFF_BEGIN: {token}\n"
                "STATUS: done\nSUMMARY: complete\n"
                f"RUN_TOKEN: {token}"
            )
            database = store / f"{conversation_id}.db"
            connection = sqlite3.connect(database)
            connection.execute(
                "CREATE TABLE steps (idx integer PRIMARY KEY, step_payload blob)"
            )
            connection.execute(
                "INSERT INTO steps (idx, step_payload) VALUES (?, ?)",
                # AGY 1.1.4 protobuf fields can place a printable wire tag
                # immediately after the closing token.
                (1, b"\x4a" + block.encode() + b"2(printable-trailer"),
            )
            connection.commit()
            connection.close()
            manifest = {
                "conversation_id": conversation_id,
                "conversation_store": str(store),
                "job_token": token,
            }
            wrapped_terminal = block.replace(token, token[:16] + "\n" + token[16:])
            self.assertFalse(orchestration.has_handoff(wrapped_terminal, token))
            self.assertEqual(orchestration.raw_conversation_handoff(manifest), block)

    def test_raw_handoff_rejects_marker_sentences_from_prompt(self) -> None:
        token = "abc123"
        prompt_echo = (
            f"Opening marker exactly: HANDOFF_BEGIN: {token}. "
            "STATUS: done\n"
            f"Closing marker exactly: RUN_TOKEN: {token}."
        )
        self.assertIsNone(orchestration.raw_handoff_block(prompt_echo, token))

    def test_conversation_id_is_pinned_from_job_log(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "job.log"
            log.write_text(
                "Created conversation d934dcac-bd5b-4064-b67e-31b9b2c282f1\n"
            )
            self.assertEqual(
                orchestration.conversation_id_from_log(log),
                "d934dcac-bd5b-4064-b67e-31b9b2c282f1",
            )

    def test_herdr_status_requires_compatible_matching_protocols(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            socket = Path(directory) / "herdr.sock"
            socket.touch()
            status = orchestration.parse_herdr_status(
                "client:\n  version: 0.7.3\n  protocol: 16\n\n"
                "server:\n  status: running\n  version: 0.7.3\n"
                f"  protocol: 16\n  compatible: yes\n  socket: {socket}\n"
            )
            runtime = orchestration.validate_herdr_status(status)
            self.assertEqual(runtime["protocol"], 16)
            status["server"]["compatible"] = "no"
            with self.assertRaises(orchestration.OrchestrationError):
                orchestration.validate_herdr_status(status)

    def test_final_layout_requires_one_owned_pane_filling_the_area(self) -> None:
        payload = {
            "result": {
                "layout": {
                    "area": {"x": 0, "y": 0, "width": 80, "height": 24},
                    "panes": [
                        {
                            "pane_id": "owned",
                            "rect": {
                                "x": 0,
                                "y": 0,
                                "width": 80,
                                "height": 24,
                            },
                        }
                    ],
                }
            }
        }
        orchestration.validate_single_full_pane_layout(payload, "owned")
        payload["result"]["layout"]["panes"][0]["rect"]["width"] = 40
        with self.assertRaisesRegex(orchestration.OrchestrationError, "does not fill"):
            orchestration.validate_single_full_pane_layout(payload, "owned")

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

    def test_prepare_parser_defaults_to_open_web_and_scoped_mcp(self) -> None:
        parser = orchestration.build_parser()
        defaults = parser.parse_args(
            ["prepare", "--workspace", "/tmp/workspace"]
        )
        self.assertEqual(defaults.network, "allow")
        self.assertEqual(defaults.browser, "allow")
        self.assertEqual(defaults.mcp_allow, [])

        scoped = parser.parse_args(
            [
                "prepare",
                "--workspace",
                "/tmp/workspace",
                "--network",
                "ask",
                "--browser",
                "deny",
                "--mcp-allow",
                "docs/search",
                "--mcp-allow",
                "index/*",
            ]
        )
        self.assertEqual(scoped.network, "ask")
        self.assertEqual(scoped.browser, "deny")
        self.assertEqual(scoped.mcp_allow, ["docs/search", "index/*"])

    def test_prepare_requires_explicit_herdr_authority(self) -> None:
        args = argparse.Namespace(herdr_authorized=False, workspace="/tmp")
        with self.assertRaisesRegex(
            orchestration.OrchestrationError, "--herdr-authorized"
        ):
            orchestration.prepare(args)

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

    def test_observe_uses_raw_handoff_when_terminal_marker_is_wrapped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            token = "abc123"
            before = "old terminal\n"
            raw_handoff = (
                f"HANDOFF_BEGIN: {token}\nSTATUS: done\n"
                + "evidence\n" * 300
                + f"RUN_TOKEN: {token}"
            )
            wrapped_terminal = raw_handoff.replace("abc123", "abc\n123")
            (run_dir / "terminal-before-1.txt").write_text(before)
            job_log = run_dir / "job.log"
            job_log.write_text("streaming\n")
            orchestration.save_manifest(
                run_dir,
                {
                    "phase": "launched",
                    "agent_name": "agent",
                    "pane_id": "pane",
                    "terminal_id": "terminal",
                    "workspace_id": "workspace",
                    "tab_id": "tab",
                    "job_sequence": 1,
                    "job_token": token,
                    "seen_working": True,
                    "job_log": str(job_log),
                    "job_log_offset": 0,
                    "conversation_id": "conversation",
                },
            )
            info = {
                "pane_id": "pane",
                "terminal_id": "terminal",
                "workspace_id": "workspace",
                "tab_id": "tab",
                "agent_status": "idle",
            }

            def fake_read(manifest, lines=180, source="recent-unwrapped"):
                if source == "visible":
                    return "ready"
                return wrapped_terminal

            args = argparse.Namespace(
                run_dir=str(run_dir), timeout=1, interval=0, lines=180
            )
            with (
                mock.patch.object(orchestration, "verify_herdr_runtime"),
                mock.patch.object(orchestration, "pane_info", return_value=info),
                mock.patch.object(
                    orchestration, "read_terminal", side_effect=fake_read
                ),
                mock.patch.object(
                    orchestration,
                    "refresh_conversation_identity",
                    return_value=True,
                ),
                mock.patch.object(
                    orchestration, "raw_conversation_handoff", return_value=raw_handoff
                ),
                mock.patch("builtins.print"),
            ):
                self.assertEqual(orchestration.observe(args), 0)

            handoff_file = run_dir / "handoff-1.txt"
            self.assertEqual(handoff_file.read_text(), raw_handoff + "\n")
            self.assertEqual(
                orchestration.handoff_status(handoff_file.read_text(), token), "done"
            )
            manifest = orchestration.load_manifest(run_dir)
            self.assertEqual(manifest["phase"], "handoff")
            self.assertEqual(manifest["handoff_source"], "agy-conversation-sqlite")

    def test_observe_fails_fast_after_completed_stream_with_malformed_raw_handoff(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            job_log = run_dir / "job.log"
            conversation_id = "d934dcac-bd5b-4064-b67e-31b9b2c282f1"
            job_log.write_text(f"Stream completed for {conversation_id}\n")
            orchestration.save_manifest(
                run_dir,
                {
                    "phase": "launched",
                    "pane_id": "pane",
                    "terminal_id": "terminal",
                    "workspace_id": "workspace",
                    "tab_id": "tab",
                    "job_sequence": 1,
                    "job_token": "abc123",
                    "seen_working": True,
                    "job_log": str(job_log),
                    "job_log_offset": 0,
                    "conversation_id": conversation_id,
                },
            )
            info = {
                "pane_id": "pane",
                "terminal_id": "terminal",
                "workspace_id": "workspace",
                "tab_id": "tab",
                "agent_status": "idle",
            }
            args = argparse.Namespace(
                run_dir=str(run_dir), timeout=1, interval=0, lines=180
            )
            with (
                mock.patch.object(orchestration, "verify_herdr_runtime"),
                mock.patch.object(orchestration, "pane_info", return_value=info),
                mock.patch.object(orchestration, "read_terminal", return_value="ready"),
                mock.patch.object(
                    orchestration, "refresh_conversation_identity", return_value=True
                ),
                mock.patch.object(
                    orchestration, "raw_conversation_handoff", return_value=None
                ),
                mock.patch.object(orchestration, "RAW_HANDOFF_GRACE_SECONDS", 0),
            ):
                with self.assertRaisesRegex(
                    orchestration.OrchestrationError, "without a valid raw"
                ):
                    orchestration.observe(args)
            self.assertEqual(
                orchestration.load_manifest(run_dir)["phase"], "malformed_handoff"
            )

    def test_snapshot_preserves_observed_handoff_after_marker_rolls_out(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            token = "abc123"
            handoff = (
                f"HANDOFF_BEGIN: {token}\nSTATUS: done\nRUN_TOKEN: {token}\n"
            )
            baseline = {"captured_at": "before", "kind": "directory"}
            orchestration.write_json(run_dir / "baseline-1.json", baseline)
            (run_dir / "terminal-before-1.txt").write_text("old\n")
            (run_dir / "terminal-1.txt").write_text("latest tail\n")
            (run_dir / "terminal-job-live-1.txt").write_text("latest tail\n")
            (run_dir / "handoff-1.txt").write_text(handoff)
            (run_dir / "job.log").write_text("log\n")
            orchestration.save_manifest(
                run_dir,
                {
                    "phase": "handoff",
                    "agent_name": "agent",
                    "pane_id": "pane",
                    "terminal_id": "terminal",
                    "workspace_id": "workspace",
                    "tab_id": "tab",
                    "job_sequence": 1,
                    "job_token": token,
                    "job_log": str(run_dir / "job.log"),
                    "job_log_offset": 0,
                    "workspace": str(run_dir),
                    "evidence_scopes": [],
                    "baseline_file": "baseline-1.json",
                },
            )
            info = {
                "pane_id": "pane",
                "terminal_id": "terminal",
                "workspace_id": "workspace",
                "tab_id": "tab",
                "agent_status": "idle",
            }
            args = argparse.Namespace(run_dir=str(run_dir), lines=260)
            with (
                mock.patch.object(orchestration, "verify_herdr_runtime"),
                mock.patch.object(orchestration, "pane_info", return_value=info),
                mock.patch.object(
                    orchestration,
                    "read_complete_terminal",
                    return_value="latest tail\n",
                ),
                mock.patch.object(
                    orchestration,
                    "git_baseline",
                    return_value={"captured_at": "after", "kind": "directory"},
                ),
                mock.patch("builtins.print"),
            ):
                self.assertEqual(orchestration.snapshot(args), 0)

            manifest = orchestration.load_manifest(run_dir)
            self.assertEqual(manifest["phase"], "snapshotted")
            comparison = json.loads((run_dir / "comparison-1.json").read_text())
            self.assertEqual(comparison["handoff_status"], "done")
            self.assertEqual(comparison["handoff"], "handoff-1.txt")
            saved = (run_dir / "terminal-job-1.txt").read_text()
            self.assertIsNone(orchestration.handoff_status(saved, token))

    def test_snapshot_rejects_missing_raw_handoff_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            orchestration.save_manifest(
                run_dir,
                {
                    "phase": "handoff",
                    "pane_id": "pane",
                    "terminal_id": "terminal",
                    "workspace_id": "workspace",
                    "tab_id": "tab",
                    "job_sequence": 1,
                    "job_token": "abc123",
                },
            )
            info = {
                "pane_id": "pane",
                "terminal_id": "terminal",
                "workspace_id": "workspace",
                "tab_id": "tab",
            }
            args = argparse.Namespace(run_dir=str(run_dir), lines=180)
            with (
                mock.patch.object(orchestration, "verify_herdr_runtime"),
                mock.patch.object(orchestration, "pane_info", return_value=info),
                mock.patch.object(
                    orchestration,
                    "read_complete_terminal",
                    return_value=(
                        "HANDOFF_BEGIN: abc123\nSTATUS: done\nRUN_TOKEN: abc123\n"
                    ),
                ),
            ):
                with self.assertRaisesRegex(
                    orchestration.OrchestrationError, "raw conversation handoff"
                ):
                    orchestration.snapshot(args)

    def test_record_uses_persisted_handoff_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            handoff_path = run_dir / "handoff-1.txt"
            handoff_path.write_text(
                "HANDOFF_BEGIN: abc123\nSTATUS: done\nRUN_TOKEN: abc123\n"
            )
            (run_dir / "job-log-segment-1.log").write_text("log\n")
            orchestration.write_json(
                run_dir / "comparison-1.json",
                {
                    "handoff": handoff_path.name,
                    "handoff_status": "done",
                    "job_log_segment": "job-log-segment-1.log",
                },
            )
            orchestration.save_manifest(
                run_dir,
                {
                    "phase": "snapshotted",
                    "job_sequence": 1,
                    "model": orchestration.DEFAULT_MODEL,
                },
            )
            args = argparse.Namespace(run_dir=str(run_dir), job="VERIFY")
            completed = subprocess.CompletedProcess([], 0, "recorded\n", "")
            with (
                mock.patch.object(
                    orchestration, "run", return_value=completed
                ) as run,
                mock.patch("builtins.print"),
            ):
                self.assertEqual(orchestration.record_verified(args), 0)
            argv = run.call_args.args[0]
            self.assertEqual(
                argv[argv.index("--stdout-file") + 1], str(handoff_path.resolve())
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
                herdr_authorized=True,
            )

            def fake_run(argv, **kwargs):
                if argv == ["agy", "--version"]:
                    stdout = "1.1.3\n"
                elif argv == ["herdr", "--version"]:
                    stdout = "herdr 0.7.3\n"
                elif argv == ["herdr", "--help"]:
                    stdout = (
                        "herdr workspace <subcommand>\n"
                        "herdr pane <subcommand>\n"
                        "herdr agent <subcommand>\n"
                    )
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
                    "capture_herdr_runtime",
                    return_value={
                        "client_version": "0.7.3",
                        "server_version": "0.7.3",
                        "protocol": 16,
                        "socket": "/tmp/herdr.sock",
                        "socket_identity": "1:2",
                    },
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
                herdr_authorized=True,
            )

            def fake_run(argv, **kwargs):
                outputs = {
                    ("agy", "--version"): "1.1.3\n",
                    ("herdr", "--version"): "herdr 0.7.3\n",
                    ("herdr", "--help"): (
                        "herdr workspace <subcommand>\n"
                        "herdr pane <subcommand>\n"
                        "herdr agent <subcommand>\n"
                    ),
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
                    "capture_herdr_runtime",
                    return_value={
                        "client_version": "0.7.3",
                        "server_version": "0.7.3",
                        "protocol": 16,
                        "socket": "/tmp/herdr.sock",
                        "socket_identity": "1:2",
                    },
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
            stop.assert_called_once_with(
                123, "identity", {"HERDR_SOCKET_PATH": "/tmp/herdr.sock"}
            )

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
                mock.patch.object(orchestration, "verify_herdr_runtime"),
                mock.patch.object(orchestration, "stop_herdr_server") as stop,
                mock.patch.object(
                    orchestration, "herdr_server_running", return_value=True
                ),
                mock.patch("builtins.print"),
            ):
                self.assertEqual(orchestration.cleanup(args), 0)
            stop.assert_not_called()

    def test_cleanup_refuses_changed_pane_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            orchestration.save_manifest(
                run_dir,
                {
                    "phase": "recorded",
                    "agent_name": "agent",
                    "pane_id": "owned-pane",
                    "terminal_id": "owned-terminal",
                    "workspace_id": "owned-workspace",
                    "tab_id": "owned-tab",
                    "workspace_owned": True,
                    "server_started": False,
                },
            )
            args = argparse.Namespace(
                run_dir=str(run_dir), keep_agent=False, keep_server=False
            )
            reused = {
                "pane_id": "other-pane",
                "terminal_id": "other-terminal",
                "workspace_id": "owned-workspace",
                "tab_id": "owned-tab",
            }
            with (
                mock.patch.object(orchestration, "verify_herdr_runtime"),
                mock.patch.object(orchestration, "pane_info", return_value=reused),
            ):
                with self.assertRaises(orchestration.OrchestrationError):
                    orchestration.cleanup(args)

    def test_failure_cleanup_refuses_unrecorded_pane_in_owned_workspace(self) -> None:
        manifest = {
            "workspace_id": "workspace",
            "tab_id": "tab",
            "workspace_owned": True,
            "bootstrap_pane_id": "bootstrap",
            "bootstrap_terminal_id": "bootstrap-terminal",
        }
        unexpected = {
            "pane_id": "foreign",
            "terminal_id": "foreign-terminal",
            "workspace_id": "workspace",
            "tab_id": "tab",
        }
        with (
            mock.patch.object(orchestration, "list_panes", return_value=[unexpected]),
            mock.patch.object(orchestration, "run_herdr") as herdr,
        ):
            with self.assertRaisesRegex(
                orchestration.OrchestrationError, "unexpected pane"
            ):
                orchestration.close_owned_workspace(
                    manifest, require_single_pane=False
                )
        herdr.assert_not_called()

    def test_launch_failure_does_not_reconcile_by_mutable_name(self) -> None:
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
                mock.patch.object(orchestration, "verify_herdr_runtime"),
                mock.patch.object(
                    orchestration,
                    "create_run_workspace",
                    side_effect=orchestration.OrchestrationError("start failed"),
                ),
            ):
                with self.assertRaises(orchestration.OrchestrationError):
                    orchestration.launch(args)
            self.assertEqual(
                orchestration.load_manifest(run_dir)["phase"], "launch_failed"
            )

    def test_launch_owns_topology_and_binds_workspace(self) -> None:
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
            topology = {
                "workspace_id": "workspace",
                "tab_id": "tab",
                "bootstrap_pane_id": "bootstrap",
                "bootstrap_terminal_id": "bootstrap-terminal",
                "workspace_owned": True,
            }
            agent = {
                "name": "agent",
                "pane_id": "pane",
                "terminal_id": "terminal",
                "workspace_id": "workspace",
                "tab_id": "tab",
                "agent_status": "working",
            }
            bootstrap = {
                "pane_id": "bootstrap",
                "terminal_id": "bootstrap-terminal",
                "workspace_id": "workspace",
                "tab_id": "tab",
            }
            started = subprocess.CompletedProcess(
                [], 0, json.dumps({"result": {"agent": agent}}), ""
            )
            closed = subprocess.CompletedProcess([], 0, '{"result":{"type":"ok"}}', "")
            final_layout = {
                "result": {
                    "layout": {
                        "area": {"x": 0, "y": 0, "width": 80, "height": 24},
                        "panes": [
                            {
                                "pane_id": "pane",
                                "rect": {
                                    "x": 0,
                                    "y": 0,
                                    "width": 80,
                                    "height": 24,
                                },
                            }
                        ],
                    }
                }
            }

            def fake_herdr(manifest, argv, **kwargs):
                return started if argv[0:2] == ["agent", "start"] else closed

            with (
                mock.patch.object(orchestration, "verify_herdr_runtime"),
                mock.patch.object(
                    orchestration, "create_run_workspace", return_value=topology
                ),
                mock.patch.object(
                    orchestration,
                    "pane_split_direction",
                    side_effect=[
                        ("right", {"result": {}}),
                        ("right", final_layout),
                    ],
                ),
                mock.patch.object(
                    orchestration, "run_herdr", side_effect=fake_herdr
                ) as herdr,
                mock.patch.object(
                    orchestration,
                    "list_panes",
                    side_effect=[[bootstrap, agent], [agent]],
                ),
                mock.patch.object(orchestration, "read_terminal", return_value="ready"),
                mock.patch.object(
                    orchestration, "refresh_conversation_identity", return_value=False
                ),
                mock.patch("builtins.print"),
            ):
                self.assertEqual(orchestration.launch(args), 0)

            argv = next(
                call.args[1]
                for call in herdr.call_args_list
                if call.args[1][0:2] == ["agent", "start"]
            )
            self.assertEqual(argv[0:3], ["agent", "start", "agent"])
            self.assertIn("--cwd", argv)
            self.assertEqual(argv[argv.index("--cwd") + 1], str(run_dir))
            self.assertEqual(argv[argv.index("--workspace") + 1], "workspace")
            self.assertEqual(argv[argv.index("--tab") + 1], "tab")
            self.assertEqual(argv[argv.index("--split") + 1], "right")
            self.assertIn("--no-focus", argv)
            self.assertIn("--add-dir", argv)
            self.assertEqual(argv[argv.index("--add-dir") + 1], str(run_dir))
            self.assertLess(argv.index("--add-dir"), argv.index("--prompt-interactive"))
            manifest = orchestration.load_manifest(run_dir)
            self.assertEqual(manifest["pane_id"], "pane")
            self.assertEqual(manifest["workspace_id"], "workspace")


if __name__ == "__main__":
    unittest.main()
