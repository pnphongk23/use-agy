# AGY Orchestration

Use this reference to turn a user goal into a supervised execution strategy. Optimize for outcome quality, observability, recoverability, and minimal coordination overhead.

## Table Of Contents

- Delegation decision
- Task decomposition
- Runtime selection
- Tmux lifecycle
- Concurrency and worktrees
- Monitoring and intervention
- Completion protocol

## Delegation Decision

Before invoking AGY, score the job informally:

| Dimension | Low | High |
|---|---|---|
| Duration | seconds/minutes | long build, sweep, migration |
| Uncertainty | known file/change | discovery or root cause unknown |
| Risk | read-only/reversible | broad writes, external effects |
| Interactivity | no approvals | review/permissions likely |
| Concurrency | one linear result | independent jobs can overlap |
| Verification | one cheap check | multiple checks/manual evidence |
| Disconnect risk | stable local process | SSH, overnight, laptop workflow |

Use AGY when a bounded worker can materially reduce time, provide independent evidence, or execute local tooling. Do not use AGY when direct completion is faster, context is too sensitive, the task is an ambiguous product decision, or verification would cost more than doing the work.

## Task Decomposition

Choose one of three plans:

### Single-pass

Use for low-risk, localized work with a clear acceptance command:

```text
EXECUTE -> supervisor VERIFY
```

### Gated

Use for uncertain or risky changes:

```text
EXPLORE/DIAGNOSE -> supervisor reviews evidence
-> EXECUTE approved plan -> independent VERIFY
```

### Parallel

Use only for independent work:

```text
worker A: bounded scope A ----\
worker B: bounded scope B -----+-> supervisor consolidates -> VERIFY
worker C: bounded scope C ----/
```

Do not parallelize coupled changes, shared files, sequential dependencies, or jobs whose outputs require constant cross-coordination.

## Runtime Selection

### Foreground one-shot: default

Use when:

- expected duration is short;
- caller needs stdout and exit status immediately;
- no human approval is expected;
- CI or another process runner already owns lifecycle;
- only one worker is active.

Benefits: simplest, observable, easy error propagation, easy cleanup.

### Detached tmux one-shot

Use when:

- work is long-running or may outlive the current terminal;
- SSH/disconnect risk matters;
- the supervisor can poll pane/log output;
- the job should continue without continuous interaction.

Require: unique session name, correct working directory, persistent log, captured exit marker, monitoring plan, and cleanup owner.

Do not use merely because tmux is installed. Avoid for short jobs, CI, structured-output pipelines, or jobs likely to block on approval.

### Attended TUI, optionally in tmux

Use when:

- planning/artifact review is valuable;
- permissions or course correction are expected;
- a human wants to inspect subagents/tasks;
- an interactive session must survive disconnects.

Do not automate onboarding, consent, login, telemetry, or privacy choices.

### Conversation selection

Start fresh for isolated reproducible work. Use `--continue` only when latest directory-scoped context is intentional. Use `--conversation <id>` for an exact prior thread. Avoid resuming stale context for unrelated work.

## Tmux Lifecycle

Treat tmux as a process supervisor, not a quality mechanism.

Before launch:

1. Choose a collision-resistant name such as `agy-<job>-<slug>`.
2. Confirm no existing session owns that name.
3. Set the intended workspace/worktree as session working directory.
4. Choose a log path that contains no secrets.
5. Define timeout, poll method, approval strategy, and cleanup owner.

During execution:

1. Poll `tmux capture-pane` or persistent logs.
2. Detect no-output stalls, repeated loops, permission prompts, and objective drift.
3. Attach only when interaction is required; otherwise preserve reproducibility.
4. Stop the worker when behavior becomes unsafe or out of scope.

At completion:

1. Capture final output and actual exit status.
2. Confirm the AGY process is no longer running.
3. Independently inspect workspace changes and run acceptance checks.
4. Preserve useful logs, then remove the session when no longer needed.

Remember: tmux persistence does not override AGY's `--print-timeout`.

## Concurrency And Worktrees

Parallel read-only workers may share a checkout when their commands do not mutate caches, generated outputs, lockfiles, or build artifacts.

Parallel writing workers must use isolated worktrees or separate checkouts. Assign:

- disjoint file ownership;
- unique branch/worktree;
- unique tmux session and log;
- independent acceptance contract;
- explicit integration order.

Before integration, review each diff against the latest base. Resolve overlap deliberately; never merge worker output merely because its tests passed in isolation.

Prefer sequential work when worktree setup and integration cost exceed expected time saved.

## Monitoring And Intervention

Track each worker as:

```text
planned -> running -> waiting | done | failed | stopped
```

Interpret states:

- `waiting`: permission, auth, consent, missing input, or review required.
- `done`: process exited and handoff exists; verification still pending.
- `failed`: timeout, nonzero exit, invalid handoff, or unmet acceptance.
- `stopped`: supervisor interrupted unsafe, drifting, or obsolete work.

Intervene when:

- output no longer addresses the mission;
- commands/paths/domains exceed authority;
- AGY claims success without evidence;
- expected output is silent beyond a reasonable task-specific interval;
- a worker modifies another worker's ownership;
- new information invalidates the plan.

Use steering proportional to the problem:

1. Clarify one missing fact or contract field.
2. Stop and issue one shorter corrective retry for objective drift.
3. Re-plan or work directly after repeated failure.
4. Ask the user only for genuinely new authority or a material decision.

## Completion Protocol

The supervisor may report completion only when:

- every required dependency is resolved;
- every worker is done, failed and handled, or intentionally stopped;
- no required foreground process or tmux session remains unobserved;
- changed files and sources have been independently inspected;
- acceptance checks have been rerun or inability is disclosed;
- temporary worktrees/sessions have an explicit retained-or-cleaned state;
- user-facing report separates AGY claims from supervisor verification.
