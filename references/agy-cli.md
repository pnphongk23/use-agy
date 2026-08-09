# AGY Foreground CLI

Authority sources, in order:

1. Installed binary: `agy --version`, `agy --help`
2. Official headless docs: https://antigravity.google/docs/cli/headless

Current verified version for this skill: AGY `1.1.11` (locally checked 2026-08-09). Native skill expansion in headless/print mode requires AGY `>= 1.1.9`.

## Skill And Plugin Capability Preflight

AGY 1.1.11 exposes print-mode slash/skill expansion (unless `--disable-slash-commands` is passed) and `agy plugin list|import|install|enable|disable|validate`.

For the normal path, run `scripts/preflight-agy.py --workspace '<WORKSPACE>' [--skill '<exact-slug>']` and use its compact output. The script performs the checks below, creates the run directory and handoff skeleton, and prints ready-to-use run/summarize commands. Use the manual steps only to diagnose a preflight failure or behavior introduced by a newer CLI.

Before a mission that requires a native skill:

1. Check `agy --version` and `agy --help`.
2. Run `agy plugin list` and inspect the active workspace/global skill inventory. Use `/skills` machine-readable output when supported by the installed CLI.
3. Confirm the exact slug. Do not infer availability from a Claude skill folder or assume `agy plugin import claude` imported standalone `~/.claude/skills`.
4. Do not install/import/enable plugins as a preflight side effect without explicit user authority.
5. If native activation is available, begin the prompt with `/<skill-slug>`. If unavailable, use a hashed run-scoped contract pack or block when native activation is mandatory.

Official locations documented by Antigravity include workspace `.agents/skills/`, global `~/.gemini/antigravity-cli/skills/`, and plugin `skills/` directories. Keep filesystem discovery read-only.

## Per-Run Directory

Every invocation uses a unique `<RUN_DIR>`:

```text
<RUN_DIR>/
  handoff.md
  domain-contract.md       # only for Skill Bridge runs
  context-manifest.json    # only for Skill Bridge runs
  events.ndjson
  stderr.txt
  cli.log
  ordered-summary.json
  report.md
```

Corrective resume adds sibling files such as `corrective-handoff.md`, `corrective-events.ndjson`, `corrective-stderr.txt`, `corrective-cli.log`, `corrective-ordered-summary.json`, and `corrective-report.md`.

## Normal Invocation

Write `<RUN_DIR>/handoff.md` first. Put its contents immediately after `-p`. Always use the `run-agy.py` watchdog script to redirect stdout/stderr to files:

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

`--overall-timeout` must be later than `--print-timeout` (930s vs 15m/900s above). This grace lets AGY write its own timeout result; active-tool liveness can still end a stuck tool earlier.

Use `--mode accept-edits` for authorized implementation.

- Run from the approved workspace and pass the same path with `--add-dir`.
- Start fresh by default. Pass `--conversation` only for one corrective resume when resume-eligible, or when the user requests prior context.
- Let normal AGY configuration select the model. Run `agy models` only when model selection matters or after a diagnosed model failure.
- Use `--effort` only when the user or task needs a deliberate override.
- Use `--dangerously-skip-permissions` for authorized headless work when permission prompts would otherwise soft-deny tools.
- Keep stdout and stderr as separate files so NDJSON stays parseable.

The permission-bypass flag auto-approves CLI tool requests. It removes runtime friction but does not authorize effects omitted from the work order.

For native skill activation, the value passed to `-p` must begin with the slash skill:

```bash
agy -p "/<project-skill-slug>
$(<"$RUN_DIR/handoff.md")" ...
```

Merely mentioning the skill inside the handoff is not activation. For contract fallback, prepend the compiled `DOMAIN CONTRACT` content instead.

## Output Formats (Official)

Per Antigravity headless docs, `--output-format` accepts:

| Format | `stdout` shape | Skill policy |
| --- | --- | --- |
| `text` | Response text | Not used for handoff |
| `json` | One JSON object on completion | Not used; lacks ordered tool timeline |
| `stream-json` | NDJSON event stream | **Required** — always redirect to `events.ndjson` |

Never consume live NDJSON into supervisor context. Parse the file after exit.

With `--json-schema`, the terminal `result` carries `structured_output`; `response` is the same payload as a string. Prefer `structured_output`.

## Summarize Script

After process exit:

```bash
python3 '<USE_AGY_SKILL>/scripts/summarize-agy-stream.py' \
  --events "$RUN_DIR/events.ndjson" \
  --stderr "$RUN_DIR/stderr.txt" \
  --summary-out "$RUN_DIR/ordered-summary.json" \
  --report-out "$RUN_DIR/report.md" \
  --context-manifest "$RUN_DIR/context-manifest.json"
```

Omit `--context-manifest` for missions with no external skill/domain contract. When present it is validated against [../context-manifest.schema.json](../context-manifest.schema.json) and makes declared receipt/matrix requirements fail-closed.

Contract:

- read every NDJSON line in file order (`ordinal` is authoritative; never reorder by timestamp);
- emit a chronological `timeline` covering init, every step_update (including ACTIVE and DONE), and the terminal result;
- truncate large params/output fields in the summary while marking `truncated: true` and original length;
- materialize exactly one `report.md` from valid terminal `structured_output`;
- when `--context-manifest` is present, verify exact skill activation/hash, reference hashes, critical-rule receipt, and any required semantic matrix;
- exit non-zero and refuse a fake success report when the stream/result/handoff is invalid.

Summary fields used by the supervisor:

- `conversation_id`
- `status`
- `events_read`
- `timeline`
- `tools` / unfinished tools
- `subagents`
- `usage`
- `structured_output` / `response`
- `context_manifest_applied`
- `checkpoint_count`
- `input_tokens`
- `resume_hint` facts for rule (b)
- `diagnostics`

## Models

Run `agy models` and pass an exact listed slug with `--model` when selection matters.

- `gemini-3.6-flash-low`: extraction and cheap routine work.
- `gemini-3.6-flash-medium`: balanced default for normal coding and review.
- `gemini-3.6-flash-high`: difficult diagnosis, architecture, or high-risk review.

Other listed families may be selected when the user asks. Use `--effort low|medium|high` only when choosing effort separately instead of using an effort-specific model slug.

## Process Contract

The foreground process provides:

- real process lifetime and exit code;
- NDJSON events on stdout (redirected to `events.ndjson`);
- diagnostics on stderr (redirected to `stderr.txt`);
- a unique `--log-file` for targeted failure investigation.

Terminal `result` fields used by this skill:

- `conversation_id`
- `status` (`SUCCESS`, `ERROR`, `CANCELED`, `INTERRUPTED`, `INVALID`, `WAITING`, `RUNNING`)
- `response`
- `structured_output` (when `--json-schema` is set)
- `error` (on failure)
- `usage` (token accounting, including `cache_read_tokens`)

Exit `0` is not sufficient. Soft-denied tools may still exit `0` with notices on stderr. Missing `report.md` is incomplete.

For a Skill Bridge run, a missing/mismatched receipt, missing declared requirement/check, unapproved fallback, or `done` status with unresolved coverage/checks also makes the summarizer exit non-zero and remove a stale report. `partial`/`blocked` may honestly carry unresolved rows for supervisor review. All role and check semantics come from the project contract; the summarizer does not define domain vocabulary.

## Resume Heuristic (Rule B)

The summarize script reports facts only. The supervisor decides resume eligibility.

`resume_eligible` when all are true:

1. `conversation_id` is present;
2. this job has not already used a corrective resume (max one);
3. `input_tokens` is below the conservative model ceiling below;
4. same goal and authority boundary as the original handoff.

Simple input-token ceilings for rule (b):

| Model family | Max prior `input_tokens` to allow resume |
| --- | ---: |
| `*-flash-low` | 80000 |
| `*-flash-medium` | 120000 |
| `*-flash-high`, `*-pro-*`, thinking/claude/gpt | 160000 |
| unknown / unset | 100000 |

If not eligible, start a fresh corrective conversation with a full corrective handoff file. Do not invent a precise remaining-context percentage.

`checkpoint_count` is useful timeline metadata but does not prove that context compaction occurred; never use it alone to allow or deny resume.

## Monitoring

Run the watchdog with the host's background-process mechanism after a short startup wait (5–30s). In Cursor, invoke Shell with a short `block_until_ms` (for example `5000`), retain the shell/PID handle, and smoke-check startup once. Never use the full AGY print timeout as `block_until_ms`.

The watchdog prints compact `[heartbeat]` status lines without prompts or raw tool payloads. Keep the chat responsive: do independent work, rely on completion notifications, and use bounded `AwaitShell` waits only when the next step requires the result.

The watchdog automatically terminates the AGY process group if:
1. There is an active tool and no new events are received for `--liveness-timeout` (default 180s).
2. The `--overall-timeout` (default 930s) is exceeded.

If a terminal result (SUCCESS or ERROR) is written to `events.ndjson`, the watchdog stops waiting and exits immediately.

For manual cancellation, terminate only the owned watchdog PID/process group from the retained host handle. Never kill by a broad process-name match.

After exit:

1. Run the summarize script.
2. Require script success, AGY `status == SUCCESS`, schema-valid `structured_output`, and `report.md`.
3. Read the ordered timeline and report.
4. Scan stderr for permission, auth, or soft-deny notices.
5. On failure or contradiction only, inspect log excerpts:

```bash
tail -n 120 "$RUN_DIR/cli.log"
rg -n 'permission|denied|not logged|quota|capacity|error|timeout' "$RUN_DIR/cli.log"
```

Do not repeatedly poll the full log during a healthy run. Do not ingest raw tool payloads into supervisor context unless the summary truncated the exact field under review.

## Subagents

Encourage at most one read-only subagent by default for separable exploration or independent review. Require a concise result. The primary should collect child status/result through stream `subagent_info`, its own tools, or transcript; do not depend on a child sending to the literal recipient `"parent"`. If the child fails, the primary must continue or return partial instead of hanging.

## Retry

Retry once only when evidence shows:

- transient service or capacity failure;
- clear prompt/argument parsing drift;
- a recoverable runtime failure;
- or one large-fix corrective resume/fresh run after supervisor review.

Do not retry login, consent, secret, or genuinely new-authority blockers. Do not loop through models speculatively. Do not auto-resume more than once.
