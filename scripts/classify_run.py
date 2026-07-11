#!/usr/bin/env python3
"""Classify an AGY run and optionally append a redacted observation."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path


PATTERNS = [
    ("quota", ("resource has been exhausted", "quota exceeded")),
    ("capacity", ("no capacity available", "high traffic", "code 503", "unavailable")),
    ("sandbox", ("sandbox_command_blocked", "operation not permitted")),
    ("permission", ("permission denied", "waiting for approval", "permission request")),
    ("auth", ("not authenticated", "authentication required", "please log in")),
    ("adapter_empty", ("plannerresponse without modifiedresponse",)),
    ("transient", ("connection reset", "temporary failure", "deadline exceeded")),
]


def classify(log_text: str, stdout: str, exit_code: int) -> tuple[str, list[str]]:
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
    parser.add_argument("--exit-code", required=True, type=int)
    parser.add_argument("--model", default="unknown")
    parser.add_argument("--job", default="unknown")
    parser.add_argument("--elapsed-seconds", type=float)
    parser.add_argument("--record", action="store_true")
    parser.add_argument(
        "--ledger",
        default=str(Path(__file__).resolve().parents[1] / "evals/runtime-observations.jsonl"),
    )
    args = parser.parse_args()

    log_text = Path(args.log).read_text(errors="replace") if Path(args.log).exists() else ""
    stdout = ""
    if args.stdout_file and Path(args.stdout_file).exists():
        stdout = Path(args.stdout_file).read_text(errors="replace")
    result, markers = classify(log_text, stdout, args.exit_code)
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
            handle.write(json.dumps(observation, ensure_ascii=True, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
