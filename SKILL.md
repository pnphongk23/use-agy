---
name: use-agy
description: Use when the user asks to run, delegate to, supervise, or verify work with AGY or the Google Antigravity CLI. Runs one fresh foreground primary `agy -p` process, permits and encourages useful AGY subagents, gives them a neutral mission and explicit authority boundary, captures a structured handoff, and independently verifies the result.
---

# Use AGY

Run one foreground AGY as the primary independent engineer. Let it use subagents when they improve exploration, review, or parallel checks. Keep the supervisor responsible for scope, authority, monitoring, verification, and the final answer.

Default loop:

```text
frame -> run one fresh primary AGY -> monitor -> handoff -> verify -> report
```

No Herdr, TUI orchestration, worktrees, session pool, telemetry ledger, or lifecycle framework.

## Principles

1. Use AGY only when delegation saves meaningful work or provides useful independent reasoning. Work directly for trivial tasks.
2. Treat AGY as an independent engineer inside an explicit authority envelope, not as a function that confirms the supervisor's answer.
3. For uncertain diagnosis, architecture, or foundation work, give AGY facts, unknowns, constraints, and open questions. Do not pre-solve and ask for confirmation.
4. Use one foreground primary AGY process. Encourage at most one read-only subagent by default when independent work clearly justifies its token and lifecycle cost; skip subagents for trivial work. The primary AGY must coordinate and consolidate one handoff.
5. Start a fresh conversation by default. Resume only when the user explicitly wants prior AGY context.
6. Allow broad reads inside the approved workspace. Keep writes, commands, secrets, and external effects narrow.
7. AGY's handoff is a claim, not proof. Verify changed artifacts and checks independently.
8. Do not read the full AGY log on a normal successful run.

## References

Read only what the task needs:

- [references/work-orders.md](references/work-orders.md): prompt and handoff template.
- [references/security-and-permissions.md](references/security-and-permissions.md): authority and safety boundaries.
- [references/agy-cli.md](references/agy-cli.md): current foreground CLI behavior.

## 1. Frame A Neutral Mission

Define:

- observable goal;
- relevant facts and inputs;
- unknowns and open decisions;
- real constraints;
- allowed writes and commands;
- acceptance checks.

Do not send the supervisor's whole conversation or reasoning transcript. Send the smallest context pack that lets AGY solve the task:

```text
GOAL
FACTS
UNKNOWNS
CONSTRAINTS
ALLOWED EFFECTS
DONE WHEN
```

When the solution is already a real user decision, state it as a constraint. Otherwise leave the decision open.

## 2. Establish Guardrails

Always:

- bind the approved workspace with `--add-dir` and run the process from that workspace;
- preserve unrelated and pre-existing user changes;
- use `--mode plan` for read-only work and `--mode accept-edits` for authorized edits;
- allow read-only AGY subagents within the same mission and authority boundary;
- permit `--dangerously-skip-permissions` for headless runs so already-authorized tools do not soft-deny;
- never provide or extract secrets;
- never automate login, consent, trust, telemetry, or persistent permission changes;
- never infer authority to commit, push, delete user data, publish, deploy, message, purchase, or mutate production.

If AGY needs an effect outside the work order, it must return `status: blocked`. Exit code `0` does not override a blocked or incomplete handoff.

## 3. Run One Foreground AGY

Inspect `agy --help` when flags may have changed. Put the prompt immediately after `-p`:

```bash
agy -p '<WORK_ORDER>' \
  --add-dir '<WORKSPACE>' \
  --mode plan \
  --dangerously-skip-permissions \
  --output-format stream-json \
  --json-schema '<USE_AGY_SKILL>/handoff.schema.json' \
  --print-timeout 10m \
  --log-file '<UNIQUE_LOG>'
```

Use `--mode accept-edits` only when edits are authorized. `--dangerously-skip-permissions` removes CLI approval friction; it does not expand the work order's authority. Consume `stream-json` live; never redirect stdout to a file without a live event consumer. Let normal AGY configuration choose the model unless the user requests one or a diagnosed runtime failure requires an explicit model.

Do not smoke-test models before every job. Diagnose only after an actual failure.

## 4. Monitor Without Consuming The Context

Keep the foreground process handle and consume NDJSON events as they arrive.

While it runs:

- capture the `init` conversation ID;
- summarize `step_update` events without copying full tool output into supervisor context;
- track `subagent_info` conversation IDs and log URIs when present;
- accept only the terminal `result` event as the process handoff;
- surface useful status to the user during long work;
- stop if AGY visibly exceeds its authority.

Do not use a sink-only stdout redirect, repeatedly poll the full log, or stream raw tool payloads into the supervisor's context. If events stop for a task-inappropriate interval, inspect the conversation and a targeted log excerpt immediately. After a terminal `result`, stop waiting; if the owned process does not exit promptly, terminate it.

## 5. Accept The Handoff

Require the terminal `result.response` to match [handoff.schema.json](handoff.schema.json):

```json
{
  "status": "done | partial | blocked",
  "summary": "string",
  "evidence": ["string"],
  "changes": ["string"],
  "checks": ["string"],
  "uncertainty": ["string"],
  "next": "string"
}
```

Treat a terminal `result.status` other than success, schema failure, missing result, permission soft-denial, or contradiction as incomplete even when the process exits `0`.

Use at most one retry for a transient runtime failure or clear prompt/argument drift. Keep the same goal and authority boundary. After that, work directly or report the blocker.

## 6. Verify

On a normal successful run, read only:

- the structured handoff;
- every changed file or cited artifact;
- the diff;
- the output of checks rerun by the supervisor.

Validate the terminal result first. Inspect the conversation and targeted log excerpts with `tail`, `rg`, or a narrow range only when:

- the process fails or times out;
- the handoff is missing or contradictory;
- a permission or authentication block is suspected;
- AGY may have exceeded scope;
- AGY's reported check differs from the supervisor's check.

Do not accept reasoning prose, a green claim, or exit code alone as proof.

## 7. Report

Tell the user:

- what AGY was asked to do;
- what it actually changed or concluded;
- what the supervisor independently verified;
- remaining uncertainty or blocked authority.

If a required check cannot run, state: `Tôi không thể xác minh điều này hoạt động vì...` and name the blocker.
