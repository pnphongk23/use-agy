---
name: use-agy
description: Use when the user asks to run, delegate to, supervise, or verify work with AGY or the Google Antigravity CLI. Supervisor writes one handoff.md, runs one fresh foreground primary AGY via the `run-agy.py` watchdog script with `--output-format stream-json` redirected to files, parses the ordered timeline into summary + report.md, reviews carefully, and may resume once for a large corrective fix.
---

# Use AGY

The supervisor is the commander: frame the mission, write the handoff, run AGY, read the ordered timeline and report, verify independently, decide self-fix vs resume/fresh, and report to the user.

AGY is the independent engineer inside an explicit authority envelope. Its terminal handoff is a claim, not proof.

Default loop:

```text
frame -> write handoff.md -> run AGY (stream-json -> files) -> summarize -> report.md -> verify -> triage -> report
```

No Herdr, TUI orchestration, worktrees, session pool, telemetry ledger, or lifecycle framework.

## Quick Start

Run the deterministic preflight before reading any reference:

```bash
python3 '<USE_AGY_SKILL>/scripts/preflight-agy.py' \
  --workspace '<WORKSPACE>' \
  --mode plan
```

Use `--mode accept-edits` only for authorized edits. Add `--skill '<exact-slug>'` when the mission requires a native AGY skill.

The preflight checks the installed and latest AGY versions, required headless flags, and the exact native skill when requested. It creates a unique run directory and `handoff.md` skeleton, then prints compact `RUN` and `SUMMARIZE` commands. Fill the handoff, execute `RUN` with the host's background-process mechanism, then execute `SUMMARIZE` after exit. Do not read a reference unless preflight fails or the routing below requires advanced behavior.

## Principles

1. Use AGY only when delegation saves meaningful work or provides useful independent reasoning. Work directly for trivial tasks.
2. Treat AGY as an independent engineer inside an explicit authority envelope, not as a function that confirms the supervisor's answer.
3. For uncertain diagnosis, architecture, or foundation work, give AGY facts, unknowns, constraints, and open questions. Do not pre-solve and ask for confirmation.
4. Use one foreground primary AGY process. Encourage at most one read-only subagent by default when independent work clearly justifies its token and lifecycle cost; skip subagents for trivial work. The primary AGY must coordinate and consolidate one handoff.
5. Start a fresh conversation by default. Resume at most once for a large corrective fix when resume-eligible (rule b). Resume for prior context only when the user asks or corrective review needs the same AGY conversation.
6. Allow broad reads inside the approved workspace. Keep writes, commands, secrets, and external effects narrow.
7. AGY's handoff is a claim, not proof. Verify changed artifacts and checks independently.
8. Always use `--output-format stream-json` redirected to files. Never consume live NDJSON into supervisor context. Parse every event with the summarize script into an ordered timeline and `report.md`. Do not read the full AGY log on a normal successful run. Do not read thinking/transcript files.
9. Never write “read skill X” and assume activation. Use native slash activation when available; otherwise embed the necessary task-specific rules directly in the handoff and fail closed when required domain context is unavailable or unverified.

## References

Read only what the task needs:

- [references/work-orders.md](references/work-orders.md): prompt and handoff template.
- [references/security-and-permissions.md](references/security-and-permissions.md): authority and safety boundaries.
- [references/agy-cli.md](references/agy-cli.md): current foreground CLI behavior and summarize script.

## 1. Frame A Neutral Mission

Define:

- observable goal;
- relevant facts and inputs;
- unknowns and open decisions;
- real constraints;
- allowed writes and commands;
- acceptance checks.

Do not send the supervisor's whole conversation or reasoning transcript. Write the smallest context pack into one handoff file:

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

If AGY needs an effect outside the work order, it must return `status: blocked`. Exit code `0` does not override a blocked or incomplete handoff. Missing `report.md` means the run is incomplete.

## 3. Build The Handoff And Run Directory

Keep the task source self-contained in `handoff.md`, even when the mission depends on a domain skill or external references.

### Capability preflight

Use the preflight output. It verifies AGY `>= 1.1.9`, required flags, the latest published changelog version, and an exact requested skill slug. It never updates AGY or changes plugins. If a requested native skill is absent, embed only the necessary project rules directly in `handoff.md`. If the full native skill is mandatory and unavailable, return blocked; never silently send an ordinary handoff.

Native activation:

```text
/<project-skill-slug>
<handoff contents>
```

Embedded-rules fallback:

```text
PROJECT RULES
<compiled, task-specific invariants>

<work-order contents>
```

The fallback is still a precise briefing, not a loose summary. Include only the applicable invariants, approved fallbacks, forbidden substitutions, and stop/block conditions AGY actually needs. The supervisor must read the source skill and required references before compiling it.

The preflight creates a unique `<RUN_DIR>` for every invocation:

```text
<RUN_DIR>/
  handoff.md            # single self-contained task source written by supervisor
  events.ndjson         # redirected stream-json
  stderr.txt
  cli.log               # --log-file target
  ordered-summary.json  # script output
  report.md             # required terminal report
```

Write `<RUN_DIR>/handoff.md` from the work-order template. If a native skill is available, start the file with `/<slug>`. Otherwise compile the necessary rules directly into the same file; do not point AGY at an unverified sibling manifest it cannot read.

## 4. Run One Primary AGY via Watchdog

Always run AGY through `run-agy.py` and use the host's background-process mechanism after a short startup wait (5–30 seconds). In Cursor, call the Shell tool with a short `block_until_ms` (for example `5000`), retain the returned shell/PID handle, and immediately smoke-check that the watchdog started. Never set `block_until_ms` to the full AGY print timeout.

```bash
python3 '<USE_AGY_SKILL>/scripts/run-agy.py' \
  --events "$RUN_DIR/events.ndjson" \
  --stderr "$RUN_DIR/stderr.txt" \
  --liveness-timeout 180 \
  --heartbeat-interval 30 \
  --overall-timeout 930 \
  -- \
  agy -p "$(<"$RUN_DIR/handoff.md")" \
  --add-dir '<WORKSPACE>' \
  --mode plan \
  --dangerously-skip-permissions \
  --output-format stream-json \
  --json-schema '<USE_AGY_SKILL>/handoff.schema.json' \
  --print-timeout 15m \
  --log-file "$RUN_DIR/cli.log"
```

Keep the watchdog hard timeout later than AGY's print timeout (here 930s vs 900s) so AGY can emit its own terminal timeout result first. The active-tool liveness timeout may terminate a stuck command earlier.

Use `--mode accept-edits` only when edits are authorized. `--dangerously-skip-permissions` removes CLI approval friction; it does not expand the work order's authority. Let normal AGY configuration choose the model unless the user requests one or a diagnosed runtime failure requires an explicit model.

Do not smoke-test models before every job. Diagnose only after an actual failure.

## 5. Supervise Without Blocking The Chat

The watchdog runs in the background and prints compact heartbeats. Keep the supervisor responsive: continue independent verification work, surface meaningful progress, and rely on the host's completion notification. Use bounded waits only when the next step genuinely requires the result.

While it runs:
- Do not stream raw NDJSON events or read the events file directly into supervisor context.
- Treat `[heartbeat]` lines as liveness metadata only; they never include prompts or raw tool payloads.
- The watchdog script automatically terminates the AGY process and its process group if:
  - There is a stalled ACTIVE tool call and no new events are received for `--liveness-timeout` (default 180s).
  - The `--overall-timeout` (default 930s) is exceeded.
- If a terminal result (SUCCESS or ERROR) is written to `events.ndjson`, the watchdog script stops waiting and exits immediately.
- If the watchdog exits with an error status (including synthetic liveness timeouts), stop waiting immediately and run the summarize script to triage.
- To terminate manually, kill only the owned watchdog PID/process group from the retained host handle. In Cursor, use the PID shown by the Shell terminal metadata; never kill by a broad process-name match.

After exit, run:

```bash
python3 '<USE_AGY_SKILL>/scripts/summarize-agy-stream.py' \
  --events "$RUN_DIR/events.ndjson" \
  --stderr "$RUN_DIR/stderr.txt" \
  --summary-out "$RUN_DIR/ordered-summary.json" \
  --report-out "$RUN_DIR/report.md"
```

The script must read every NDJSON line in order, emit `ordered-summary.json` with a chronological `timeline`, and materialize `report.md` from terminal `structured_output`.

Read:

- `ordered-summary.json` — full ordered timeline (compact fields may be truncated; respect `truncated` markers);
- `report.md` — required human-readable terminal report;
- `stderr.txt` — soft-denials and runtime diagnostics;
- targeted `cli.log` excerpts only on failure or contradiction;
- raw `events.ndjson` only when the summary is missing, contradictory, or truncated where review needs the full payload.

## 6. Accept The Handoff And Report

Require all of:

- summarize script exit `0`;
- exactly one terminal `result` with AGY `status: SUCCESS`;
- `structured_output` matching [handoff.schema.json](handoff.schema.json);
- `report.md` present and derived from that structured output.

Handoff object shape:

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

Treat AGY envelope status other than `SUCCESS`, schema failure, missing/empty events, missing `report.md`, permission soft-denial in stderr, or contradiction as incomplete even when the process exits `0`.

## 7. Verify And Triage

On a normal successful run, read:

- the ordered timeline and `report.md`;
- every changed file or cited artifact;
- the diff;
- the output of checks rerun by the supervisor.

For work with project-defined semantics, independently compare AGY's claims against the canonical project requirement and supervisor rerun checks. `use-agy` must not define or reinterpret domain roles; their meaning belongs exclusively to the project skill or the embedded rules compiled into the handoff.

AGY may use only a fallback already approved by the work order. Failure to satisfy a project-defined check never authorizes silently changing the approved requirement or plan. Return `partial` or `blocked` with the failed/unresolved checks and candidate limitations instead.

Validate the file-captured result first. Inspect stderr, conversation, and targeted log excerpts only when:

- the process fails or times out;
- events/summary/report are missing or contradictory;
- a permission or authentication block is suspected;
- AGY may have exceeded scope;
- AGY's reported check differs from the supervisor's check.

Do not accept reasoning prose, a green claim, or exit code alone as proof.

Triage after independent review:

| Case | Action |
| --- | --- |
| OK | Report to the user |
| Small fix: exactly one file, local and clear | Supervisor edits + re-verify |
| Large fix: ≥2 files, or one file that is cross-cutting / broad logic, or checks still fail after one supervisor self-fix | Return to AGY |

## 8. Corrective Resume Or Fresh

At most one corrective AGY run per job.

Write `<RUN_DIR>/corrective-handoff.md` (do not reuse or mutate the original handoff). Resume when **all** resume-eligible conditions hold; otherwise start fresh:

1. prior `conversation_id` exists;
2. no corrective resume used yet in this job;
3. prior `input_tokens` is below the conservative model threshold in [references/agy-cli.md](references/agy-cli.md); `checkpoint_count` is observational only and must not be treated as proof of compaction;
4. same goal and authority boundary.

Resume:

```bash
python3 '<USE_AGY_SKILL>/scripts/run-agy.py' \
  --events "$RUN_DIR/corrective-events.ndjson" \
  --stderr "$RUN_DIR/corrective-stderr.txt" \
  --liveness-timeout 180 \
  --heartbeat-interval 30 \
  --overall-timeout 930 \
  -- \
  agy -p "$(<"$RUN_DIR/corrective-handoff.md")" \
  --conversation '<CONVERSATION_ID>' \
  --add-dir '<WORKSPACE>' \
  --mode accept-edits \
  --dangerously-skip-permissions \
  --output-format stream-json \
  --json-schema '<USE_AGY_SKILL>/handoff.schema.json' \
  --print-timeout 15m \
  --log-file "$RUN_DIR/corrective-cli.log"
```

Then summarize corrective events into corrective summary/report paths and verify again. Do not loop beyond that single corrective AGY run.

A corrective run must preserve honest acceptance claims. Removing an invalid output may fix one defect while creating a coverage deficit; the result is not `done` until the original requirement is honestly re-covered. Otherwise return `partial` or `blocked` and say what remains uncovered.

Also allow one retry for transient runtime failure or clear prompt/argument drift under the same goal/authority rules. Do not retry login, consent, secret, or genuinely new-authority blockers.

## 9. Report

Tell the user:

- what AGY was asked to do (`handoff.md` goal);
- what the ordered timeline and `report.md` claim;
- what the supervisor independently verified;
- whether a small self-fix, resume, or fresh corrective run happened;
- remaining uncertainty or blocked authority.

If a required check cannot run, state: `Tôi không thể xác minh điều này hoạt động vì...` and name the blocker.
