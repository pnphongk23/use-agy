#!/usr/bin/env python3
"""Classify an AGY run and optionally append a redacted observation."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path


PATTERNS = [
    (
        "unsafe_permission_allow",
        ("risk_unexpectedly_allowed", "rm_unexpectedly_allowed"),
    ),
    ("onboarding", ("choose your color scheme", "choose your theme")),
    ("quota", ("resource has been exhausted", "quota exceeded")),
    ("capacity", ("no capacity available", "high traffic", "code 503", "unavailable")),
    ("sandbox", ("sandbox_command_blocked", "operation not permitted")),
    ("workspace", ("inside the scratch directory", "not a git repository")),
    (
        "permission",
        (
            "permission denied",
            "waiting for approval",
            "permission request",
            "headless mode cannot prompt",
            "auto-denied",
            "required the \"command\" permission",
        ),
    ),
    ("auth", ("not authenticated", "authentication required", "please log in")),
    ("adapter_empty", ("plannerresponse without modifiedresponse",)),
    ("transient", ("connection reset", "temporary failure", "deadline exceeded")),
]


def classify(
    log_text: str,
    stdout: str,
    exit_code: int | None,
    verified_interactive: bool = False,
    interactive_status: str | None = None,
) -> tuple[str, list[str]]:
    if verified_interactive and interactive_status == "partial":
        return "partial", []
    if verified_interactive and interactive_status == "blocked":
        return "blocked", []
    if verified_interactive and interactive_status == "done":
        return "success", []
    text = f"{log_text}\n{stdout}".lower()
    for name, needles in PATTERNS:
        hits = [needle for needle in needles if needle in text]
        if hits:
            if name == "auth" and "authenticated successfully" in text:
                continue
            if name == "adapter_empty" and stdout.strip():
                continue
            return name, hits[:3]
    if exit_code == 0 and stdout.strip():
        return "success", []
    if exit_code == 0:
        return "empty", []
    return "unknown", []


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", required=True)
    parser.add_argument("--stdout-file")
    parser.add_argument("--exit-code", type=int)
    parser.add_argument(
        "--verified-interactive",
        action="store_true",
        help="Record a supervisor-verified bounded job in a still-running interactive TUI.",
    )
    parser.add_argument("--interactive-status", choices=("done", "partial", "blocked"))
    parser.add_argument("--model", default="unknown")
    parser.add_argument("--job", default="unknown")
    parser.add_argument("--elapsed-seconds", type=float)
    parser.add_argument("--record", action="store_true")
    parser.add_argument(
        "--ledger",
        default=str(
            Path(__file__).resolve().parents[1] / "evals/runtime-observations.jsonl"
        ),
    )
    args = parser.parse_args()

    if args.verified_interactive and args.exit_code is not None:
        parser.error("--verified-interactive cannot be combined with --exit-code")
    if args.interactive_status and not args.verified_interactive:
        parser.error("--interactive-status requires --verified-interactive")
    if args.verified_interactive and not args.interactive_status:
        parser.error("--verified-interactive requires --interactive-status")
    if not args.verified_interactive and args.exit_code is None:
        parser.error("--exit-code is required unless --verified-interactive is used")

    log_text = (
        Path(args.log).read_text(errors="replace") if Path(args.log).exists() else ""
    )
    stdout = ""
    if args.stdout_file and Path(args.stdout_file).exists():
        stdout = Path(args.stdout_file).read_text(errors="replace")
    if args.verified_interactive and not stdout.strip():
        parser.error("--verified-interactive requires a non-empty --stdout-file")
    result, markers = classify(
        log_text,
        stdout,
        args.exit_code,
        verified_interactive=args.verified_interactive,
        interactive_status=args.interactive_status,
    )
    fingerprint_input = "\0".join(
        (args.model, args.job, str(args.exit_code), log_text, stdout)
    ).encode("utf-8", errors="replace")
    observation = {
        "timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "model": args.model,
        "job": args.job,
        "exit_code": args.exit_code,
        "elapsed_seconds": args.elapsed_seconds,
        "classification": result,
        "handoff_status": args.interactive_status,
        "stdout_nonempty": bool(stdout.strip()),
        "evidence_markers": markers,
        "run_fingerprint": hashlib.sha256(fingerprint_input).hexdigest()[:16],
    }
    print(json.dumps(observation, ensure_ascii=True, sort_keys=True))
    if args.record:
        ledger = Path(args.ledger)
        ledger.parent.mkdir(parents=True, exist_ok=True)
        existing = set()
        if ledger.exists():
            for line in ledger.read_text(errors="replace").splitlines():
                try:
                    fingerprint = json.loads(line).get("run_fingerprint")
                except json.JSONDecodeError:
                    continue
                if fingerprint:
                    existing.add(fingerprint)
        if observation["run_fingerprint"] in existing:
            return 0
        with ledger.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(observation, ensure_ascii=True, sort_keys=True) + "\n"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
