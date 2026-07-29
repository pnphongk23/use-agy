# AGY Foreground CLI

Use the installed binary as the syntax authority:

```bash
agy --version
agy --help
```

## Normal Invocation

Put the prompt immediately after `-p`:

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

Use `--mode accept-edits` for authorized implementation.

- Run from the approved workspace and pass the same path with `--add-dir`.
- Start fresh by default. Do not pass `--continue` or `--conversation` unless the user requests prior context.
- Let normal AGY configuration select the model. Run `agy models` only when model selection matters or after a diagnosed model failure.
- Use `--effort` only when the user or task needs a deliberate override.
- Use `--dangerously-skip-permissions` for authorized headless work when permission prompts would otherwise soft-deny tools.

The flag auto-approves CLI tool requests. It removes runtime friction but does not authorize effects omitted from the work order.

## Models

Run `agy models` and pass an exact listed slug with `--model` when selection matters.

- `gemini-3.6-flash-low`: extraction and cheap routine work.
- `gemini-3.6-flash-medium`: balanced default for normal coding and review.
- `gemini-3.6-flash-high`: difficult diagnosis, architecture, or high-risk review.

Other listed families may be selected when the user asks. Use `--effort low|medium|high` only when choosing effort separately instead of using an effort-specific model slug.

## Process Contract

The foreground process provides:

- real process lifetime;
- exit code;
- live NDJSON events;
- stderr/runtime diagnostics;
- a unique log for targeted failure investigation.

AGY 1.1.8 adds `--output-format json|stream-json` and `--json-schema`. Prefer `stream-json`: it exposes progress, subagent metadata, token usage, and a terminal result without waiting blindly for process exit. Exit `0` is not sufficient.

## Monitoring

Keep the process handle and consume stdout NDJSON live. Do not redirect it to a file as the only consumer.

- `init`: capture the conversation ID.
- `step_update`: keep compact progress summaries; do not ingest raw tool payloads.
- `subagent_info`: track child conversation IDs and log URIs.
- `result`: validate status, response schema, usage, and error.

After the terminal result, do not keep waiting on a stuck child lifecycle. Stop the owned process if it does not exit promptly. On failure or a stalled stream, inspect the conversation plus relevant log excerpts:

```bash
tail -n 120 '<LOG>'
rg -n 'permission|denied|not logged|quota|capacity|error|timeout' '<LOG>'
```

## Subagents

Encourage at most one read-only subagent by default for separable exploration or independent review. Require a concise result. The primary should collect child status/result through subagent metadata or transcript; do not depend on a child sending to the literal recipient `"parent"`. If the child fails, the primary must continue or return partial instead of hanging.

## Retry

Retry once only when evidence shows:

- transient service or capacity failure;
- clear prompt/argument parsing drift;
- a recoverable runtime failure.

Do not retry login, consent, secret, or genuinely new-authority blockers. Do not loop through models speculatively.
