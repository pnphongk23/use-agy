#!/usr/bin/env python3
"""Stateful Herdr orchestration for supervised AGY sessions.

The script automates lifecycle and evidence capture. It deliberately never
answers trust, login, consent, or permission prompts.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid


DEFAULT_MODEL = "Gemini 3.5 Flash (Medium)"
SMOKE_PROMPT = "Reply with exactly AGY_OK and nothing else."
ATTENTION_PATTERNS = (
    "do you trust the contents of this project",
    "requesting permission for",
    "allow access to this file",
    "choose your color scheme",
    "choose your theme",
    "please log in",
    "authentication required",
    "enable telemetry?",
    "accept the terms of service",
    "do you agree to the privacy",
)
HANDOFF_PATTERN = re.compile(r"(?mi)^\s*STATUS:\s*(done|partial|blocked)\s*$")


class OrchestrationError(RuntimeError):
    pass


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def run(
    argv: list[str],
    *,
    cwd: Path | None = None,
    timeout: float | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        argv,
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if check and result.returncode != 0:
        raise OrchestrationError(
            f"command failed ({result.returncode}): {safe_command(argv)}\n"
            f"stdout: {result.stdout[-2000:]}\n"
            f"stderr: {result.stderr[-2000:]}"
        )
    return result


def safe_command(argv: list[str]) -> str:
    redacted = list(argv)
    if "--prompt-interactive" in redacted:
        index = redacted.index("--prompt-interactive") + 1
        if index < len(redacted):
            redacted[index] = "<WORK_ORDER>"
    if redacted[:3] == ["herdr", "pane", "run"] and len(redacted) > 4:
        redacted[4] = "<WORK_ORDER>"
    return " ".join(redacted)


def parse_json_output(text: str) -> dict:
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise OrchestrationError("command returned no JSON object")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def symlink_ancestor(path: Path, workspace: Path) -> Path | None:
    try:
        relative = path.relative_to(workspace)
    except ValueError:
        return None
    current = workspace
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            return current
    return None


def hash_symlink(path: Path, workspace: Path) -> dict[str, str]:
    relative = str(path.relative_to(workspace))
    return {
        relative: "symlink:"
        + sha256_bytes(os.readlink(path).encode("utf-8", errors="surrogateescape"))
    }


def hash_path(path: Path, workspace: Path) -> dict[str, str]:
    """Hash files below path without following symlinks outside the workspace."""
    result: dict[str, str] = {}
    ancestor = symlink_ancestor(path, workspace)
    if ancestor is not None:
        result.update(hash_symlink(ancestor, workspace))
        return result
    if path.is_file():
        result[str(path.relative_to(workspace))] = sha256_file(path)
        return result
    if not path.is_dir():
        return result
    for root, directories, files in os.walk(path, followlinks=False):
        root_path = Path(root)
        kept_directories = []
        for name in sorted(directories):
            candidate = root_path / name
            if candidate.is_symlink():
                result.update(hash_symlink(candidate, workspace))
            else:
                kept_directories.append(name)
        directories[:] = kept_directories
        for name in sorted(files):
            candidate = root_path / name
            if candidate.is_symlink():
                result.update(hash_symlink(candidate, workspace))
            else:
                result[str(candidate.relative_to(workspace))] = sha256_file(candidate)
    return result


def lexical_scoped_path(workspace: Path, value: str) -> Path | None:
    candidate = Path(os.path.abspath(workspace / value))
    if candidate == workspace or workspace in candidate.parents:
        return candidate
    return None


def manifest_evidence_scopes(manifest: dict) -> list[str]:
    """Return baseline hashing scopes, including legacy run manifests."""
    value = manifest.get("evidence_scopes", manifest.get("scopes", []))
    return value if isinstance(value, list) else []


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_manifest(run_dir: Path) -> dict:
    path = run_dir / "manifest.json"
    if not path.exists():
        raise OrchestrationError(f"missing manifest: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def save_manifest(run_dir: Path, manifest: dict) -> None:
    manifest["updated_at"] = utc_now()
    write_json(run_dir / "manifest.json", manifest)


@contextmanager
def run_lock(run_dir: Path):
    lock_path = run_dir / ".orchestration.lock"
    with lock_path.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def herdr_server_running() -> bool:
    result = run(["herdr", "status"], check=False)
    return result.returncode == 0 and "status: running" in result.stdout.lower()


def process_identity(pid: int) -> str | None:
    result = run(["ps", "-p", str(pid), "-o", "lstart=", "-o", "command="], check=False)
    value = result.stdout.strip()
    return sha256_bytes(value.encode()) if result.returncode == 0 and value else None


def start_herdr_server(run_dir: Path) -> tuple[int, str | None]:
    log_path = run_dir / "herdr-server.log"
    with log_path.open("ab") as log_handle:
        process = subprocess.Popen(
            ["herdr", "server"],
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise OrchestrationError(
                f"Herdr server exited early with code {process.returncode}"
            )
        if herdr_server_running():
            return process.pid, process_identity(process.pid)
        time.sleep(0.25)
    process.send_signal(signal.SIGINT)
    raise OrchestrationError("Herdr server did not become ready")


def stop_herdr_server(pid: int | None, identity: str | None = None) -> None:
    if isinstance(pid, int):
        if identity is not None and process_identity(pid) != identity:
            return
        try:
            os.kill(pid, signal.SIGINT)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and herdr_server_running():
        time.sleep(0.2)


def git_baseline(workspace: Path, scopes: list[str]) -> dict:
    workspace = workspace.resolve()
    inside = run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=workspace,
        check=False,
    )
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        scoped: dict[str, str] = {}
        for scope in scopes:
            candidate = lexical_scoped_path(workspace, scope)
            if candidate is not None:
                scoped.update(hash_path(candidate, workspace))
        return {
            "kind": "directory",
            "captured_at": utc_now(),
            "scoped_sha256": scoped,
            "scope_required_for_evidence": not bool(scopes),
        }

    status = run(["git", "status", "--porcelain=v1", "-z"], cwd=workspace).stdout
    tracked = run(["git", "diff", "--binary"], cwd=workspace).stdout.encode()
    staged = run(["git", "diff", "--cached", "--binary"], cwd=workspace).stdout.encode()

    untracked: dict[str, str] = {}
    scope_roots = [
        root
        for scope in scopes
        if (root := lexical_scoped_path(workspace, scope)) is not None
    ]
    entries = status.split("\0")
    for entry in entries:
        if not entry.startswith("?? "):
            continue
        relative = entry[3:]
        candidate = lexical_scoped_path(workspace, relative)
        if candidate is None:
            continue
        if scope_roots and not any(
            candidate == root or root in candidate.parents for root in scope_roots
        ):
            continue
        untracked.update(hash_path(candidate, workspace))

    return {
        "kind": "git",
        "captured_at": utc_now(),
        "status_sha256": sha256_bytes(status.encode()),
        "tracked_diff_sha256": sha256_bytes(tracked),
        "staged_diff_sha256": sha256_bytes(staged),
        "untracked_sha256": untracked,
    }


def agent_info(name: str) -> dict:
    result = run(["herdr", "agent", "get", name], check=False)
    if result.returncode != 0:
        raise OrchestrationError(f"Herdr agent not found: {name}")
    payload = parse_json_output(result.stdout)
    return payload.get("result", {}).get("agent", {})


def list_agents() -> list[dict]:
    result = run(["herdr", "agent", "list"], check=False)
    if result.returncode != 0:
        raise OrchestrationError("cannot enumerate Herdr agents")
    payload = parse_json_output(result.stdout)
    agents = payload.get("result", {}).get("agents", [])
    return agents if isinstance(agents, list) else []


def is_owned_agent(info: dict, manifest: dict) -> bool:
    expected = (
        manifest.get("agent_name"),
        manifest.get("pane_id"),
        manifest.get("terminal_id"),
    )
    actual = (info.get("name"), info.get("pane_id"), info.get("terminal_id"))
    return all(expected) and actual == expected


def herdr_runtime_in_use() -> bool:
    if list_agents():
        return True
    result = run(["herdr", "pane", "list"], check=False)
    if result.returncode != 0:
        return True
    try:
        payload = parse_json_output(result.stdout)
    except OrchestrationError:
        return True
    panes = payload.get("result", {}).get("panes")
    return bool(panes) if isinstance(panes, list) else True


def read_terminal(name: str, lines: int = 180, source: str = "recent-unwrapped") -> str:
    result = run(
        [
            "herdr",
            "agent",
            "read",
            name,
            "--source",
            source,
            "--lines",
            str(lines),
        ],
        check=False,
    )
    if result.returncode != 0:
        return result.stdout + result.stderr
    payload = parse_json_output(result.stdout)
    return payload.get("result", {}).get("read", {}).get("text", result.stdout)


def needs_attention(terminal: str) -> str | None:
    lowered = terminal.lower()
    for pattern in ATTENTION_PATTERNS:
        if pattern in lowered:
            return pattern
    return None


def handoff_status(terminal: str, token: str | None = None) -> str | None:
    if token is None:
        match = HANDOFF_PATTERN.search(terminal)
        return match.group(1).lower() if match else None
    begin_matches = list(
        re.finditer(rf"(?m)^HANDOFF_BEGIN:\s*{re.escape(token)}\s*$", terminal)
    )
    if not begin_matches:
        return None
    begin = begin_matches[-1]
    end = re.search(
        rf"(?m)^RUN_TOKEN:\s*{re.escape(token)}\s*$", terminal[begin.end() :]
    )
    if end is None:
        return None
    block = terminal[begin.end() : begin.end() + end.start()]
    statuses = list(HANDOFF_PATTERN.finditer(block))
    return statuses[-1].group(1).lower() if statuses else None


def has_handoff(terminal: str, token: str | None = None) -> bool:
    return handoff_status(terminal, token) is not None


def prompt_with_token(prompt: str, token: str) -> str:
    return (
        prompt
        + "\n\nFINAL HANDOFF REQUIREMENT: Wrap the final handoff in two marker lines. "
        + "Build the opening marker by joining HANDOFF_BEGIN: with one space and "
        + "this token: "
        + token
        + ". Build the closing marker by joining RUN_TOKEN: with one space and "
        + "the same token: "
        + token
        + ". Put the structured STATUS and fields between those markers. Do not "
        + "quote this instruction."
    )


def baseline_signature(value: dict) -> dict:
    return {key: item for key, item in value.items() if key != "captured_at"}


def appended_terminal(before: str, current: str) -> str:
    if before and current.startswith(before):
        return current[len(before) :]
    if before and before in current:
        return current.rsplit(before, 1)[1]
    limit = min(len(before), len(current))
    for size in range(limit, 0, -1):
        if before.endswith(current[:size]):
            return current[size:]
    return current


def record_smoke(
    run_dir: Path, log_path: Path, stdout_path: Path, exit_code: int, model: str
) -> None:
    classifier = Path(__file__).with_name("classify_run.py")
    if not classifier.exists():
        return
    run(
        [
            sys.executable,
            str(classifier),
            "--log",
            str(log_path),
            "--stdout-file",
            str(stdout_path),
            "--exit-code",
            str(exit_code),
            "--model",
            model,
            "--job",
            "smoke",
            "--record",
        ],
        cwd=run_dir,
        check=False,
    )


def prepare(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).expanduser().resolve()
    if not workspace.is_dir():
        raise OrchestrationError(f"workspace is not a directory: {workspace}")
    if not shutil.which("agy") or not shutil.which("herdr"):
        raise OrchestrationError("both agy and herdr must be installed")

    run_dir = (
        Path(args.run_dir).expanduser().resolve()
        if args.run_dir
        else Path("/tmp") / f"use-agy-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    )
    run_dir.mkdir(parents=True, exist_ok=False)

    agy_version = run(["agy", "--version"]).stdout.strip()
    herdr_version = run(["herdr", "--version"]).stdout.strip()
    models = run(["agy", "models"]).stdout.splitlines()
    if args.model not in {line.strip() for line in models}:
        raise OrchestrationError(f"model is not listed by AGY: {args.model}")

    server_started = False
    server_pid = None
    server_identity = None
    if not herdr_server_running():
        server_pid, server_identity = start_herdr_server(run_dir)
        server_started = True

    started = time.monotonic()
    model_candidates = [args.model]
    fallback = "Gemini 3.5 Flash (Low)"
    if not args.no_model_fallback and fallback in models and fallback != args.model:
        model_candidates.append(fallback)
    selected_model = None
    smoke = None
    manifest_durable = False
    try:
        for index, model in enumerate(model_candidates, start=1):
            smoke_log = run_dir / f"smoke-{index}.log"
            stdout_path = run_dir / f"smoke-{index}.stdout"
            smoke = run(
                [
                    "agy",
                    "--model",
                    model,
                    "-p",
                    SMOKE_PROMPT,
                    "--print-timeout",
                    "45s",
                    "--log-file",
                    str(smoke_log),
                ],
                cwd=workspace,
                timeout=55,
                check=False,
            )
            stdout_path.write_text(smoke.stdout, encoding="utf-8")
            (run_dir / f"smoke-{index}.stderr").write_text(
                smoke.stderr, encoding="utf-8"
            )
            record_smoke(run_dir, smoke_log, stdout_path, smoke.returncode, model)
            if smoke.returncode == 0 and smoke.stdout.strip() == "AGY_OK":
                selected_model = model
                break
        if selected_model is None or smoke is None:
            raise OrchestrationError("no Gemini 3.5 smoke-test candidate passed")

        agent_name = args.agent_name or f"agy-{workspace.name}-{uuid.uuid4().hex[:6]}"
        evidence_scopes = getattr(
            args, "evidence_scope", getattr(args, "scope", [])
        )
        baseline = git_baseline(workspace, evidence_scopes)
        write_json(run_dir / "baseline-1.json", baseline)
        manifest = {
            "schema_version": 1,
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "phase": "prepared",
            "workspace": str(workspace),
            "run_dir": str(run_dir),
            "agent_name": agent_name,
            "model": selected_model,
            "agy_version": agy_version,
            "herdr_version": herdr_version,
            "server_started": server_started,
            "server_pid": server_pid,
            "server_identity": server_identity,
            "smoke_elapsed_seconds": round(time.monotonic() - started, 3),
            "job_sequence": 1,
            "baseline_file": "baseline-1.json",
            "evidence_scopes": evidence_scopes,
        }
        save_manifest(run_dir, manifest)
        manifest_durable = True
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 0
    except BaseException:
        if server_started and not manifest_durable:
            stop_herdr_server(server_pid, server_identity)
        raise


def launch(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir).expanduser().resolve()
    manifest = load_manifest(run_dir)
    if manifest["phase"] != "prepared":
        raise OrchestrationError(f"cannot launch from phase {manifest['phase']}")
    prompt = Path(args.prompt_file).read_text(encoding="utf-8").strip()
    if not prompt:
        raise OrchestrationError("work-order prompt is empty")

    name = manifest["agent_name"]
    if any(agent.get("name") == name for agent in list_agents()):
        raise OrchestrationError(f"agent name already exists: {name}")

    job_log = run_dir / f"job-{manifest['job_sequence']}.log"
    token = uuid.uuid4().hex
    prompt = prompt_with_token(prompt, token)
    manifest.update(
        {
            "phase": "launch_pending",
            "mode": args.mode,
            "job_log": str(job_log),
            "job_token": token,
            "seen_working": False,
            "job_log_offset": 0,
        }
    )
    save_manifest(run_dir, manifest)
    argv = [
        "herdr",
        "agent",
        "start",
        name,
        "--cwd",
        manifest["workspace"],
        "--",
        "agy",
        "--model",
        manifest["model"],
        "--add-dir",
        manifest["workspace"],
        "--prompt-interactive",
        prompt,
        "--mode",
        args.mode,
    ]
    if not args.no_sandbox:
        argv.append("--sandbox")
    argv.extend(["--log-file", str(job_log)])
    started_pane_id = None
    try:
        result = run(argv, cwd=Path(manifest["workspace"]))
        payload = parse_json_output(result.stdout)
        info = payload.get("result", {}).get("agent", {})
        started_pane_id = info.get("pane_id")
        if (
            info.get("name") != name
            or not info.get("pane_id")
            or not info.get("terminal_id")
        ):
            raise OrchestrationError("Herdr start returned incomplete ownership data")
        manifest.update(
            {
                "phase": "launched",
                "pane_id": info["pane_id"],
                "terminal_id": info.get("terminal_id"),
            }
        )
        save_manifest(run_dir, manifest)
        (run_dir / f"launch-{manifest['job_sequence']}.json").write_text(
            result.stdout, encoding="utf-8"
        )
        terminal = read_terminal(name, lines=80)
        (run_dir / f"terminal-launch-{manifest['job_sequence']}.txt").write_text(
            terminal, encoding="utf-8"
        )
        (run_dir / f"terminal-before-{manifest['job_sequence']}.txt").write_text(
            "", encoding="utf-8"
        )
    except BaseException:
        pane_id = manifest.get("pane_id") or started_pane_id
        if pane_id:
            run(["herdr", "pane", "close", pane_id], check=False)
        manifest["phase"] = "launch_failed"
        save_manifest(run_dir, manifest)
        raise
    print(json.dumps({"agent_name": name, "phase": "launched"}))
    return 0


def dispatch(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir).expanduser().resolve()
    manifest = load_manifest(run_dir)
    if manifest.get("phase") not in {"recorded", "retained"}:
        raise OrchestrationError(f"cannot dispatch from phase {manifest.get('phase')}")
    prompt = Path(args.prompt_file).read_text(encoding="utf-8").strip()
    if not prompt:
        raise OrchestrationError("work-order prompt is empty")
    info = agent_info(manifest["agent_name"])
    if not is_owned_agent(info, manifest):
        raise OrchestrationError("Herdr agent identity no longer matches this run")
    if info.get("agent_status") not in {"idle", "done"}:
        raise OrchestrationError("agent is not idle; do not dispatch another job")

    sequence = int(manifest.get("job_sequence", 1)) + 1
    baseline = git_baseline(
        Path(manifest["workspace"]), manifest_evidence_scopes(manifest)
    )
    write_json(run_dir / f"baseline-{sequence}.json", baseline)
    terminal = read_terminal(manifest["agent_name"], lines=120)
    (run_dir / f"terminal-before-{sequence}.txt").write_text(terminal, encoding="utf-8")
    job_log = Path(manifest["job_log"])
    log_offset = job_log.stat().st_size if job_log.exists() else 0
    token = uuid.uuid4().hex
    manifest.update(
        {
            "phase": "dispatch_pending",
            "job_sequence": sequence,
            "baseline_file": f"baseline-{sequence}.json",
            "seen_working": False,
            "terminal_before_sha256": sha256_bytes(terminal.encode()),
            "job_log_offset": log_offset,
            "job_token": token,
        }
    )
    save_manifest(run_dir, manifest)
    try:
        run(
            [
                "herdr",
                "pane",
                "run",
                info["pane_id"],
                prompt_with_token(prompt, token),
            ]
        )
    except BaseException:
        manifest["phase"] = "dispatch_uncertain"
        save_manifest(run_dir, manifest)
        raise
    manifest["phase"] = "launched"
    save_manifest(run_dir, manifest)
    print(json.dumps({"job_sequence": sequence, "phase": "launched"}))
    return 0


def observe(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir).expanduser().resolve()
    manifest = load_manifest(run_dir)
    if manifest.get("phase") not in {"launched", "attention", "timeout"}:
        raise OrchestrationError(f"cannot observe from phase {manifest.get('phase')}")
    name = manifest["agent_name"]
    deadline = time.monotonic() + args.timeout
    last_terminal = ""

    while time.monotonic() < deadline:
        info = agent_info(name)
        if not is_owned_agent(info, manifest):
            raise OrchestrationError("Herdr agent identity no longer matches this run")
        terminal = read_terminal(name, lines=args.lines)
        visible = read_terminal(name, lines=80, source="visible")
        before_path = run_dir / f"terminal-before-{manifest['job_sequence']}.txt"
        before = before_path.read_text(encoding="utf-8") if before_path.exists() else ""
        job_terminal = appended_terminal(before, terminal)
        last_terminal = terminal
        (run_dir / f"terminal-{manifest['job_sequence']}.txt").write_text(
            terminal, encoding="utf-8"
        )
        attention = needs_attention(visible)
        if attention:
            manifest.update({"phase": "attention", "attention": attention})
            save_manifest(run_dir, manifest)
            print(
                json.dumps(
                    {
                        "status": "attention",
                        "reason": attention,
                        "agent_status": info.get("agent_status"),
                        "terminal_file": str(
                            run_dir / f"terminal-{manifest['job_sequence']}.txt"
                        ),
                    }
                )
            )
            return 20

        if info.get("agent_status") == "working":
            manifest["seen_working"] = True
            save_manifest(run_dir, manifest)

        changed = bool(job_terminal.strip())
        if info.get("agent_status") in {"idle", "done"} and has_handoff(
            job_terminal, manifest["job_token"]
        ):
            if manifest.get("seen_working") or changed:
                manifest["phase"] = "handoff"
                save_manifest(run_dir, manifest)
                print(
                    json.dumps(
                        {
                            "status": "handoff",
                            "agent_status": info.get("agent_status"),
                            "terminal_file": str(
                                run_dir / f"terminal-{manifest['job_sequence']}.txt"
                            ),
                        }
                    )
                )
                return 0
        time.sleep(args.interval)

    manifest["phase"] = "timeout"
    save_manifest(run_dir, manifest)
    if last_terminal:
        print(last_terminal[-2000:])
    return 21


def snapshot(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir).expanduser().resolve()
    manifest = load_manifest(run_dir)
    if manifest.get("phase") not in {"handoff", "attention"}:
        raise OrchestrationError(f"cannot snapshot from phase {manifest.get('phase')}")
    info = agent_info(manifest["agent_name"])
    if not is_owned_agent(info, manifest):
        raise OrchestrationError("Herdr agent identity no longer matches this run")
    sequence = manifest["job_sequence"]
    terminal = read_terminal(manifest["agent_name"], lines=args.lines)
    terminal_path = run_dir / f"terminal-final-{sequence}.txt"
    terminal_path.write_text(terminal, encoding="utf-8")
    before_path = run_dir / f"terminal-before-{sequence}.txt"
    before = before_path.read_text(encoding="utf-8") if before_path.exists() else ""
    job_terminal_path = run_dir / f"terminal-job-{sequence}.txt"
    job_terminal_path.write_text(appended_terminal(before, terminal), encoding="utf-8")
    job_log = Path(manifest["job_log"])
    log_segment_path = run_dir / f"job-log-segment-{sequence}.log"
    if job_log.exists():
        with job_log.open("rb") as handle:
            handle.seek(int(manifest.get("job_log_offset", 0)))
            log_segment_path.write_bytes(handle.read())
    else:
        log_segment_path.write_bytes(b"")
    post = git_baseline(
        Path(manifest["workspace"]), manifest_evidence_scopes(manifest)
    )
    post_path = run_dir / f"post-{sequence}.json"
    write_json(post_path, post)
    baseline = json.loads((run_dir / manifest["baseline_file"]).read_text())
    comparison = {
        "baseline_equals_post": baseline_signature(baseline)
        == baseline_signature(post),
        "baseline": manifest["baseline_file"],
        "post": post_path.name,
        "terminal": terminal_path.name,
        "job_terminal": job_terminal_path.name,
        "job_log_segment": log_segment_path.name,
        "handoff_status": handoff_status(
            job_terminal_path.read_text(encoding="utf-8"), manifest["job_token"]
        ),
    }
    write_json(run_dir / f"comparison-{sequence}.json", comparison)
    manifest["phase"] = "snapshotted"
    save_manifest(run_dir, manifest)
    print(json.dumps(comparison, ensure_ascii=False, indent=2))
    return 0


def record_verified(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir).expanduser().resolve()
    manifest = load_manifest(run_dir)
    if manifest.get("phase") != "snapshotted":
        raise OrchestrationError(
            "snapshot and independent verification are required first"
        )
    sequence = manifest["job_sequence"]
    comparison_path = run_dir / f"comparison-{sequence}.json"
    comparison = json.loads(comparison_path.read_text(encoding="utf-8"))
    status = comparison.get("handoff_status")
    if status not in {"done", "partial", "blocked"}:
        raise OrchestrationError("cannot record a verified run without a handoff")
    terminal_path = run_dir / f"terminal-job-{sequence}.txt"
    classifier = Path(__file__).with_name("classify_run.py")
    result = run(
        [
            sys.executable,
            str(classifier),
            "--log",
            str(run_dir / comparison["job_log_segment"]),
            "--stdout-file",
            str(terminal_path),
            "--verified-interactive",
            "--interactive-status",
            status,
            "--model",
            manifest["model"],
            "--job",
            args.job,
            "--record",
        ],
        cwd=run_dir,
    )
    manifest["phase"] = "recorded"
    save_manifest(run_dir, manifest)
    print(result.stdout.strip())
    return 0


def cleanup(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir).expanduser().resolve()
    manifest = load_manifest(run_dir)
    if not args.keep_agent:
        info = None
        try:
            info = agent_info(manifest["agent_name"])
        except OrchestrationError:
            pass
        if info and not is_owned_agent(info, manifest):
            raise OrchestrationError(
                "agent name now belongs to a different pane; nothing was closed"
            )
        if info and info.get("pane_id"):
            result = run(["herdr", "pane", "close", info["pane_id"]], check=False)
            if result.returncode != 0:
                raise OrchestrationError("failed to close the owned Herdr pane")
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                try:
                    agent_info(manifest["agent_name"])
                except OrchestrationError:
                    break
                time.sleep(0.1)
            else:
                raise OrchestrationError(
                    "owned Herdr agent is still present; server was preserved"
                )

    keep_server = args.keep_server or args.keep_agent
    if manifest.get("server_started") and not keep_server:
        keep_server = herdr_runtime_in_use()
    if manifest.get("server_started") and not keep_server:
        stop_herdr_server(manifest.get("server_pid"), manifest.get("server_identity"))

    manifest["phase"] = "retained" if args.keep_agent else "cleaned"
    save_manifest(run_dir, manifest)
    print(
        json.dumps(
            {
                "agent_kept": args.keep_agent,
                "server_kept": keep_server,
                "herdr_running": herdr_server_running(),
            }
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Automate safe AGY orchestration through Herdr."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--workspace", required=True)
    prepare_parser.add_argument("--run-dir")
    prepare_parser.add_argument("--model", default=DEFAULT_MODEL)
    prepare_parser.add_argument("--agent-name")
    prepare_parser.add_argument(
        "--evidence-scope",
        "--scope",
        dest="evidence_scope",
        action="append",
        default=[],
        help="workspace-relative path to hash for baseline evidence; does not restrict AGY reads",
    )
    prepare_parser.add_argument("--no-model-fallback", action="store_true")
    prepare_parser.set_defaults(handler=prepare)

    launch_parser = subparsers.add_parser("launch")
    launch_parser.add_argument("--run-dir", required=True)
    launch_parser.add_argument("--prompt-file", required=True)
    launch_parser.add_argument(
        "--mode", choices=("plan", "accept-edits"), required=True
    )
    launch_parser.add_argument("--no-sandbox", action="store_true")
    launch_parser.set_defaults(handler=launch)

    dispatch_parser = subparsers.add_parser("dispatch")
    dispatch_parser.add_argument("--run-dir", required=True)
    dispatch_parser.add_argument("--prompt-file", required=True)
    dispatch_parser.set_defaults(handler=dispatch)

    observe_parser = subparsers.add_parser("observe")
    observe_parser.add_argument("--run-dir", required=True)
    observe_parser.add_argument("--timeout", type=float, default=600)
    observe_parser.add_argument("--interval", type=float, default=2)
    observe_parser.add_argument("--lines", type=int, default=180)
    observe_parser.set_defaults(handler=observe)

    snapshot_parser = subparsers.add_parser("snapshot")
    snapshot_parser.add_argument("--run-dir", required=True)
    snapshot_parser.add_argument("--lines", type=int, default=260)
    snapshot_parser.set_defaults(handler=snapshot)

    record_parser = subparsers.add_parser("record")
    record_parser.add_argument("--run-dir", required=True)
    record_parser.add_argument("--job", required=True)
    record_parser.set_defaults(handler=record_verified)

    cleanup_parser = subparsers.add_parser("cleanup")
    cleanup_parser.add_argument("--run-dir", required=True)
    cleanup_parser.add_argument("--keep-agent", action="store_true")
    cleanup_parser.add_argument("--keep-server", action="store_true")
    cleanup_parser.set_defaults(handler=cleanup)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            return args.handler(args)
        with run_lock(Path(args.run_dir).expanduser().resolve()):
            return args.handler(args)
    except (OrchestrationError, OSError, subprocess.TimeoutExpired) as error:
        print(json.dumps({"status": "error", "error": str(error)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
