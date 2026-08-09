#!/usr/bin/env python3
"""Validate AGY capabilities and initialize one run directory."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


MINIMUM_AGY_VERSION = (1, 1, 9)
VERSION_RE = re.compile(r"(?<!\d)(\d+)\.(\d+)\.(\d+)(?!\d)")
REQUIRED_HELP_TOKENS = (
    "--add-dir",
    "--dangerously-skip-permissions",
    "--json-schema",
    "--output-format",
    "--print-timeout",
)


class PreflightError(RuntimeError):
    """A user-actionable preflight failure."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check AGY and create a ready-to-use run directory."
    )
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument(
        "--runs-root",
        type=Path,
        help="Run directory parent (default: system temp/use-agy-runs).",
    )
    parser.add_argument("--skill", help="Exact native AGY skill slug to check.")
    parser.add_argument("--agy-bin", help="AGY executable path (default: PATH lookup).")
    parser.add_argument("--mode", choices=("plan", "accept-edits"), default="plan")
    parser.add_argument("--json", action="store_true", help="Print one JSON object.")
    parser.add_argument(
        "--command-timeout",
        type=float,
        default=15.0,
        help="Seconds allowed for each read-only AGY probe.",
    )
    return parser.parse_args()


def run_probe(command: list[str], timeout: float) -> str:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PreflightError(f"probe failed: {shlex.join(command)}: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        suffix = detail[0] if detail else f"exit {result.returncode}"
        raise PreflightError(f"probe failed: {shlex.join(command)}: {suffix}")
    # AGY currently writes `--help` to stderr even on a successful exit. Prefer
    # stdout so machine-readable probes such as `/skills` remain valid when a
    # harmless warning is also present on stderr.
    return result.stdout if result.stdout.strip() else result.stderr


def version_tuple(value: str, label: str) -> tuple[int, int, int]:
    match = VERSION_RE.search(value)
    if not match:
        raise PreflightError(f"cannot parse {label} version")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def version_text(value: tuple[int, int, int]) -> str:
    return ".".join(str(part) for part in value)


def parse_skill_slugs(raw_json: str) -> set[str]:
    try:
        envelope = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise PreflightError(f"/skills returned invalid JSON: {exc}") from exc
    if envelope.get("status") != "SUCCESS":
        raise PreflightError(f"/skills status is {envelope.get('status')!r}")
    response = envelope.get("response")
    if not isinstance(response, str):
        raise PreflightError("/skills response is not text")
    return {
        line.split("\t", 1)[0]
        for line in response.splitlines()
        if "\t" in line and line.split("\t", 1)[0]
    }


def shell_command(parts: list[str]) -> str:
    return shlex.join(parts)


def initialize_run(
    workspace: Path,
    runs_root: Path,
    skill: str | None,
    native_skill: bool | None,
) -> tuple[Path, Path]:
    runs_root.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix="agy-", dir=runs_root)).resolve()
    handoff = run_dir / "handoff.md"
    prefix = f"/{skill}\n" if skill and native_skill else ""
    handoff.write_text(
        prefix
        + "GOAL\n\n"
        + "FACTS\n\n"
        + "UNKNOWNS\n\n"
        + "CONSTRAINTS\n\n"
        + "ALLOWED EFFECTS\n\n"
        + "DONE WHEN\n",
        encoding="utf-8",
    )
    return run_dir, handoff


def build_result(args: argparse.Namespace) -> dict[str, Any]:
    workspace = args.workspace.expanduser().resolve()
    if not workspace.is_dir():
        raise PreflightError(f"workspace is not a directory: {workspace}")
    if args.skill and ("\n" in args.skill or "\r" in args.skill or args.skill.startswith("/")):
        raise PreflightError("--skill must be an exact slug without a leading slash")

    discovered = args.agy_bin or shutil.which("agy")
    if not discovered:
        raise PreflightError("agy is not available on PATH")
    agy_bin = str(Path(discovered).expanduser().resolve())

    installed_raw = run_probe([agy_bin, "--version"], args.command_timeout)
    installed = version_tuple(installed_raw, "installed AGY")
    if installed < MINIMUM_AGY_VERSION:
        raise PreflightError(
            f"AGY {version_text(installed)} is too old; need >= "
            f"{version_text(MINIMUM_AGY_VERSION)}"
        )

    help_text = run_probe([agy_bin, "--help"], args.command_timeout)
    missing = [token for token in REQUIRED_HELP_TOKENS if token not in help_text]
    if missing:
        raise PreflightError(f"AGY is missing required flags: {', '.join(missing)}")

    warnings: list[str] = []
    latest: tuple[int, int, int] | None = None
    try:
        changelog = run_probe([agy_bin, "changelog"], args.command_timeout)
        latest = version_tuple(changelog, "latest AGY")
    except PreflightError as exc:
        warnings.append(f"latest-version check unavailable: {exc}")

    native_skill: bool | None = None
    if args.skill:
        skills_json = run_probe(
            [agy_bin, "-p", "/skills", "--output-format", "json"],
            args.command_timeout,
        )
        native_skill = args.skill in parse_skill_slugs(skills_json)

    runs_root = (
        args.runs_root.expanduser().resolve()
        if args.runs_root
        else Path(tempfile.gettempdir()).resolve() / "use-agy-runs"
    )
    run_dir, handoff = initialize_run(workspace, runs_root, args.skill, native_skill)
    skill_dir = Path(__file__).resolve().parent.parent
    runner = skill_dir / "scripts" / "run-agy.py"
    summarizer = skill_dir / "scripts" / "summarize-agy-stream.py"

    prompt_placeholder = "__USE_AGY_HANDOFF_PROMPT__"
    prompt_expression = f'"$(<{shlex.quote(str(handoff))})"'
    run_parts = [
        sys.executable,
        str(runner),
        "--events",
        str(run_dir / "events.ndjson"),
        "--stderr",
        str(run_dir / "stderr.txt"),
        "--liveness-timeout",
        "180",
        "--heartbeat-interval",
        "30",
        "--overall-timeout",
        "930",
        "--",
        agy_bin,
        "-p",
        prompt_placeholder,
        "--add-dir",
        str(workspace),
        "--mode",
        args.mode,
        "--dangerously-skip-permissions",
        "--output-format",
        "stream-json",
        "--json-schema",
        str(skill_dir / "handoff.schema.json"),
        "--print-timeout",
        "15m",
        "--log-file",
        str(run_dir / "cli.log"),
    ]
    # Insert one intentionally quoted command substitution after safely quoting
    # every ordinary argument. The quotes preserve the whole handoff as one arg.
    run_command = shell_command(run_parts).replace(prompt_placeholder, prompt_expression)
    summarize_command = shell_command(
        [
            sys.executable,
            str(summarizer),
            "--events",
            str(run_dir / "events.ndjson"),
            "--stderr",
            str(run_dir / "stderr.txt"),
            "--summary-out",
            str(run_dir / "ordered-summary.json"),
            "--report-out",
            str(run_dir / "report.md"),
        ]
    )

    result: dict[str, Any] = {
        "status": "ok",
        "agy_bin": agy_bin,
        "installed_version": version_text(installed),
        "latest_version": version_text(latest) if latest else None,
        "update_available": latest is not None and installed < latest,
        "workspace": str(workspace),
        "run_dir": str(run_dir),
        "handoff": str(handoff),
        "mode": args.mode,
        "requested_skill": args.skill,
        "native_skill_available": native_skill,
        "run_command": run_command,
        "summarize_command": summarize_command,
        "warnings": warnings,
    }
    (run_dir / "preflight.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return result


def print_human(result: dict[str, Any]) -> None:
    latest = result["latest_version"] or "unknown"
    update = "yes" if result["update_available"] else "no"
    print("PREFLIGHT_OK")
    print(f"RUN_DIR={result['run_dir']}")
    print(f"HANDOFF={result['handoff']}")
    print(f"AGY_VERSION={result['installed_version']}")
    print(f"AGY_LATEST={latest}")
    print(f"UPDATE_AVAILABLE={update}")
    if result["requested_skill"]:
        native = "yes" if result["native_skill_available"] else "no"
        print(f"NATIVE_SKILL={native}")
    for warning in result["warnings"]:
        print(f"WARNING={warning}")
    print(f"RUN={result['run_command']}")
    print(f"SUMMARIZE={result['summarize_command']}")


def main() -> int:
    args = parse_args()
    try:
        result = build_result(args)
    except PreflightError as exc:
        if args.json:
            print(json.dumps({"status": "error", "error": str(exc)}))
        else:
            print(f"PREFLIGHT_ERROR={exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print_human(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
