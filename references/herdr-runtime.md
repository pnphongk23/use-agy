# Herdr Runtime For AGY

Read [herdr-upstream-skill.md](herdr-upstream-skill.md) first. The installed Herdr binary is the syntax authority; the vendored upstream skill supplies the control model, while this file narrows it for the externally supervised AGY lifecycle.

When the user explicitly invokes `$use-agy` or requests Herdr, use Herdr as the default runtime for delegated AGY work, including short bounded jobs. A mere AGY mention does not authorize external Herdr control; use foreground `agy -p`. Even with Herdr authority, foreground remains appropriate for direct-exit-status/structured-output workflows, an explicit user request, a disclosed Herdr incompatibility, or an exceptionally small latency-sensitive job.

Herdr is a persistent terminal and control plane. Its AGY state is derived from the foreground process and terminal-screen rules, so `working`, `blocked`, `idle`, and `done` are attention signals rather than proof of completion.

## Preferred Automated Lifecycle

Run from the `use-agy` skill directory, or use absolute script paths:

```bash
python3 scripts/orchestrate_herdr.py prepare \
  --workspace '<WORKSPACE>' --run-dir '<RUN_DIR>' \
  --herdr-authorized \
  --evidence-scope '<PATH_TO_HASH_FOR_BASELINE>'
python3 scripts/orchestrate_herdr.py launch \
  --run-dir '<RUN_DIR>' --prompt-file '<ORDER>' --mode plan
python3 scripts/orchestrate_herdr.py observe \
  --run-dir '<RUN_DIR>' --timeout 600
python3 scripts/orchestrate_herdr.py snapshot --run-dir '<RUN_DIR>'
python3 scripts/orchestrate_herdr.py record \
  --run-dir '<RUN_DIR>' --job '<JOB_TYPE>'
python3 scripts/orchestrate_herdr.py cleanup --run-dir '<RUN_DIR>'
```

Use `accept-edits` for an authorized implementation. `--herdr-authorized` is a required assertion that the user explicitly invoked `$use-agy` or requested Herdr; a mere AGY mention is insufficient. `prepare` creates the run directory, selects a healthy Gemini 3.5 model, captures a symlink-safe baseline, and pins the compatible Herdr client version, server version, protocol, socket path, and socket inode identity. It records whether it started the server. `--evidence-scope` controls which existing untracked content is hashed for evidence; it never restricts AGY's read access. The legacy `--scope` spelling remains an alias.

`launch` creates a run-owned workspace with `--no-focus`, parses its returned workspace/tab/bootstrap-pane IDs, starts AGY by those exact IDs, verifies and closes only the bootstrap pane, and requires the remaining owned pane to fill the workspace. It binds the approved workspace with both Herdr `--cwd` and AGY `--add-dir`. Every later control call revalidates the pinned Herdr runtime and uses the manifest pane ID rather than agent name or UI focus.

`observe` uses terminal output only for progress, attention, and diagnostic capture. It obtains the conversation UUID from the owned AGY job log, opens only `~/.gemini/antigravity-cli/conversations/<UUID>.db` read-only, and accepts completion only when the raw response contains the exact generated begin/end marker lines plus structured status. It persists that unwrapped block as helper-owned `handoff-<N>.txt`, so handoff length is not limited by terminal lines or scrollback and no AGY write authority is added. A completed AGY stream without a valid raw block becomes `malformed_handoff` and exits `2`; terminal wrapping, scrollback loss, or `idle`/`done` never causes completion or a corrective handoff retry. Exit `0` means raw handoff captured, `20` means attended input, and `21` means bounded observation timeout.

Lifecycle commands use a fail-fast per-run lock: a competing helper exits `2` with the owning PID and command instead of waiting behind a long `observe`. Terminal diagnostic snapshots are capped independently of handoff size. `dispatch` reuses an owned idle session only after the prior job was recorded, durably marks dispatch pending before sending exactly once, and creates a new per-job baseline. `snapshot` is allowed only after raw handoff capture or an attention state; it will not synthesize a missing handoff from terminal text. After independent verification, `record` preserves `done`, `partial`, or `blocked`. `cleanup` revalidates the runtime and exact workspace/tab/pane/terminal ownership, refuses unexpected panes, closes only the run-owned workspace, and stops a run-started server only when unused; keeping an agent implicitly keeps that server.

The helper never approves trust, onboarding, login, consent, telemetry, privacy, or permissions. Inspect the terminal file before acting on exit `20`. Use manual commands below for diagnosis or recovery, not as the routine path.

If a helper is interrupted, inspect `.orchestration.lock` and the process table before sending a signal. Stop only the exact stale helper PID recorded there; do not stop the owned AGY pane merely to release a helper lock. A stale metadata record without a live lock is harmless and will be replaced by the next lifecycle command.

## Preflight

Before launch:

1. Run `command -v herdr`, `herdr --version`, `herdr --help`, and `herdr status`. Discover a relevant command group through its non-mutating group help; never run bare `herdr` for discovery and never probe a potentially mutating nested command by omitting required-looking arguments.
2. Require compatible client/server protocol versions. If no server is running, start a temporary one with `herdr server`; do not enable a login service merely for one job.
3. Run the normal AGY health gate and select an explicitly healthy Gemini 3.5 tier.
4. Choose a unique agent name, workspace, log path, effect boundary, monitoring cadence, output-review strategy, and cleanup owner.
5. Inspect `herdr agent list` to avoid reusing an active name.
6. Capture a workspace baseline appropriate to the job before dispatch. In Git repositories, include status and a tracked-diff hash; if relevant untracked files already exist, hash their in-scope contents too. A status-only snapshot cannot prove that an existing untracked file was unchanged.
7. Let the helper create an unfocused run-owned workspace and parse every returned workspace/tab/pane/terminal ID as opaque. It inspects the bootstrap layout to choose a usable split, closes the bootstrap only after identity verification, and verifies the final AGY pane occupies the whole workspace. Never synthesize IDs or rely on UI focus.

If the AGY TUI opens first-run theme, login, terms, telemetry, or privacy onboarding, do not select an option. Preserve or close the temporary session as appropriate, report the exact screen, and require the user to complete onboarding in an attended attach before retrying the work order.

## Launch

The helper may control Herdr from outside a managed pane only because the user explicitly invoked `$use-agy` or asked for Herdr and `prepare` received `--herdr-authorized`. It binds itself to a verified server/socket plus run-owned IDs. This is the deliberate adapter exception to upstream's standalone `HERDR_ENV=1` rule. Manual ad-hoc control outside Herdr is not covered by that exception.

When diagnosing the helper, reproduce its explicit topology rather than relying on the focused pane. First create the workspace and record the returned IDs:

```bash
herdr workspace create --cwd '<WORKSPACE>' --label 'agy-<job>-<slug>' --no-focus
herdr pane layout --pane '<BOOTSTRAP_PANE_ID>'
herdr agent start 'agy-<job>-<slug>' --cwd '<WORKSPACE>' \
  --workspace '<WORKSPACE_ID>' --tab '<TAB_ID>' \
  --split '<right|down>' --no-focus -- \
  agy --model '<HEALTHY_MODEL>' --add-dir '<WORKSPACE>' \
  --prompt-interactive '<WORK_ORDER>' --mode plan --log-file '<UNIQUE_LOG>'
```

After verifying the returned AGY pane/terminal IDs, close exactly `<BOOTSTRAP_PANE_ID>` and confirm that the AGY pane is the only pane and fills the layout. Routine runs must use the helper instead of this manual sequence.

For authorized edits, use `--mode accept-edits` and the same bounded write, command, network, and verification contract used by foreground execution. Add `--sandbox` only when the command trust boundary needs containment and the requested behavior remains valid inside it. Never pass `--dangerously-skip-permissions`.

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
3. If the `working` transition was too brief to observe, confirm that the new prompt/activity was appended, then wait for the new raw conversation handoff. Do not resend blindly and do not use a terminal marker as completion evidence.
4. Treat each work order as a separate monitored run with its own baseline, acceptance contract, verification, and redacted observation.

Example transition gate:

```bash
herdr agent wait 'agy-<job>-<slug>' --status working --timeout 10000
herdr agent wait 'agy-<job>-<slug>' --status idle --timeout 60000
```

Timeouts are observations, not automatic failures. Read the terminal, log, and process information before retrying or stopping.

## Observe

Use the manifest-owned pane ID for terminal and process operations. Agent-name operations are supplementary diagnostics only. The `--lines` value below caps a diagnostic terminal sample; it is not a handoff limit and must not be increased as a strategy for finding completion:

```bash
herdr pane get '<PANE_ID>'
herdr pane read '<PANE_ID>' --source recent-unwrapped --lines 120
herdr pane process-info --pane '<PANE_ID>'
herdr agent explain 'agy-<job>-<slug>' --json
```

Prefer explicit pane/agent IDs resolved from the run manifest. Never omit a target in a way that can fall back to the UI-focused pane. Inspect current output before waiting for a future transition.

Use `herdr agent wait <name> --status blocked --timeout <milliseconds>` only as one bounded observation. A timeout means the requested state was not observed; it does not mean the worker failed. Re-read the terminal and log before deciding that AGY stalled.

Interpret state conservatively:

- `working`: AGY displays a recognized activity signal; inspect terminal/log for objective drift.
- `blocked`: a known visible permission prompt needs attended review.
- `idle`: no working or blocker rule matched; AGY may be ready, waiting, or in an unrecognized screen state.
- `done`: Herdr observed a completed attention transition; verification is still pending.
- `unknown`: inspect process information, terminal output, and log directly.

`idle` and `done` represent the same semantic waiting/completion state with different seen/unseen attention. Focus can turn `done` into `idle`; therefore neither state proves that a marker or complete handoff is present. `recent-unwrapped` joins soft terminal wraps only. It cannot reconstruct text that the child TUI itself hard-wrapped, truncated, or replaced. Exact completion evidence comes from the raw AGY conversation response identified by the owned job log; the helper writes it to `handoff-<N>.txt` without imposing a line limit.

Attach only when interaction is needed:

```bash
herdr agent attach 'agy-<job>-<slug>'
```

Never accept login, terms, telemetry, privacy, browser authorization, or broader permissions for the user. Never send speculative input merely to make a status change.

## Stall Triage

Silence alone is not a failure. When output appears stale:

1. Read recent unwrapped terminal output and the persistent AGY log.
2. Run `herdr agent get` and `herdr agent explain --json`.
3. Read the manifest-owned pane id, then inspect it with `herdr pane process-info --pane '<PANE_ID>'`.
4. Distinguish a visible permission wait, active recognized spinner/task, completed handoff, process exit, and unrecognized silent state.
5. Attach for attended inspection when ambiguity remains. Stop only for unsafe behavior, mission/effect drift, or a confirmed failed process.

## Completion And Cleanup

Do not accept Herdr state as the result. Before cleanup:

1. Preserve the helper-owned raw AGY handoff, terminal output, unique log, handoff-source metadata, and any failure evidence.
2. Confirm the task is not waiting for input or approval.
3. Compare the workspace to the baseline captured immediately before this work order, independently inspect all changed files, and run the acceptance checks. A baseline captured only before a corrective retry says nothing about the original attempt.
4. Review the output claim by claim: open cited files or primary sources, confirm relationships and stated absences, reject invented or unsupported evidence, and attempt to falsify consequential conclusions.
5. Let cleanup revalidate the pinned socket and exact workspace/tab/pane/terminal tuple; it closes the run-owned workspace only if no unexpected pane exists.
6. Stop the Herdr server only if this workflow started it and no other workspace, tab, pane, or agent is using it.

If the pane exited or the stream completed before a valid raw handoff, classify and preserve the run as a contract/evidence failure; do not rerun the substantive job merely to repair markers. Do not request a "shorter handoff" after completion; use the preserved malformed evidence to fix the contract or helper. If Herdr state disagrees with the terminal, preserve `herdr agent explain --json` as detection evidence and trust direct process, raw conversation, log, and workspace evidence instead.

For a completed work order in a still-running TUI, capture only the job-specific terminal segment, redact sensitive values, and prefer `orchestrate_herdr.py record`. A manual classifier call must include `--verified-interactive --interactive-status <done|partial|blocked> --record`. Record only after the handoff and independent verification. The ledger records `exit_code: null`; never pass a fabricated zero for a process that has not exited.
