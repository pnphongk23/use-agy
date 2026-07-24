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
import sqlite3
import subprocess
import sys
import time
import uuid


DEFAULT_MODEL = "Gemini 3.5 Flash (Medium)"
SMOKE_PROMPT = "Reply with exactly AGY_OK and nothing else."
REPOSITORY_STANDARD = (
    "REPOSITORY STANDARD: Use AGENTS.md/README.md and their linked docs as "
    "navigation. Start with targeted search, relevant code relationships, and "
    "nearby tests. Treat file lists and counts as starting context, not read "
    "limits; follow "
    "additional workspace dependencies when evidence makes them relevant. Verify "
    "the result with applicable checks and ground the final handoff in evidence."
)
HANDOFF_FIELDS = (
    "STATUS, SUMMARY, EVIDENCE, GUIDANCE_USED, CHANGES, VERIFICATION, "
    "UNCERTAINTY, and NEXT"
)
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
CONVERSATION_ID_PATTERN = re.compile(
    r"(?:Created conversation|Streaming conversation) "
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.IGNORECASE,
)
RAW_STEP_COLUMNS = (
    "metadata",
    "error_details",
    "permissions",
    "task_details",
    "render_info",
    "step_payload",
)
RAW_STEP_WINDOW = 64
RAW_HANDOFF_GRACE_SECONDS = 5.0
MAX_TERMINAL_LINES = 65536
MCP_GRANT_PATTERN = re.compile(r"^[^/\s()]+/(?:[^/\s()]+|\*)$")
PERMISSION_REQUEST_PATTERN = re.compile(
    r"requesting permission for:\s*([^\r\n]+)", re.IGNORECASE
)


class OrchestrationError(RuntimeError):
    pass


def capability_profile(args: argparse.Namespace | None = None) -> dict:
    args = args or argparse.Namespace()
    mcp_allow = sorted(set(getattr(args, "mcp_allow", []) or []))
    invalid = [grant for grant in mcp_allow if not MCP_GRANT_PATTERN.fullmatch(grant)]
    if invalid:
        raise OrchestrationError(
            "invalid --mcp-allow value; expected server/tool or server/*: "
            + ", ".join(invalid)
        )
    network = getattr(args, "network", "allow")
    browser = getattr(args, "browser", "allow")
    runtime_allow = []
    if network == "allow" or browser == "allow":
        runtime_allow.append("read_url(*)")
    if browser == "allow":
        runtime_allow.append("execute_url(*)")
    runtime_allow.extend(f"mcp({grant})" for grant in mcp_allow)
    return {
        "schema_version": 1,
        "skill_loading": {
            "mode": "allow",
            "scope": "installed-project-global-and-registered-resources",
        },
        "network": {
            "mode": network,
            "permission": "read_url(*)",
        },
        "browser": {
            "mode": browser,
            "permissions": ["read_url(*)", "execute_url(*)"],
        },
        "mcp": {
            "mode": "allowlist",
            "allow": mcp_allow,
            "unmatched": "ask",
        },
        "runtime_permissions": {
            "required_allow": runtime_allow,
            "mutates_settings": False,
        },
        "controlled_effects": [
            "writes",
            "commands",
            "subagents",
            "secrets-and-sensitive-data",
            "non-workspace-paths",
            "destructive-actions",
            "external-mutations",
        ],
    }


def manifest_capabilities(manifest: dict) -> dict:
    value = manifest.get("capabilities")
    return value if isinstance(value, dict) else capability_profile()


def capability_contract(capabilities: dict) -> str:
    network = capabilities.get("network", {}).get("mode", "allow")
    browser = capabilities.get("browser", {}).get("mode", "allow")
    mcp = capabilities.get("mcp", {})
    grants = mcp.get("allow", [])
    rendered_grants = ", ".join(grants) if grants else "none pre-approved; ask"
    return (
        "CAPABILITY CONTRACT: Load installed project/global skills and registered "
        "skill resources freely. Skill loading is always allowed. "
        f"Network={network}; browser={browser} within the mission. "
        f"MCP grants={rendered_grants}; unmatched MCP tools={mcp.get('unmatched', 'ask')}. "
        "Open network/browser access does not authorize login, consent, secrets, "
        "sensitive-data disclosure, destructive actions, messages, purchases, "
        "production changes, or other external mutations."
    )


def mcp_grant_matches(target: str, grants: list[str]) -> bool:
    if target in grants:
        return True
    server, separator, _ = target.partition("/")
    return bool(separator and f"{server}/*" in grants)


def permission_target_status(target: str, capabilities: dict) -> tuple[str, str]:
    normalized = target.strip().strip("`'\".,")
    if normalized.startswith("read_url("):
        allowed = (
            capabilities.get("network", {}).get("mode") == "allow"
            or capabilities.get("browser", {}).get("mode") == "allow"
        )
        return ("network", "configuration_mismatch" if allowed else "new_authority")
    if normalized.startswith("execute_url("):
        allowed = capabilities.get("browser", {}).get("mode") == "allow"
        return ("browser", "configuration_mismatch" if allowed else "new_authority")
    if normalized.startswith("mcp(") and normalized.endswith(")"):
        mcp_target = normalized[4:-1]
        grants = capabilities.get("mcp", {}).get("allow", [])
        allowed = mcp_grant_matches(mcp_target, grants)
        return ("mcp", "configuration_mismatch" if allowed else "new_authority")
    return ("permission", "new_authority")


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def run(
    argv: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    process_env = None
    if env:
        process_env = os.environ.copy()
        process_env.update(env)
    result = subprocess.run(
        argv,
        cwd=str(cwd) if cwd else None,
        env=process_env,
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
def run_lock(run_dir: Path, *, command: str):
    lock_path = run_dir / ".orchestration.lock"
    with lock_path.open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            handle.seek(0)
            owner = handle.read().strip() or "owner metadata unavailable"
            raise OrchestrationError(
                f"another lifecycle helper owns {lock_path}; "
                f"it was not waited on: {owner}"
            ) from error
        handle.seek(0)
        handle.truncate()
        handle.write(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "command": command,
                    "started_at": utc_now(),
                }
            )
            + "\n"
        )
        handle.flush()
        os.fsync(handle.fileno())
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def parse_herdr_status(text: str) -> dict[str, dict[str, str]]:
    sections: dict[str, dict[str, str]] = {}
    section: str | None = None
    for raw_line in text.splitlines():
        if raw_line and not raw_line[0].isspace() and raw_line.endswith(":"):
            section = raw_line[:-1].strip().lower()
            sections[section] = {}
            continue
        if section is None or ":" not in raw_line:
            continue
        key, value = raw_line.strip().split(":", 1)
        sections[section][key.strip().lower()] = value.strip()
    return sections


def herdr_status(env: dict[str, str] | None = None) -> dict[str, dict[str, str]]:
    result = run(["herdr", "status"], env=env, timeout=10, check=False)
    return parse_herdr_status(result.stdout + "\n" + result.stderr)


def herdr_server_running(env: dict[str, str] | None = None) -> bool:
    return herdr_status(env).get("server", {}).get("status") == "running"


def socket_identity(path: Path) -> str:
    stat = path.stat()
    return f"{stat.st_dev}:{stat.st_ino}"


def validate_herdr_status(
    status: dict[str, dict[str, str]],
) -> dict[str, str | int]:
    client = status.get("client", {})
    server = status.get("server", {})
    if server.get("status") != "running":
        raise OrchestrationError("Herdr server is not running")
    if server.get("compatible") != "yes":
        raise OrchestrationError("Herdr client/server protocols are not compatible")
    try:
        client_protocol = int(client["protocol"])
        server_protocol = int(server["protocol"])
    except (KeyError, ValueError) as error:
        raise OrchestrationError("Herdr status omitted a valid protocol") from error
    if client_protocol != server_protocol:
        raise OrchestrationError("Herdr client/server protocol mismatch")
    socket_value = server.get("socket")
    if not socket_value:
        raise OrchestrationError("Herdr status omitted the server socket")
    socket_path = Path(socket_value).expanduser().resolve()
    if not socket_path.exists():
        raise OrchestrationError(f"Herdr socket does not exist: {socket_path}")
    return {
        "client_version": client.get("version", ""),
        "server_version": server.get("version", ""),
        "protocol": server_protocol,
        "socket": str(socket_path),
        "socket_identity": socket_identity(socket_path),
    }


def manifest_herdr_env(manifest: dict) -> dict[str, str]:
    socket_path = manifest.get("herdr_socket")
    return {"HERDR_SOCKET_PATH": socket_path} if socket_path else {}


def capture_herdr_runtime() -> dict[str, str | int]:
    return validate_herdr_status(herdr_status())


def verify_herdr_runtime(manifest: dict) -> dict[str, str | int]:
    runtime = validate_herdr_status(herdr_status(manifest_herdr_env(manifest)))
    expected = {
        "client_version": manifest.get("herdr_client_version"),
        "server_version": manifest.get("herdr_server_version"),
        "protocol": manifest.get("herdr_protocol"),
        "socket": manifest.get("herdr_socket"),
        "socket_identity": manifest.get("herdr_socket_identity"),
    }
    if runtime != expected:
        raise OrchestrationError(
            "Herdr runtime identity changed after prepare; refusing control"
        )
    return runtime


def run_herdr(
    manifest: dict,
    args: list[str],
    *,
    cwd: Path | None = None,
    timeout: float | None = 10,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return run(
        ["herdr", *args],
        cwd=cwd,
        env=manifest_herdr_env(manifest),
        timeout=timeout,
        check=check,
    )


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


def stop_herdr_server(
    pid: int | None,
    identity: str | None = None,
    env: dict[str, str] | None = None,
) -> None:
    if isinstance(pid, int):
        if identity is not None and process_identity(pid) != identity:
            return
        try:
            os.kill(pid, signal.SIGINT)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and herdr_server_running(env):
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


def pane_info(manifest: dict) -> dict:
    pane_id = manifest.get("pane_id")
    if not pane_id:
        raise OrchestrationError("run manifest has no owned pane id")
    result = run_herdr(manifest, ["pane", "get", pane_id], check=False)
    if result.returncode != 0:
        raise OrchestrationError(f"Herdr pane not found: {pane_id}")
    payload = parse_json_output(result.stdout)
    return payload.get("result", {}).get("pane", {})


def list_panes(manifest: dict, workspace_id: str | None = None) -> list[dict]:
    args = ["pane", "list"]
    if workspace_id:
        args.extend(["--workspace", workspace_id])
    result = run_herdr(manifest, args, check=False)
    if result.returncode != 0:
        raise OrchestrationError("cannot enumerate Herdr panes")
    payload = parse_json_output(result.stdout)
    panes = payload.get("result", {}).get("panes", [])
    return panes if isinstance(panes, list) else []


def is_owned_pane(info: dict, manifest: dict) -> bool:
    expected = (
        manifest.get("pane_id"),
        manifest.get("terminal_id"),
        manifest.get("workspace_id"),
        manifest.get("tab_id"),
    )
    actual = (
        info.get("pane_id"),
        info.get("terminal_id"),
        info.get("workspace_id"),
        info.get("tab_id"),
    )
    return all(expected) and actual == expected


def herdr_runtime_in_use(manifest: dict) -> bool:
    result = run_herdr(manifest, ["pane", "list"], check=False)
    if result.returncode != 0:
        return True
    try:
        payload = parse_json_output(result.stdout)
    except OrchestrationError:
        return True
    panes = payload.get("result", {}).get("panes")
    return bool(panes) if isinstance(panes, list) else True


def read_terminal_capture(
    manifest: dict, lines: int = 180, source: str = "recent-unwrapped"
) -> tuple[str, bool]:
    """Read pane output using Herdr's raw-text CLI contract.

    ``herdr pane read`` is unlike Herdr's control commands: successful stdout is
    terminal text, not a JSON response.  The CLI currently exposes no
    authoritative truncation flag, so callers receive ``False`` for that value.
    """
    result = run_herdr(
        manifest,
        [
            "pane",
            "read",
            manifest["pane_id"],
            "--source",
            source,
            "--lines",
            str(lines),
            "--format",
            "text",
        ],
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        suffix = f": {detail[-2000:]}" if detail else ""
        raise OrchestrationError(
            f"Herdr pane read failed ({result.returncode}) for owned pane "
            f"{manifest['pane_id']}{suffix}"
        )
    return result.stdout, False


def read_terminal(
    manifest: dict, lines: int = 180, source: str = "recent-unwrapped"
) -> str:
    return read_terminal_capture(manifest, lines, source)[0]


def capture_launch_terminal(run_dir: Path, manifest: dict, lines: int = 80) -> bool:
    """Capture launch diagnostics without tearing down a verified AGY pane."""
    sequence = manifest["job_sequence"]
    terminal_path = run_dir / f"terminal-launch-{sequence}.txt"
    error_path = run_dir / f"terminal-launch-{sequence}.error.json"
    try:
        terminal = read_terminal(manifest, lines=lines)
    except (OrchestrationError, OSError, subprocess.TimeoutExpired) as exc:
        write_json(
            error_path,
            {
                "captured_at": utc_now(),
                "operation": "herdr pane read",
                "status": "error",
                "error": str(exc),
            },
        )
        manifest["launch_terminal_capture"] = {
            "status": "error",
            "error_file": error_path.name,
        }
        save_manifest(run_dir, manifest)
        return False

    terminal_path.write_text(terminal, encoding="utf-8")
    manifest["launch_terminal_capture"] = {
        "status": "ok",
        "terminal_file": terminal_path.name,
    }
    save_manifest(run_dir, manifest)
    return True


def read_complete_terminal(
    manifest: dict, lines: int = 180, source: str = "recent-unwrapped"
) -> str:
    """Read one bounded diagnostic window; raw handoff uses conversation data."""
    requested = min(max(1, lines), MAX_TERMINAL_LINES)
    return read_terminal_capture(manifest, requested, source)[0]


def attention_event(terminal: str, capabilities: dict | None = None) -> dict | None:
    request = PERMISSION_REQUEST_PATTERN.search(terminal)
    if request:
        target = request.group(1).strip()
        kind, classification = permission_target_status(
            target, capabilities or capability_profile()
        )
        return {
            "reason": "requesting permission for",
            "kind": kind,
            "target": target,
            "classification": classification,
        }
    lowered = terminal.lower()
    for pattern in ATTENTION_PATTERNS:
        if pattern in lowered:
            return {
                "reason": pattern,
                "kind": "onboarding-or-auth"
                if pattern != "allow access to this file"
                else "file-access",
                "target": None,
                "classification": "user_attention",
            }
    return None


def needs_attention(terminal: str) -> str | None:
    event = attention_event(terminal)
    return event["reason"] if event else None


def handoff_block(terminal: str, token: str) -> str | None:
    begin_matches = list(
        re.finditer(
            rf"(?m)^HANDOFF_BEGIN:[^\S\r\n]*{re.escape(token)}[^\S\r\n]*$",
            terminal,
        )
    )
    if not begin_matches:
        return None
    begin = begin_matches[-1]
    end = re.search(
        rf"(?m)^RUN_TOKEN:[^\S\r\n]*{re.escape(token)}[^\S\r\n]*$",
        terminal[begin.end() :],
    )
    if end is None:
        return None
    return terminal[begin.start() : begin.end() + end.end()]


def handoff_status(terminal: str, token: str | None = None) -> str | None:
    if token is None:
        match = HANDOFF_PATTERN.search(terminal)
        return match.group(1).lower() if match else None
    marked = handoff_block(terminal, token)
    if marked is None:
        return None
    block = marked.splitlines()[1:-1]
    body = "\n".join(block)
    statuses = list(HANDOFF_PATTERN.finditer(body))
    return statuses[-1].group(1).lower() if statuses else None


def has_handoff(terminal: str, token: str | None = None) -> bool:
    return handoff_status(terminal, token) is not None


def raw_handoff_block(text: str, token: str) -> str | None:
    pattern = re.compile(
        rf"HANDOFF_BEGIN:[^\S\r\n]*{re.escape(token)}\r?\n"
        rf"(?P<body>.*?)"
        rf"^RUN_TOKEN:[^\S\r\n]*{re.escape(token)}",
        re.MULTILINE | re.DOTALL,
    )
    matches = list(pattern.finditer(text))
    for match in reversed(matches):
        block = match.group(0)
        if HANDOFF_PATTERN.search(match.group("body")):
            return block
    return None


def conversation_id_from_log(log_path: Path) -> str | None:
    if not log_path.exists():
        return None
    text = log_path.read_text(encoding="utf-8", errors="replace")
    matches = CONVERSATION_ID_PATTERN.findall(text)
    return matches[-1].lower() if matches else None


def conversation_db_path(manifest: dict) -> Path | None:
    conversation_id = manifest.get("conversation_id")
    store_value = manifest.get("conversation_store")
    if not conversation_id or not store_value:
        return None
    store = Path(store_value).expanduser().resolve()
    candidate = (store / f"{conversation_id}.db").resolve()
    if candidate.parent != store:
        raise OrchestrationError("conversation database escaped its configured store")
    return candidate


def raw_conversation_handoff(manifest: dict) -> str | None:
    database = conversation_db_path(manifest)
    if database is None or not database.exists():
        return None
    try:
        connection = sqlite3.connect(
            f"file:{database}?mode=ro",
            uri=True,
            timeout=1,
        )
        try:
            connection.execute("PRAGMA query_only=ON")
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(steps)")
            }
            selected = [column for column in RAW_STEP_COLUMNS if column in columns]
            if not selected or "idx" not in columns:
                raise OrchestrationError(
                    "AGY conversation database has an unsupported steps schema"
                )
            maximum_row = connection.execute(
                "SELECT COALESCE(MAX(idx), -1) FROM steps"
            ).fetchone()
            maximum = int(maximum_row[0]) if maximum_row else -1
            minimum = max(0, maximum - RAW_STEP_WINDOW + 1)
            query = (
                "SELECT "
                + ",".join(["idx", *selected])
                + " FROM steps WHERE idx >= ? ORDER BY idx"
            )
            chunks: list[str] = []
            for row in connection.execute(query, (minimum,)):
                for value in row[1:]:
                    if isinstance(value, bytes):
                        chunks.append(value.decode("utf-8", errors="ignore"))
                    elif isinstance(value, str):
                        chunks.append(value)
        finally:
            connection.close()
    except sqlite3.Error as error:
        raise OrchestrationError(
            f"cannot read AGY conversation database: {error}"
        ) from error
    return raw_handoff_block("\n".join(chunks), manifest["job_token"])


def stream_completed(manifest: dict) -> bool:
    conversation_id = manifest.get("conversation_id")
    job_log = Path(manifest["job_log"])
    if not conversation_id or not job_log.exists():
        return False
    with job_log.open("rb") as handle:
        handle.seek(int(manifest.get("job_log_offset", 0)))
        segment = handle.read().decode("utf-8", errors="replace")
    return f"Stream completed for {conversation_id}" in segment


def refresh_conversation_identity(run_dir: Path, manifest: dict) -> bool:
    if manifest.get("conversation_id"):
        return True
    conversation_id = conversation_id_from_log(Path(manifest["job_log"]))
    if not conversation_id:
        return False
    manifest["conversation_id"] = conversation_id
    save_manifest(run_dir, manifest)
    return True


def prompt_with_token(
    prompt: str, token: str, capabilities: dict | None = None
) -> str:
    sections = [prompt]
    if REPOSITORY_STANDARD not in prompt:
        sections.append(REPOSITORY_STANDARD)
    sections.append(capability_contract(capabilities or capability_profile()))
    sections.append(
        "FINAL HANDOFF REQUIREMENT: Return these evidence-based fields: "
        + HANDOFF_FIELDS
        + ". Wrap the final handoff in two marker lines. "
        + "Build the opening marker by joining HANDOFF_BEGIN: with one space and "
        + "this token: "
        + token
        + ". Build the closing marker by joining RUN_TOKEN: with one space and "
        + "the same token: "
        + token
        + ". Put the structured STATUS and fields between those markers. Do not "
        + "quote this instruction. The marker block has no line-count limit; the "
        + "supervisor will persist it to a handoff evidence file."
    )
    return "\n\n".join(sections)


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


def merge_job_terminal(
    history: str,
    before: str,
    previous_terminal: str,
    current_terminal: str,
) -> str:
    """Accumulate a job transcript while Herdr's recent buffer rolls forward."""
    from_baseline = appended_terminal(before, current_terminal)
    if not history:
        return from_baseline
    if from_baseline.endswith(history):
        return from_baseline
    if history.endswith(from_baseline):
        return history
    delta = appended_terminal(previous_terminal, current_terminal)
    if not delta or history.endswith(delta):
        return history
    return history + delta


def create_run_workspace(run_dir: Path, manifest: dict) -> dict:
    result = run_herdr(
        manifest,
        [
            "workspace",
            "create",
            "--cwd",
            manifest["workspace"],
            "--label",
            manifest["agent_name"],
            "--no-focus",
        ],
        cwd=Path(manifest["workspace"]),
    )
    payload = parse_json_output(result.stdout).get("result", {})
    workspace = payload.get("workspace", {})
    tab = payload.get("tab", {})
    root_pane = payload.get("root_pane", {})
    workspace_id = workspace.get("workspace_id")
    tab_id = tab.get("tab_id")
    root_pane_id = root_pane.get("pane_id")
    root_terminal_id = root_pane.get("terminal_id")
    if not all((workspace_id, tab_id, root_pane_id, root_terminal_id)):
        raise OrchestrationError("Herdr workspace create returned incomplete ownership")
    if (
        tab.get("workspace_id") != workspace_id
        or root_pane.get("workspace_id") != workspace_id
        or root_pane.get("tab_id") != tab_id
    ):
        raise OrchestrationError("Herdr workspace topology is internally inconsistent")
    creation_path = run_dir / "workspace-create.json"
    creation_path.write_text(result.stdout, encoding="utf-8")
    return {
        "workspace_id": workspace_id,
        "tab_id": tab_id,
        "bootstrap_pane_id": root_pane_id,
        "bootstrap_terminal_id": root_terminal_id,
        "workspace_owned": True,
    }


def pane_split_direction(manifest: dict, pane_id: str) -> tuple[str, dict]:
    result = run_herdr(manifest, ["pane", "layout", "--pane", pane_id])
    payload = parse_json_output(result.stdout)
    layout = payload.get("result", {}).get("layout", {})
    panes = layout.get("panes", [])
    rectangle = next(
        (pane.get("rect", {}) for pane in panes if pane.get("pane_id") == pane_id),
        {},
    )
    width = rectangle.get("width")
    height = rectangle.get("height")
    if not isinstance(width, int) or not isinstance(height, int):
        raise OrchestrationError("Herdr pane layout omitted numeric dimensions")
    return ("right" if width >= height * 2 else "down"), payload


def validate_single_full_pane_layout(payload: dict, pane_id: str) -> None:
    layout = payload.get("result", {}).get("layout", {})
    panes = layout.get("panes", [])
    area = layout.get("area", {})
    if len(panes) != 1 or panes[0].get("pane_id") != pane_id:
        raise OrchestrationError(
            "run workspace layout does not contain exactly the owned pane"
        )
    rectangle = panes[0].get("rect", {})
    dimensions = (
        area.get("width"),
        area.get("height"),
        rectangle.get("width"),
        rectangle.get("height"),
    )
    if not all(isinstance(value, int) for value in dimensions):
        raise OrchestrationError("Herdr final layout omitted numeric dimensions")
    if rectangle.get("x") != area.get("x") or rectangle.get("y") != area.get("y"):
        raise OrchestrationError("owned pane does not start at the run workspace origin")
    if dimensions[0:2] != dimensions[2:4]:
        raise OrchestrationError("owned pane does not fill the run workspace")


def is_recorded_workspace_pane(info: dict, manifest: dict) -> bool:
    if (
        info.get("workspace_id") != manifest.get("workspace_id")
        or info.get("tab_id") != manifest.get("tab_id")
    ):
        return False
    identities = (
        (manifest.get("bootstrap_pane_id"), manifest.get("bootstrap_terminal_id")),
        (manifest.get("pane_id"), manifest.get("terminal_id")),
    )
    return any(
        pane_id
        and terminal_id
        and info.get("pane_id") == pane_id
        and info.get("terminal_id") == terminal_id
        for pane_id, terminal_id in identities
    )


def close_owned_workspace(manifest: dict, *, require_single_pane: bool) -> None:
    workspace_id = manifest.get("workspace_id")
    if not workspace_id or not manifest.get("workspace_owned"):
        return
    panes = list_panes(manifest, workspace_id)
    if any(not is_recorded_workspace_pane(pane, manifest) for pane in panes):
        raise OrchestrationError(
            "run-owned workspace contains an unexpected pane; nothing was closed"
        )
    if require_single_pane:
        if len(panes) != 1 or not is_owned_pane(panes[0], manifest):
            raise OrchestrationError(
                "run-owned workspace contains an unexpected pane; nothing was closed"
            )
    result = run_herdr(
        manifest,
        ["workspace", "close", workspace_id],
        check=False,
    )
    if result.returncode != 0:
        raise OrchestrationError("failed to close the run-owned Herdr workspace")


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
    if not getattr(args, "herdr_authorized", False):
        raise OrchestrationError(
            "external Herdr control requires explicit --herdr-authorized"
        )
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
    herdr_help = run(["herdr", "--help"]).stdout
    required_capabilities = (
        "herdr workspace <subcommand>",
        "herdr pane <subcommand>",
        "herdr agent <subcommand>",
    )
    if not all(capability in herdr_help for capability in required_capabilities):
        raise OrchestrationError("installed Herdr lacks required control commands")
    models = run(["agy", "models"]).stdout.splitlines()
    if args.model not in {line.strip() for line in models}:
        raise OrchestrationError(f"model is not listed by AGY: {args.model}")

    server_started = False
    server_pid = None
    server_identity = None
    if not herdr_server_running():
        server_pid, server_identity = start_herdr_server(run_dir)
        server_started = True
    try:
        runtime = capture_herdr_runtime()
    except BaseException:
        if server_started:
            stop_herdr_server(server_pid, server_identity)
        raise

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
            "schema_version": 3,
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "phase": "prepared",
            "workspace": str(workspace),
            "run_dir": str(run_dir),
            "agent_name": agent_name,
            "model": selected_model,
            "agy_version": agy_version,
            "herdr_version": herdr_version,
            "herdr_client_version": runtime["client_version"],
            "herdr_server_version": runtime["server_version"],
            "herdr_protocol": runtime["protocol"],
            "herdr_socket": runtime["socket"],
            "herdr_socket_identity": runtime["socket_identity"],
            "server_started": server_started,
            "server_pid": server_pid,
            "server_identity": server_identity,
            "smoke_elapsed_seconds": round(time.monotonic() - started, 3),
            "job_sequence": 1,
            "baseline_file": "baseline-1.json",
            "evidence_scopes": evidence_scopes,
            "conversation_store": str(
                Path.home() / ".gemini" / "antigravity-cli" / "conversations"
            ),
            "herdr_authorized": True,
            "capabilities": capability_profile(args),
        }
        save_manifest(run_dir, manifest)
        manifest_durable = True
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 0
    except BaseException:
        if server_started and not manifest_durable:
            stop_herdr_server(
                server_pid,
                server_identity,
                {"HERDR_SOCKET_PATH": str(runtime["socket"])},
            )
        raise


def launch(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir).expanduser().resolve()
    manifest = load_manifest(run_dir)
    if manifest["phase"] != "prepared":
        raise OrchestrationError(f"cannot launch from phase {manifest['phase']}")
    prompt = Path(args.prompt_file).read_text(encoding="utf-8").strip()
    if not prompt:
        raise OrchestrationError("work-order prompt is empty")

    verify_herdr_runtime(manifest)
    name = manifest["agent_name"]
    job_log = run_dir / f"job-{manifest['job_sequence']}.log"
    token = uuid.uuid4().hex
    prompt = prompt_with_token(prompt, token, manifest_capabilities(manifest))
    manifest.update(
        {
            "phase": "launch_pending",
            "mode": args.mode,
            "job_log": str(job_log),
            "job_token": token,
            "seen_working": False,
            "job_log_offset": 0,
            "conversation_id": None,
        }
    )
    save_manifest(run_dir, manifest)
    try:
        manifest.update(create_run_workspace(run_dir, manifest))
        save_manifest(run_dir, manifest)
        direction, initial_layout = pane_split_direction(
            manifest, manifest["bootstrap_pane_id"]
        )
        write_json(run_dir / "layout-before-launch.json", initial_layout)
        argv = [
            "agent",
            "start",
            name,
            "--cwd",
            manifest["workspace"],
            "--workspace",
            manifest["workspace_id"],
            "--tab",
            manifest["tab_id"],
            "--split",
            direction,
            "--no-focus",
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
        result = run_herdr(
            manifest,
            argv,
            cwd=Path(manifest["workspace"]),
            timeout=30,
        )
        payload = parse_json_output(result.stdout)
        info = payload.get("result", {}).get("agent", {})
        if (
            info.get("name") != name
            or not info.get("pane_id")
            or not info.get("terminal_id")
            or info.get("workspace_id") != manifest["workspace_id"]
            or info.get("tab_id") != manifest["tab_id"]
        ):
            raise OrchestrationError("Herdr start returned incomplete ownership data")
        manifest.update(
            {
                "phase": "launched",
                "pane_id": info["pane_id"],
                "terminal_id": info.get("terminal_id"),
                "split_direction": direction,
            }
        )
        save_manifest(run_dir, manifest)
        bootstrap = manifest["bootstrap_pane_id"]
        bootstrap_info = next(
            (
                pane
                for pane in list_panes(manifest, manifest["workspace_id"])
                if pane.get("pane_id") == bootstrap
            ),
            None,
        )
        if (
            not bootstrap_info
            or bootstrap_info.get("terminal_id")
            != manifest["bootstrap_terminal_id"]
        ):
            raise OrchestrationError("run bootstrap pane identity changed")
        close_result = run_herdr(
            manifest,
            ["pane", "close", bootstrap],
            check=False,
        )
        if close_result.returncode != 0:
            raise OrchestrationError("failed to close the run bootstrap pane")
        remaining = list_panes(manifest, manifest["workspace_id"])
        if len(remaining) != 1 or not is_owned_pane(remaining[0], manifest):
            raise OrchestrationError("run workspace did not converge to one owned pane")
        _, final_layout = pane_split_direction(manifest, manifest["pane_id"])
        validate_single_full_pane_layout(final_layout, manifest["pane_id"])
        write_json(run_dir / "layout-after-launch.json", final_layout)
        (run_dir / f"launch-{manifest['job_sequence']}.json").write_text(
            result.stdout, encoding="utf-8"
        )
        capture_launch_terminal(run_dir, manifest, lines=80)
        (run_dir / f"terminal-before-{manifest['job_sequence']}.txt").write_text(
            "", encoding="utf-8"
        )
        refresh_conversation_identity(run_dir, manifest)
    except BaseException:
        if manifest.get("workspace_owned"):
            try:
                close_owned_workspace(manifest, require_single_pane=False)
            except (OrchestrationError, OSError, subprocess.TimeoutExpired):
                pass
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
    verify_herdr_runtime(manifest)
    info = pane_info(manifest)
    if not is_owned_pane(info, manifest):
        raise OrchestrationError("Herdr pane identity no longer matches this run")
    if info.get("agent_status") not in {"idle", "done"}:
        raise OrchestrationError("agent is not idle; do not dispatch another job")

    sequence = int(manifest.get("job_sequence", 1)) + 1
    baseline = git_baseline(
        Path(manifest["workspace"]), manifest_evidence_scopes(manifest)
    )
    write_json(run_dir / f"baseline-{sequence}.json", baseline)
    terminal = read_terminal(manifest, lines=120)
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
            "raw_handoff_missing_since": None,
        }
    )
    save_manifest(run_dir, manifest)
    try:
        run_herdr(
            manifest,
            [
                "pane",
                "run",
                info["pane_id"],
                prompt_with_token(prompt, token, manifest_capabilities(manifest)),
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
    verify_herdr_runtime(manifest)
    deadline = time.monotonic() + args.timeout
    last_terminal = ""
    raw_missing_since: float | None = None
    sequence = manifest["job_sequence"]
    before_path = run_dir / f"terminal-before-{sequence}.txt"
    before = before_path.read_text(encoding="utf-8") if before_path.exists() else ""
    terminal_path = run_dir / f"terminal-{sequence}.txt"
    history_path = run_dir / f"terminal-job-live-{sequence}.txt"
    job_terminal = (
        history_path.read_text(encoding="utf-8") if history_path.exists() else ""
    )
    previous_terminal = (
        terminal_path.read_text(encoding="utf-8")
        if terminal_path.exists()
        else before
    )

    while time.monotonic() < deadline:
        info = pane_info(manifest)
        if not is_owned_pane(info, manifest):
            raise OrchestrationError("Herdr pane identity no longer matches this run")
        terminal = read_terminal(manifest, lines=args.lines)
        visible = read_terminal(manifest, lines=80, source="visible")
        candidate = merge_job_terminal(
            job_terminal, before, previous_terminal, terminal
        )
        job_terminal = candidate
        previous_terminal = terminal
        last_terminal = terminal
        terminal_path.write_text(terminal, encoding="utf-8")
        history_path.write_text(job_terminal, encoding="utf-8")
        attention = attention_event(visible, manifest_capabilities(manifest))
        if attention:
            manifest.update(
                {
                    "phase": "attention",
                    "attention": attention["reason"],
                    "attention_detail": attention,
                }
            )
            save_manifest(run_dir, manifest)
            print(
                json.dumps(
                    {
                        "status": "attention",
                        "reason": attention["reason"],
                        "attention": attention,
                        "agent_status": info.get("agent_status"),
                        "terminal_file": str(terminal_path),
                    }
                )
            )
            return 20

        if info.get("agent_status") == "working":
            manifest["seen_working"] = True
            save_manifest(run_dir, manifest)

        refresh_conversation_identity(run_dir, manifest)
        raw_handoff = raw_conversation_handoff(manifest)
        completed_state = info.get("agent_status") in {"idle", "done"}
        if completed_state and raw_handoff is not None:
            handoff_path = run_dir / f"handoff-{sequence}.txt"
            handoff_path.write_text(raw_handoff + "\n", encoding="utf-8")
            write_json(
                run_dir / f"handoff-source-{sequence}.json",
                {
                    "source": "agy-conversation-sqlite",
                    "conversation_id": manifest["conversation_id"],
                    "status": handoff_status(raw_handoff, manifest["job_token"]),
                },
            )
            manifest["phase"] = "handoff"
            manifest["handoff_source"] = "agy-conversation-sqlite"
            save_manifest(run_dir, manifest)
            print(
                json.dumps(
                    {
                        "status": "handoff",
                        "agent_status": info.get("agent_status"),
                        "terminal_file": str(terminal_path),
                        "job_terminal_file": str(history_path),
                        "handoff_file": str(handoff_path),
                        "handoff_source": manifest["handoff_source"],
                    }
                )
            )
            return 0
        if completed_state and stream_completed(manifest):
            if raw_missing_since is None:
                raw_missing_since = time.monotonic()
            if time.monotonic() - raw_missing_since >= RAW_HANDOFF_GRACE_SECONDS:
                manifest["phase"] = "malformed_handoff"
                save_manifest(run_dir, manifest)
                raise OrchestrationError(
                    "AGY stream completed without a valid raw conversation handoff; "
                    "terminal output was not accepted as completion evidence"
                )
        else:
            raw_missing_since = None
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
    verify_herdr_runtime(manifest)
    info = pane_info(manifest)
    if not is_owned_pane(info, manifest):
        raise OrchestrationError("Herdr pane identity no longer matches this run")
    sequence = manifest["job_sequence"]
    terminal = read_complete_terminal(manifest, lines=args.lines)
    terminal_path = run_dir / f"terminal-final-{sequence}.txt"
    terminal_path.write_text(terminal, encoding="utf-8")
    before_path = run_dir / f"terminal-before-{sequence}.txt"
    before = before_path.read_text(encoding="utf-8") if before_path.exists() else ""
    recent_path = run_dir / f"terminal-{sequence}.txt"
    previous_terminal = (
        recent_path.read_text(encoding="utf-8") if recent_path.exists() else before
    )
    history_path = run_dir / f"terminal-job-live-{sequence}.txt"
    history = history_path.read_text(encoding="utf-8") if history_path.exists() else ""
    job_terminal = merge_job_terminal(
        history, before, previous_terminal, terminal
    )
    history_path.write_text(job_terminal, encoding="utf-8")
    job_terminal_path = run_dir / f"terminal-job-{sequence}.txt"
    job_terminal_path.write_text(job_terminal, encoding="utf-8")
    handoff_path = run_dir / f"handoff-{sequence}.txt"
    handoff = handoff_path.read_text(encoding="utf-8") if handoff_path.exists() else None
    if manifest.get("phase") == "handoff" and handoff is None:
        raise OrchestrationError(
            "raw conversation handoff evidence is missing; terminal output was not used"
        )
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
        "handoff": handoff_path.name if handoff_path.exists() else None,
        "job_log_segment": log_segment_path.name,
        "handoff_status": handoff_status(handoff or "", manifest["job_token"]),
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
    terminal_path = run_dir / (
        comparison.get("handoff") or comparison["job_terminal"]
    )
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
    verify_herdr_runtime(manifest)
    if not args.keep_agent:
        info = None
        try:
            info = pane_info(manifest)
        except OrchestrationError:
            pass
        if info and not is_owned_pane(info, manifest):
            raise OrchestrationError(
                "pane identity no longer belongs to this run; nothing was closed"
            )
        if info:
            close_owned_workspace(manifest, require_single_pane=True)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                try:
                    pane_info(manifest)
                except OrchestrationError:
                    break
                time.sleep(0.1)
            else:
                raise OrchestrationError(
                    "owned Herdr agent is still present; server was preserved"
                )

    keep_server = args.keep_server or args.keep_agent
    if manifest.get("server_started") and not keep_server:
        keep_server = herdr_runtime_in_use(manifest)
    if manifest.get("server_started") and not keep_server:
        stop_herdr_server(
            manifest.get("server_pid"),
            manifest.get("server_identity"),
            manifest_herdr_env(manifest),
        )

    manifest["phase"] = "retained" if args.keep_agent else "cleaned"
    save_manifest(run_dir, manifest)
    print(
        json.dumps(
            {
                "agent_kept": args.keep_agent,
                "server_kept": keep_server,
                "herdr_running": herdr_server_running(manifest_herdr_env(manifest)),
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
    prepare_parser.add_argument(
        "--herdr-authorized",
        action="store_true",
        help="confirm that the user explicitly invoked use-agy or requested Herdr",
    )
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
    prepare_parser.add_argument(
        "--network",
        choices=("allow", "ask", "deny"),
        default="allow",
        help="mission-bound read_url capability; default allow",
    )
    prepare_parser.add_argument(
        "--browser",
        choices=("allow", "ask", "deny"),
        default="allow",
        help="mission-bound execute_url capability; default allow",
    )
    prepare_parser.add_argument(
        "--mcp-allow",
        action="append",
        default=[],
        metavar="SERVER/TOOL",
        help="allow one MCP server/tool or server/*; repeatable, unmatched tools ask",
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
        with run_lock(
            Path(args.run_dir).expanduser().resolve(), command=args.command
        ):
            return args.handler(args)
    except (OrchestrationError, OSError, subprocess.TimeoutExpired) as error:
        print(json.dumps({"status": "error", "error": str(error)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
