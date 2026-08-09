#!/usr/bin/env python3
"""Summarize AGY stream-json NDJSON into an ordered timeline and report.md."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


HANDOFF_REQUIRED = (
    "status",
    "summary",
    "evidence",
    "changes",
    "checks",
    "uncertainty",
    "next",
)
HANDOFF_STATUS = {"done", "partial", "blocked"}
HANDOFF_OPTIONAL = ("context_receipt", "requirement_matrix")
SHA256_RE = re.compile(r"^[a-fA-F0-9]{64}$")
DEFAULT_TRUNCATE = 400

INPUT_TOKEN_CEILINGS = {
    "flash-low": 80000,
    "flash-medium": 120000,
    "flash-high": 160000,
    "pro": 160000,
    "thinking": 160000,
    "claude": 160000,
    "gpt": 160000,
    "unknown": 100000,
}


def truncate_value(value: Any, limit: int) -> tuple[Any, bool, int | None]:
    if isinstance(value, str):
        original = len(value)
        if original <= limit:
            return value, False, original
        return value[:limit] + f"...<truncated original_length={original}>", True, original
    if isinstance(value, dict):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
        original = len(text)
        if original <= limit:
            return value, False, original
        return (
            {"_truncated": True, "original_length": original, "preview": text[:limit]},
            True,
            original,
        )
    if isinstance(value, list):
        text = json.dumps(value, ensure_ascii=False)
        original = len(text)
        if original <= limit:
            return value, False, original
        return (
            {"_truncated": True, "original_length": original, "preview": text[:limit]},
            True,
            original,
        )
    return value, False, None


def model_family(model: str | None) -> str:
    if not model:
        return "unknown"
    m = model.lower()
    if "flash-low" in m:
        return "flash-low"
    if "flash-medium" in m:
        return "flash-medium"
    if "flash-high" in m:
        return "flash-high"
    if "pro" in m:
        return "pro"
    if "thinking" in m:
        return "thinking"
    if "claude" in m:
        return "claude"
    if "gpt" in m:
        return "gpt"
    return "unknown"


def validate_manifest(obj: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(obj, dict):
        return ["context manifest is not an object"]
    allowed = {
        "require_context_receipt",
        "require_requirement_matrix",
        "corrective_run",
        "native_skill",
        "references",
        "critical_rules",
        "requirements",
    }
    extras = sorted(obj.keys() - allowed)
    if extras:
        errors.append(f"unexpected context manifest fields: {', '.join(extras)}")
    for key in (
        "require_context_receipt",
        "require_requirement_matrix",
        "corrective_run",
    ):
        if not isinstance(obj.get(key), bool):
            errors.append(f"context manifest {key} must be a boolean")
    for key in ("references", "critical_rules", "requirements"):
        if not isinstance(obj.get(key), list):
            errors.append(f"context manifest {key} must be an array")
    native = obj.get("native_skill")
    if native is not None:
        if not isinstance(native, dict):
            errors.append("context manifest native_skill must be an object")
        else:
            if set(native) != {"slug", "activation", "version_hash"}:
                errors.append("context manifest native_skill has invalid fields")
            if not isinstance(native.get("slug"), str) or not native.get("slug"):
                errors.append("context manifest native_skill.slug must be non-empty")
            if native.get("activation") not in {"native-slash", "contract-pack"}:
                errors.append("context manifest native_skill.activation is invalid")
            if not isinstance(native.get("version_hash"), str) or not SHA256_RE.fullmatch(
                native.get("version_hash", "")
            ):
                errors.append("context manifest native_skill.version_hash must be SHA-256")
    references = obj.get("references")
    if isinstance(references, list):
        for index, item in enumerate(references):
            if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
                errors.append(f"context manifest references[{index}] has invalid fields")
                continue
            if not isinstance(item.get("path"), str) or not item.get("path"):
                errors.append(f"context manifest references[{index}].path must be non-empty")
            if not isinstance(item.get("sha256"), str) or not SHA256_RE.fullmatch(
                item.get("sha256", "")
            ):
                errors.append(f"context manifest references[{index}].sha256 must be SHA-256")
    rules = obj.get("critical_rules")
    if isinstance(rules, list) and any(not isinstance(rule, str) or not rule for rule in rules):
        errors.append("context manifest critical_rules must contain non-empty strings")
    requirements = obj.get("requirements")
    seen_requirements: set[str] = set()
    if isinstance(requirements, list):
        for index, item in enumerate(requirements):
            label = f"context manifest requirements[{index}]"
            if not isinstance(item, dict) or set(item) != {
                "requirement_id",
                "role",
                "required_checks",
            }:
                errors.append(f"{label} has invalid fields")
                continue
            requirement_id = item.get("requirement_id")
            if not isinstance(requirement_id, str) or not requirement_id:
                errors.append(f"{label}.requirement_id must be non-empty")
            elif requirement_id in seen_requirements:
                errors.append(f"duplicate context manifest requirement_id: {requirement_id}")
            else:
                seen_requirements.add(requirement_id)
            if not isinstance(item.get("role"), str) or not item.get("role"):
                errors.append(f"{label}.role must be non-empty")
            checks = item.get("required_checks")
            if not isinstance(checks, list) or any(
                not isinstance(check, str) or not check for check in checks
            ):
                errors.append(f"{label}.required_checks must contain non-empty strings")
            elif len(checks) != len(set(checks)):
                errors.append(f"{label}.required_checks contains duplicates")
    if obj.get("require_requirement_matrix") is True and not requirements:
        errors.append("context manifest requirements must be non-empty when matrix is required")
    return errors


def validate_context_receipt(receipt: Any, manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(receipt, dict):
        return ["missing required context_receipt"]
    if set(receipt) != {"skills_activated", "references_loaded", "critical_rules"}:
        errors.append("context_receipt has invalid fields")
    skills = receipt.get("skills_activated")
    refs = receipt.get("references_loaded")
    rules = receipt.get("critical_rules")
    if not isinstance(skills, list):
        errors.append("context_receipt.skills_activated must be an array")
        skills = []
    if not isinstance(refs, list):
        errors.append("context_receipt.references_loaded must be an array")
        refs = []
    if not isinstance(rules, list) or any(not isinstance(rule, str) for rule in rules):
        errors.append("context_receipt.critical_rules must be an array of strings")
        rules = []

    native = manifest.get("native_skill")
    if isinstance(native, dict):
        match = next(
            (
                skill
                for skill in skills
                if isinstance(skill, dict) and skill.get("name") == native.get("slug")
            ),
            None,
        )
        if match is None:
            errors.append(f"context_receipt missing skill: {native.get('slug')}")
        else:
            if match.get("activation") != native.get("activation"):
                errors.append(f"context_receipt activation mismatch for {native.get('slug')}")
            if str(match.get("version_hash", "")).lower() != str(
                native.get("version_hash", "")
            ).lower():
                errors.append(f"context_receipt version_hash mismatch for {native.get('slug')}")

    received_refs = {
        item.get("path"): str(item.get("sha256", "")).lower()
        for item in refs
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }
    for expected in manifest.get("references", []):
        path = expected.get("path")
        if path not in received_refs:
            errors.append(f"context_receipt missing reference: {path}")
        elif received_refs[path] != str(expected.get("sha256", "")).lower():
            errors.append(f"context_receipt sha256 mismatch for reference: {path}")
    received_rules = set(rules)
    for rule in manifest.get("critical_rules", []):
        if rule not in received_rules:
            errors.append(f"context_receipt missing critical rule: {rule}")
    return errors


def validate_requirement_matrix(
    matrix: Any,
    handoff_status: Any,
    manifest: dict[str, Any] | None = None,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(matrix, list) or not matrix:
        return ["missing required requirement_matrix"]
    rows_by_id: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(matrix):
        label = f"requirement_matrix[{index}]"
        if not isinstance(row, dict):
            errors.append(f"{label} must be an object")
            continue
        required = {
            "requirement_id",
            "role",
            "planned_requirement",
            "resolved_output",
            "checks",
            "fallback",
            "coverage",
        }
        if set(row) != required:
            errors.append(f"{label} has invalid fields")
        requirement_id = row.get("requirement_id")
        if not isinstance(requirement_id, str) or not requirement_id:
            errors.append(f"{label}.requirement_id must be non-empty")
        elif requirement_id in rows_by_id:
            errors.append(f"duplicate requirement_matrix requirement_id: {requirement_id}")
        else:
            rows_by_id[requirement_id] = row
        if not isinstance(row.get("role"), str) or not row.get("role"):
            errors.append(f"{label}.role must be non-empty")
        if not isinstance(row.get("planned_requirement"), str) or not row.get(
            "planned_requirement"
        ):
            errors.append(f"{label}.planned_requirement must be non-empty")
        fallback = row.get("fallback")
        if isinstance(fallback, dict):
            if fallback.get("used") is True and fallback.get("approved") is not True:
                errors.append(f"{label} uses an unapproved fallback")
        else:
            errors.append(f"{label}.fallback must be an object")
        checks = row.get("checks")
        if not isinstance(checks, list):
            errors.append(f"{label}.checks must be an array")
        else:
            seen_checks: set[str] = set()
            for check_index, check in enumerate(checks):
                check_label = f"{label}.checks[{check_index}]"
                if not isinstance(check, dict) or set(check) != {"name", "status", "evidence"}:
                    errors.append(f"{check_label} has invalid fields")
                    continue
                name = check.get("name")
                if not isinstance(name, str) or not name:
                    errors.append(f"{check_label}.name must be non-empty")
                elif name in seen_checks:
                    errors.append(f"{label} has duplicate check: {name}")
                else:
                    seen_checks.add(name)
                if check.get("status") not in {
                    "pass",
                    "fail",
                    "unresolved",
                    "not-applicable",
                }:
                    errors.append(f"{check_label}.status is invalid")
                if not isinstance(check.get("evidence"), str):
                    errors.append(f"{check_label}.evidence must be a string")
                if handoff_status == "done" and check.get("status") not in {
                    "pass",
                    "not-applicable",
                }:
                    errors.append(
                        f"{label} cannot be done with check {name!r}={check.get('status')!r}"
                    )
        if handoff_status == "done" and row.get("coverage") != "pass":
            errors.append(f"{label} cannot be done with coverage={row.get('coverage')!r}")
        if handoff_status == "done" and not row.get("resolved_output"):
            errors.append(f"{label} cannot be done without a resolved output")

    if manifest is not None:
        for expected in manifest.get("requirements", []):
            requirement_id = expected.get("requirement_id")
            row = rows_by_id.get(requirement_id)
            if row is None:
                errors.append(f"requirement_matrix missing requirement: {requirement_id}")
                continue
            if row.get("role") != expected.get("role"):
                errors.append(f"requirement_matrix role mismatch for: {requirement_id}")
            actual_checks = {
                check.get("name")
                for check in row.get("checks", [])
                if isinstance(check, dict) and isinstance(check.get("name"), str)
            }
            for required_check in expected.get("required_checks", []):
                if required_check not in actual_checks:
                    errors.append(
                        f"requirement_matrix {requirement_id} missing check: {required_check}"
                    )
    return errors


def validate_handoff(obj: Any, manifest: dict[str, Any] | None = None) -> list[str]:
    errors: list[str] = []
    if not isinstance(obj, dict):
        return ["structured_output is not an object"]
    for key in HANDOFF_REQUIRED:
        if key not in obj:
            errors.append(f"missing handoff field: {key}")
    status = obj.get("status")
    if status is not None and status not in HANDOFF_STATUS:
        errors.append(f"invalid handoff status: {status!r}")
    for key in ("evidence", "changes", "checks", "uncertainty"):
        if key in obj and not isinstance(obj[key], list):
            errors.append(f"{key} must be an array")
        elif key in obj and isinstance(obj[key], list):
            if any(not isinstance(item, str) for item in obj[key]):
                errors.append(f"{key} must be an array of strings")
    if "summary" in obj and not isinstance(obj["summary"], str):
        errors.append("summary must be a string")
    if "next" in obj and not isinstance(obj["next"], str):
        errors.append("next must be a string")
    allowed_fields = set(HANDOFF_REQUIRED) | set(HANDOFF_OPTIONAL)
    if obj.keys() - allowed_fields:
        # allow only schema fields; warn on extras as invalid for this skill contract
        extras = sorted(obj.keys() - allowed_fields)
        errors.append(f"unexpected handoff fields: {', '.join(extras)}")
    if manifest is not None:
        if manifest.get("require_context_receipt") or "context_receipt" in obj:
            errors.extend(validate_context_receipt(obj.get("context_receipt"), manifest))
        if manifest.get("require_requirement_matrix") or "requirement_matrix" in obj:
            errors.extend(
                validate_requirement_matrix(obj.get("requirement_matrix"), status, manifest)
            )
    elif "requirement_matrix" in obj:
        errors.extend(validate_requirement_matrix(obj.get("requirement_matrix"), status))
    return errors


def parse_structured_output(
    result: dict[str, Any], manifest: dict[str, Any] | None = None
) -> tuple[Any, list[str]]:
    diagnostics: list[str] = []
    structured = result.get("structured_output")
    if structured is not None:
        return structured, validate_handoff(structured, manifest)

    response = result.get("response")
    if isinstance(response, str) and response.strip():
        try:
            parsed = json.loads(response)
        except json.JSONDecodeError as exc:
            diagnostics.append(f"response is not valid JSON: {exc}")
            return None, diagnostics
        return parsed, validate_handoff(parsed, manifest)

    diagnostics.append("missing structured_output and parseable response")
    return None, diagnostics


def timeline_entry(ordinal: int, event_name: str, payload: dict[str, Any], limit: int) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "ordinal": ordinal,
        "event": event_name,
    }
    for key in ("timestamp", "ts", "created_at", "time"):
        if key in payload:
            entry["timestamp"] = payload[key]
            break

    if event_name == "init":
        init = payload.get("init") if isinstance(payload.get("init"), dict) else payload
        entry["conversation_id"] = payload.get("conversation_id") or init.get("conversation_id")
        entry["cwd"] = init.get("cwd")
        entry["permission_mode"] = init.get("permission_mode")
        entry["model"] = init.get("model")
        entry["agent"] = init.get("agent")
        tools = init.get("tools")
        if isinstance(tools, list):
            entry["tools_count"] = len(tools)
        return entry

    if event_name == "step_update":
        step = payload.get("step_update") if isinstance(payload.get("step_update"), dict) else payload
        entry["conversation_id"] = step.get("conversation_id") or payload.get("conversation_id")
        entry["step_index"] = step.get("step_index")
        entry["state"] = step.get("state")
        entry["step_type"] = step.get("step_type")
        if "tool_name" in step:
            entry["tool_name"] = step.get("tool_name")
        if "duration_seconds" in step:
            entry["duration_seconds"] = step.get("duration_seconds")
        if "usage" in step:
            entry["usage"] = step.get("usage")
        if "text_delta" in step:
            text, truncated, original = truncate_value(step.get("text_delta"), limit)
            entry["text_delta"] = text
            if truncated:
                entry["text_delta_truncated"] = True
                entry["text_delta_original_length"] = original
        tool_info = step.get("tool_info")
        if isinstance(tool_info, dict):
            params, p_trunc, p_len = truncate_value(tool_info.get("parameters"), limit)
            output, o_trunc, o_len = truncate_value(tool_info.get("output"), limit)
            compact_tool = {
                "name": tool_info.get("name") or step.get("tool_name"),
                "parameters": params,
                "output": output,
            }
            if "error" in tool_info:
                compact_tool["error"] = tool_info.get("error")
            if p_trunc:
                compact_tool["parameters_truncated"] = True
                compact_tool["parameters_original_length"] = p_len
            if o_trunc:
                compact_tool["output_truncated"] = True
                compact_tool["output_original_length"] = o_len
            entry["tool_info"] = compact_tool
        subagent_info = step.get("subagent_info")
        if subagent_info is not None:
            sub, s_trunc, s_len = truncate_value(subagent_info, limit)
            entry["subagent_info"] = sub
            if s_trunc:
                entry["subagent_info_truncated"] = True
                entry["subagent_info_original_length"] = s_len
        return entry

    if event_name == "result":
        result = payload.get("result") if isinstance(payload.get("result"), dict) else payload
        entry["conversation_id"] = result.get("conversation_id") or payload.get("conversation_id")
        entry["status"] = result.get("status")
        entry["duration_seconds"] = result.get("duration_seconds")
        entry["num_turns"] = result.get("num_turns")
        entry["usage"] = result.get("usage")
        if "error" in result:
            entry["error"] = result.get("error")
        return entry

    # Unknown event types still appear in order.
    raw, truncated, original = truncate_value(payload, limit)
    entry["payload"] = raw
    if truncated:
        entry["payload_truncated"] = True
        entry["payload_original_length"] = original
    return entry


def render_report(structured: dict[str, Any], conversation_id: str | None, status: str | None) -> str:
    lines = [
        "# AGY Report",
        "",
        f"- conversation_id: `{conversation_id or ''}`",
        f"- agy_status: `{status or ''}`",
        f"- handoff_status: `{structured.get('status', '')}`",
        "",
        "## Summary",
        "",
        str(structured.get("summary", "")),
        "",
        "## Evidence",
        "",
    ]
    evidence = structured.get("evidence") or []
    if evidence:
        lines.extend(f"- {item}" for item in evidence)
    else:
        lines.append("- (none)")
    lines.extend(["", "## Changes", ""])
    changes = structured.get("changes") or []
    if changes:
        lines.extend(f"- {item}" for item in changes)
    else:
        lines.append("- (none)")
    lines.extend(["", "## Checks", ""])
    checks = structured.get("checks") or []
    if checks:
        lines.extend(f"- {item}" for item in checks)
    else:
        lines.append("- (none)")
    lines.extend(["", "## Uncertainty", ""])
    uncertainty = structured.get("uncertainty") or []
    if uncertainty:
        lines.extend(f"- {item}" for item in uncertainty)
    else:
        lines.append("- (none)")
    receipt = structured.get("context_receipt")
    if isinstance(receipt, dict):
        lines.extend(["", "## Context Receipt", ""])
        skills = receipt.get("skills_activated") or []
        lines.append(f"- Skills activated: {len(skills)}")
        references = receipt.get("references_loaded") or []
        lines.append(f"- References loaded: {len(references)}")
        rules = receipt.get("critical_rules") or []
        lines.append(f"- Critical rules acknowledged: {len(rules)}")
    matrix = structured.get("requirement_matrix")
    if isinstance(matrix, list):
        lines.extend(["", "## Requirement Matrix", ""])
        lines.append("| Requirement | Role | Output | Coverage |")
        lines.append("| --- | --- | --- | --- |")
        for row in matrix:
            if not isinstance(row, dict):
                continue
            cells = [
                str(row.get("requirement_id", "")),
                str(row.get("role", "")),
                str(row.get("resolved_output", "")).replace("|", "\\|"),
                str(row.get("coverage", "")),
            ]
            lines.append("| " + " | ".join(cells) + " |")
    lines.extend(["", "## Next", "", str(structured.get("next", "")), ""])
    return "\n".join(lines)


def summarize(
    events_path: Path,
    stderr_path: Path | None,
    truncate_limit: int,
    context_manifest: dict[str, Any] | None = None,
    manifest_errors: list[str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None, int]:
    diagnostics: list[str] = list(manifest_errors or [])
    timeline: list[dict[str, Any]] = []
    tools: list[dict[str, Any]] = []
    unfinished: dict[tuple[Any, Any], dict[str, Any]] = {}
    subagents: list[Any] = []
    checkpoint_count = 0
    conversation_id: str | None = None
    model: str | None = None
    result_payloads: list[dict[str, Any]] = []
    events_read = 0
    exit_code = 1 if diagnostics else 0

    if not events_path.exists():
        diagnostics.append(f"events file missing: {events_path}")
        exit_code = 2
        summary = {
            "stream_valid": False,
            "conversation_id": None,
            "status": None,
            "events_read": 0,
            "timeline": [],
            "tools": [],
            "unfinished_tools": [],
            "subagents": [],
            "usage": None,
            "structured_output": None,
            "response": None,
            "checkpoint_count": 0,
            "input_tokens": None,
            "resume_hint": {
                "has_conversation_id": False,
                "checkpoint_count": 0,
                "input_tokens": None,
                "input_token_ceiling": INPUT_TOKEN_CEILINGS["unknown"],
                "over_token_ceiling": False,
                "facts_only": True,
            },
            "diagnostics": diagnostics,
        }
        return summary, None, exit_code

    with events_path.open("r", encoding="utf-8") as handle:
        for line_no, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            events_read += 1
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                diagnostics.append(f"malformed JSON at line {line_no}: {exc}")
                exit_code = 1
                timeline.append(
                    {
                        "ordinal": line_no,
                        "event": "malformed",
                        "error": str(exc),
                        "preview": line[:truncate_limit],
                    }
                )
                continue

            if not isinstance(payload, dict):
                diagnostics.append(f"non-object event at line {line_no}")
                exit_code = 1
                continue

            event_name = payload.get("event") or "unknown"
            entry = timeline_entry(line_no, event_name, payload, truncate_limit)
            timeline.append(entry)

            if event_name == "init":
                init = payload.get("init") if isinstance(payload.get("init"), dict) else {}
                conversation_id = (
                    payload.get("conversation_id")
                    or init.get("conversation_id")
                    or conversation_id
                )
                model = init.get("model") or model
            elif event_name == "step_update":
                step = payload.get("step_update") if isinstance(payload.get("step_update"), dict) else {}
                conversation_id = step.get("conversation_id") or conversation_id
                if step.get("step_type") == "checkpoint":
                    checkpoint_count += 1
                tool_info = step.get("tool_info")
                if isinstance(tool_info, dict) or step.get("step_type") == "tool":
                    key = (step.get("step_index"), step.get("tool_name") or (tool_info or {}).get("name"))
                    compact = {
                        "ordinal": line_no,
                        "step_index": step.get("step_index"),
                        "state": step.get("state"),
                        "name": step.get("tool_name") or (tool_info or {}).get("name"),
                    }
                    if isinstance(tool_info, dict):
                        params, _, _ = truncate_value(tool_info.get("parameters"), truncate_limit)
                        output, _, _ = truncate_value(tool_info.get("output"), truncate_limit)
                        compact["parameters"] = params
                        compact["output"] = output
                        if "error" in tool_info:
                            compact["error"] = tool_info.get("error")
                    tools.append(compact)
                    if step.get("state") == "ACTIVE":
                        unfinished[key] = compact
                    elif step.get("state") == "DONE":
                        unfinished.pop(key, None)
                if step.get("subagent_info") is not None:
                    subagents.append(step.get("subagent_info"))
            elif event_name == "result":
                result = payload.get("result") if isinstance(payload.get("result"), dict) else payload
                if isinstance(result, dict):
                    result_payloads.append(result)
                    conversation_id = result.get("conversation_id") or conversation_id

    if events_read == 0:
        diagnostics.append("events file is empty")
        exit_code = 1

    if len(result_payloads) == 0:
        diagnostics.append("missing terminal result event")
        exit_code = 1
    elif len(result_payloads) > 1:
        diagnostics.append(f"multiple terminal result events: {len(result_payloads)}")
        exit_code = 1

    if unfinished:
        diagnostics.append(f"unfinished ACTIVE tools: {len(unfinished)}")
        exit_code = 1

    stderr_text = ""
    if stderr_path is not None:
        if stderr_path.exists():
            stderr_text = stderr_path.read_text(encoding="utf-8", errors="replace")
            if stderr_text.strip():
                preview, truncated, original = truncate_value(stderr_text, truncate_limit)
                diagnostics.append(
                    "stderr_nonempty"
                    + (f" truncated_preview_length={original}" if truncated else "")
                )
                # keep a compact stderr excerpt in diagnostics for supervisor review
                diagnostics.append(f"stderr_preview: {preview}")
        else:
            diagnostics.append(f"stderr file missing: {stderr_path}")

    result = result_payloads[-1] if result_payloads else None
    structured = None
    response = None
    usage = None
    status = None
    duration = None
    if result is not None:
        status = result.get("status")
        usage = result.get("usage")
        duration = result.get("duration_seconds")
        response = result.get("response")
        structured, handoff_errors = parse_structured_output(result, context_manifest)
        diagnostics.extend(handoff_errors)
        if handoff_errors:
            exit_code = 1
        if status != "SUCCESS":
            diagnostics.append(f"agy status is {status!r}, expected SUCCESS")
            exit_code = 1
        if result.get("error"):
            diagnostics.append(f"result.error: {result.get('error')}")

    input_tokens = None
    if isinstance(usage, dict):
        input_tokens = usage.get("input_tokens")
    family = model_family(model)
    ceiling = INPUT_TOKEN_CEILINGS[family]
    over_ceiling = isinstance(input_tokens, int) and input_tokens > ceiling
    summary = {
        "stream_valid": exit_code == 0,
        "conversation_id": conversation_id,
        "status": status,
        "model": model,
        "duration_seconds": duration,
        "events_read": events_read,
        "timeline": timeline,
        "tools": tools,
        "unfinished_tools": list(unfinished.values()),
        "subagents": subagents,
        "usage": usage,
        "structured_output": structured,
        "context_manifest_applied": context_manifest is not None,
        "response": response,
        "checkpoint_count": checkpoint_count,
        "input_tokens": input_tokens,
        "resume_hint": {
            "has_conversation_id": bool(conversation_id),
            "checkpoint_count": checkpoint_count,
            "input_tokens": input_tokens,
            "model_family": family,
            "input_token_ceiling": ceiling,
            "over_token_ceiling": over_ceiling,
            "facts_only": True,
            "note": (
                "Supervisor decides resume_eligible using input-token facts plus "
                "job-local resume count and same mission/authority. "
                "checkpoint_count is observational and does not prove compaction."
            ),
        },
        "diagnostics": diagnostics,
    }
    report_source = structured if isinstance(structured, dict) and exit_code == 0 else None
    return summary, report_source, exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", required=True, help="Path to redirected stream-json NDJSON")
    parser.add_argument("--stderr", help="Path to redirected stderr file")
    parser.add_argument("--summary-out", required=True, help="Path for ordered-summary.json")
    parser.add_argument("--report-out", required=True, help="Path for report.md")
    parser.add_argument(
        "--context-manifest",
        help="Optional context-manifest.json; makes declared receipt/matrix checks fail-closed",
    )
    parser.add_argument(
        "--truncate",
        type=int,
        default=DEFAULT_TRUNCATE,
        help=f"Max chars for large fields in summary (default {DEFAULT_TRUNCATE})",
    )
    args = parser.parse_args(argv)

    events_path = Path(args.events)
    stderr_path = Path(args.stderr) if args.stderr else None
    summary_out = Path(args.summary_out)
    report_out = Path(args.report_out)

    context_manifest = None
    manifest_errors: list[str] = []
    if args.context_manifest:
        manifest_path = Path(args.context_manifest)
        if not manifest_path.exists():
            manifest_errors.append(f"context manifest missing: {manifest_path}")
        else:
            try:
                context_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                manifest_errors.append(f"context manifest is not valid JSON: {exc}")
            else:
                manifest_errors.extend(validate_manifest(context_manifest))

    summary, report_source, exit_code = summarize(
        events_path,
        stderr_path,
        args.truncate,
        context_manifest,
        manifest_errors,
    )

    summary_out.parent.mkdir(parents=True, exist_ok=True)
    summary_out.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    if report_source is None:
        # Do not write a fake success report.
        if report_out.exists():
            report_out.unlink()
        print(
            json.dumps(
                {
                    "ok": False,
                    "summary_out": str(summary_out),
                    "report_out": None,
                    "diagnostics": summary.get("diagnostics", []),
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return exit_code if exit_code != 0 else 1

    report_out.parent.mkdir(parents=True, exist_ok=True)
    report_out.write_text(
        render_report(
            report_source,
            summary.get("conversation_id"),
            summary.get("status"),
        ),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "ok": True,
                "summary_out": str(summary_out),
                "report_out": str(report_out),
                "conversation_id": summary.get("conversation_id"),
                "events_read": summary.get("events_read"),
                "status": summary.get("status"),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
