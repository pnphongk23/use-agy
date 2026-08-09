#!/usr/bin/env python3
"""Watchdog runner for AGY.

Monitors liveness and overall timeouts, terminates the process group on stall,
and ensures a terminal result is written.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any


def parse_args(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    if "--" not in argv:
        print("Error: Command separator '--' is required.", file=sys.stderr)
        sys.exit(1)

    idx = argv.index("--")
    our_args = argv[:idx]
    cmd_args = argv[idx + 1 :]

    parser = argparse.ArgumentParser(description="Watchdog runner for AGY")
    parser.add_argument("--events", required=True, help="Path to write events.ndjson")
    parser.add_argument("--stderr", required=True, help="Path to write stderr.txt")
    parser.add_argument(
        "--liveness-timeout",
        type=float,
        default=180,
        help="Active-tool no-event timeout in seconds (default 180)",
    )
    parser.add_argument(
        "--overall-timeout",
        type=float,
        default=930,
        help="Hard timeout in seconds (default 930; keep above AGY print timeout)",
    )
    parser.add_argument(
        "--heartbeat-interval",
        type=float,
        default=30,
        help="Compact status heartbeat interval in seconds (default 30)",
    )

    parsed = parser.parse_args(our_args)
    for name in ("liveness_timeout", "overall_timeout", "heartbeat_interval"):
        if getattr(parsed, name) <= 0:
            parser.error(f"--{name.replace('_', '-')} must be greater than zero")
    return parsed, cmd_args


def stdout_reader(pipe: Any, q: queue.Queue[bytes | None]) -> None:
    try:
        for line in pipe:
            q.put(line)
    except Exception:
        pass
    finally:
        q.put(None)


def stderr_reader(pipe: Any, filepath: Path) -> None:
    try:
        with open(filepath, "wb") as f:
            for line in pipe:
                f.write(line)
                f.flush()
    except Exception:
        pass


def terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    """Terminates the process group of the given process to clean up all children."""
    pid = process.pid
    try:
        pgid = os.getpgid(pid)
        os.killpg(pgid, signal.SIGTERM)
    except Exception:
        try:
            process.terminate()
        except Exception:
            pass

    # Bounded wait for termination
    for _ in range(50):
        if process.poll() is not None:
            return
        time.sleep(0.1)

    try:
        pgid = os.getpgid(pid)
        os.killpg(pgid, signal.SIGKILL)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass


def write_synthetic_result(
    events_path: Path,
    conversation_id: str | None,
    status: str,
    error_msg: str,
) -> None:
    result_event = {
        "event": "result",
        "result": {
            "conversation_id": conversation_id,
            "status": status,
            "error": error_msg,
            "duration_seconds": 0,
            "num_turns": 0,
            "usage": {},
        },
        "timestamp": time.time(),
    }
    try:
        with open(events_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(result_event, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"Failed to write synthetic terminal result: {e}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    args, cmd_args = parse_args(argv)

    events_path = Path(args.events)
    stderr_path = Path(args.stderr)

    events_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path.parent.mkdir(parents=True, exist_ok=True)

    # Initialize empty files or truncate existing
    events_path.write_bytes(b"")
    stderr_path.write_bytes(b"")

    if not cmd_args:
        print("Error: command after '--' is required.", file=sys.stderr)
        write_synthetic_result(events_path, None, "ERROR", "missing subprocess command")
        return 1

    # Never echo the prompt or command arguments: they can be large or sensitive.
    print(
        f"[*] Starting AGY subprocess: executable={Path(cmd_args[0]).name} "
        f"arg_count={len(cmd_args) - 1}",
        flush=True,
    )

    popen_kwargs: dict[str, Any] = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "bufsize": 0,  # Unbuffered
    }

    # Create an owned process group/session so cleanup cannot target unrelated work.
    if sys.platform != "win32":
        popen_kwargs["start_new_session"] = True

    try:
        process = subprocess.Popen(cmd_args, **popen_kwargs)
    except Exception as e:
        print(f"[-] Failed to start subprocess: {e}", file=sys.stderr)
        write_synthetic_result(events_path, None, "ERROR", f"Failed to start subprocess: {e}")
        return 1

    stdout_q: queue.Queue[bytes | None] = queue.Queue()

    t_stdout = threading.Thread(
        target=stdout_reader,
        args=(process.stdout, stdout_q),
        daemon=True,
    )
    t_stderr = threading.Thread(
        target=stderr_reader,
        args=(process.stderr, stderr_path),
        daemon=True,
    )

    t_stdout.start()
    t_stderr.start()

    start_time = time.monotonic()
    last_activity_time = start_time
    last_heartbeat_time = start_time
    active_tools: set[tuple[int, str]] = set()
    conversation_id: str | None = None
    terminal_result_received = False
    agy_status: str | None = None
    exit_reason = ""

    with open(events_path, "ab") as events_file:
        while True:
            now = time.monotonic()
            elapsed_overall = now - start_time
            if elapsed_overall >= args.overall_timeout:
                exit_reason = f"overall timeout exceeded ({args.overall_timeout:g}s)"
                break

            elapsed_liveness = now - last_activity_time
            if active_tools and elapsed_liveness >= args.liveness_timeout:
                exit_reason = (
                    f"liveness timeout ({args.liveness_timeout:g}s) "
                    f"with active tools: {sorted(active_tools)}"
                )
                break

            if now - last_heartbeat_time >= args.heartbeat_interval:
                state = (
                    "active_tools="
                    + ",".join(
                        f"{index}:{name}" for index, name in sorted(active_tools)
                    )
                    if active_tools
                    else "reasoning_or_idle"
                )
                print(
                    f"[heartbeat] elapsed={elapsed_overall:.1f}s "
                    f"last_event_age={elapsed_liveness:.1f}s state={state}",
                    flush=True,
                )
                last_heartbeat_time = now

            wait_candidates = [
                args.overall_timeout - elapsed_overall,
                args.heartbeat_interval - (now - last_heartbeat_time),
            ]
            if active_tools:
                wait_candidates.append(args.liveness_timeout - elapsed_liveness)
            wait_timeout = max(0.1, min(wait_candidates))

            try:
                line = stdout_q.get(timeout=max(0.1, wait_timeout))
            except queue.Empty:
                if process.poll() is not None:
                    # Process completed and queue is empty
                    break
                continue

            if line is None:
                # EOF reached
                break

            events_file.write(line)
            events_file.flush()

            # We got activity, update the liveness clock
            last_activity_time = time.monotonic()

            try:
                event_str = line.decode("utf-8", errors="replace").strip()
                if not event_str:
                    continue
                event = json.loads(event_str)
                event_name = event.get("event")

                if event_name == "init":
                    init = event.get("init") if isinstance(event.get("init"), dict) else {}
                    conversation_id = (
                        event.get("conversation_id")
                        or init.get("conversation_id")
                        or conversation_id
                    )
                    model = init.get("model")
                    print(f"[*] AGY initialized with model: {model}", flush=True)

                elif event_name == "step_update":
                    step = (
                        event.get("step_update")
                        if isinstance(event.get("step_update"), dict)
                        else event
                    )
                    conversation_id = step.get("conversation_id") or conversation_id
                    state = step.get("state")
                    tool_name = step.get("tool_name") or (step.get("tool_info") or {}).get("name")
                    step_index = step.get("step_index")

                    if state == "ACTIVE" and tool_name:
                        active_tools.add((step_index, tool_name))
                        print(
                            f"[*] Tool ACTIVE: {tool_name} (step {step_index})",
                            flush=True,
                        )
                    elif state == "DONE" and tool_name:
                        active_tools.discard((step_index, tool_name))
                        print(
                            f"[*] Tool DONE: {tool_name} (step {step_index})",
                            flush=True,
                        )

                elif event_name == "result":
                    result = (
                        event.get("result")
                        if isinstance(event.get("result"), dict)
                        else event
                    )
                    conversation_id = result.get("conversation_id") or conversation_id
                    agy_status = result.get("status")
                    terminal_result_received = True
                    print(f"[*] Received terminal result: status={agy_status}", flush=True)
                    # Stop waiting immediately on terminal result
                    break

            except Exception:
                # Malformed JSON, skip parsing but keep event in file
                pass

    # Clean up subprocess
    if process.poll() is None:
        if exit_reason:
            print(f"[-] Terminating process group: {exit_reason}", file=sys.stderr, flush=True)
        else:
            print("[*] Cleaning up running process group", flush=True)
        terminate_process_group(process)

    # Wait for reader threads to complete
    t_stdout.join(timeout=2.0)
    t_stderr.join(timeout=2.0)

    # Resolve exit code
    try:
        exit_code = process.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        terminate_process_group(process)
        exit_code = process.wait(timeout=2.0)

    if exit_reason:
        # We timed out or stalled, ensure we write a terminal result with ERROR status
        print(f"[-] Watchdog termination: {exit_reason}", file=sys.stderr, flush=True)
        write_synthetic_result(events_path, conversation_id, "ERROR", exit_reason)
        return 1

    if not terminal_result_received:
        # Subprocess exited without emitting a result event
        print(
            "[-] Process exited without terminal result event",
            file=sys.stderr,
            flush=True,
        )
        write_synthetic_result(
            events_path,
            conversation_id,
            "ERROR",
            f"Process exited prematurely with code {exit_code}",
        )
        return 1

    if agy_status != "SUCCESS":
        print(f"[-] Run failed with status: {agy_status}", file=sys.stderr, flush=True)
        return 1

    print("[+] Run completed successfully.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
