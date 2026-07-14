# Herdr Runtime For AGY

Use Herdr for long-running AGY work, live progress inspection, disconnect survival, and attended permission or review flows. Keep short bounded automation and CI in a foreground `agy -p` process.

Herdr is a persistent terminal and control plane. Its AGY state is derived from the foreground process and terminal-screen rules, so `working`, `blocked`, `idle`, and `done` are attention signals rather than proof of completion.

## Preflight

Before launch:

1. Run `command -v herdr`, `herdr --version`, and `herdr status`.
2. Require compatible client/server protocol versions. If no server is running, start a temporary one with `herdr server`; do not enable a login service merely for one job.
3. Run the normal AGY health gate and select an explicitly healthy Gemini 3.5 tier.
4. Choose a unique agent name, workspace, log path, authority boundary, monitoring cadence, and cleanup owner.
5. Inspect `herdr agent list` to avoid reusing an active name.
6. Capture a workspace baseline appropriate to the job before dispatch. In Git repositories, include status and a tracked-diff hash; if relevant untracked files already exist, hash their in-scope contents too. A status-only snapshot cannot prove that an existing untracked file was unchanged.

If the AGY TUI opens first-run theme, login, terms, telemetry, or privacy onboarding, do not select an option. Preserve or close the temporary session as appropriate, report the exact screen, and require the user to complete onboarding in an attended attach before retrying the work order.

## Launch

Launch an attended AGY TUI with the initial prompt immediately after `--prompt-interactive`:

```bash
herdr agent start 'agy-<job>-<slug>' --cwd '<WORKSPACE>' -- \
  agy --model '<HEALTHY_MODEL>' --prompt-interactive '<WORK_ORDER>' \
  --mode plan --sandbox --log-file '<UNIQUE_LOG>'
```

For authorized edits, use `--mode accept-edits` and the same bounded write, command, network, and verification contract used by foreground execution. Never pass `--dangerously-skip-permissions`.

Do not wrap Herdr inside tmux. Herdr cannot inspect an AGY process hidden behind a nested multiplexer.

## Reuse An Idle Session

Reuse a healthy authenticated TUI when the next work order belongs to the same workspace and trust boundary. Start a fresh session for unrelated context, a different worktree, changed authority, or a model-health failure.

Resolve the pane, record a new per-job workspace baseline, then dispatch exactly one work order:

```bash
herdr agent get 'agy-<job>-<slug>'
herdr pane run '<PANE_ID>' '<WORK_ORDER>'
```

Do not send a second Enter: `pane run` submits the text. After dispatch:

1. Read the terminal until the new prompt is visible or AGY activity is appended.
2. Wait boundedly for `working` before waiting for `idle`; otherwise an already-idle session may satisfy the completion wait before the prompt starts.
3. If the `working` transition was too brief to observe, confirm both the new prompt and a complete new handoff in appended terminal output. Do not resend blindly.
4. Treat each work order as a separate monitored run with its own baseline, acceptance contract, verification, and redacted observation.

Example transition gate:

```bash
herdr agent wait 'agy-<job>-<slug>' --status working --timeout 10000
herdr agent wait 'agy-<job>-<slug>' --status idle --timeout 60000
```

Timeouts are observations, not automatic failures. Read the terminal, log, and process information before retrying or stopping.

## Observe

Use the stable agent name for high-level operations:

```bash
herdr agent get 'agy-<job>-<slug>'
herdr agent read 'agy-<job>-<slug>' --source recent-unwrapped --lines 120
herdr agent explain 'agy-<job>-<slug>' --json
```

Use `herdr agent wait <name> --status blocked --timeout <milliseconds>` only as one bounded observation. A timeout means the requested state was not observed; it does not mean the worker failed. Re-read the terminal and log before deciding that AGY stalled.

Interpret state conservatively:

- `working`: AGY displays a recognized activity signal; inspect terminal/log for objective drift.
- `blocked`: a known visible permission prompt needs attended review.
- `idle`: no working or blocker rule matched; AGY may be ready, waiting, or in an unrecognized screen state.
- `done`: Herdr observed a completed attention transition; verification is still pending.
- `unknown`: inspect process information, terminal output, and log directly.

Attach only when interaction is needed:

```bash
herdr agent attach 'agy-<job>-<slug>'
```

Never accept login, terms, telemetry, privacy, browser authorization, or broader permissions for the user. Never send speculative input merely to make a status change.

## Stall Triage

Silence alone is not a failure. When output appears stale:

1. Read recent unwrapped terminal output and the persistent AGY log.
2. Run `herdr agent get` and `herdr agent explain --json`.
3. Resolve the pane id from `herdr agent get`, then inspect it with `herdr pane process-info <pane-id>`.
4. Distinguish a visible permission wait, active recognized spinner/task, completed handoff, process exit, and unrecognized silent state.
5. Attach for attended inspection when ambiguity remains. Stop only for unsafe behavior, scope drift, or a confirmed failed process.

## Completion And Cleanup

Do not accept Herdr state as the result. Before cleanup:

1. Preserve the AGY handoff, terminal output, unique log, and any failure evidence.
2. Confirm the task is not waiting for input or approval.
3. Compare the workspace to the baseline captured immediately before this work order, independently inspect all changed files, and run the acceptance checks. A baseline captured only before a corrective retry says nothing about the original attempt.
4. Resolve the agent to its pane, close that pane only when no further interaction is needed, and confirm it disappears from `herdr agent list`.
5. Stop the Herdr server only if this workflow started a temporary server and no other workspace, tab, pane, or agent is using it.

If the pane exited before a usable handoff, classify the AGY run from its log and captured output. If Herdr state disagrees with the terminal, preserve `herdr agent explain --json` as detection evidence and trust direct process, terminal, log, and workspace evidence instead.

For a completed work order in a still-running TUI, capture only the job-specific terminal segment, redact sensitive values, and record it with `scripts/classify_run.py --verified-interactive --record`. Use this flag only after the return contract is complete and the supervisor has independently verified the result. The ledger records `exit_code: null`; never pass a fabricated zero for a process that has not exited.
