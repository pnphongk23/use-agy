# AGY Orchestration

Use this reference to turn a user goal into a supervised execution strategy. Optimize for outcome quality, observability, recoverability, and minimal coordination overhead.

## Table Of Contents

- Delegation decision
- Task decomposition
- Runtime selection
- Herdr lifecycle
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

### Herdr-managed attended TUI: default

Use when:

- AGY receives any normal delegated research, diagnosis, implementation, or verification job;
- the supervisor benefits from persistent evidence, live inspection, bounded steering, or safe permission pauses;
- a session may be reused for sequential bounded work orders in one workspace and trust boundary.

Use `scripts/orchestrate_herdr.py` for preflight, launch, observation, evidence capture, and cleanup. Herdr is the runtime default, not proof of correctness or expanded authority.

### Foreground one-shot: exception

Use when:

- CI or a caller requires the real child-process exit status;
- machine-readable stdout is the acceptance interface;
- the user explicitly asks for a one-shot process;
- Herdr is unavailable or incompatible and the supervisor discloses the fallback;
- an extremely small latency-sensitive task clearly costs less to run than to establish a persistent session.

Do not choose foreground merely because a task is short. Preserve the same explicit model, unique log, authority envelope, bounded retries, and independent verification.

### Attended TUI in Herdr

Use when:

- planning/artifact review is valuable;
- permissions or course correction are expected;
- a human wants to inspect subagents/tasks;
- an interactive session must survive disconnects.

Do not automate onboarding, consent, login, telemetry, or privacy choices.

### Conversation selection

Start fresh for isolated reproducible work. Use `--continue` only when latest directory-scoped context is intentional. Use `--conversation <id>` for an exact prior thread. Avoid resuming stale context for unrelated work.

## Herdr Lifecycle

Treat Herdr as a persistent terminal and control surface, not a quality mechanism. Prefer the orchestration helper described in [herdr-runtime.md](herdr-runtime.md); use the manual commands only when diagnosing the helper itself or when a verified compatibility issue requires it.

Before launch:

1. Confirm the Herdr client/server versions are compatible.
2. Choose a collision-resistant agent name such as `agy-<job>-<slug>` and confirm it is unused.
3. Set the intended workspace/worktree as session working directory.
4. Choose a log path that contains no secrets.
5. Define timeout, poll method, approval strategy, and cleanup owner.

During execution:

1. Capture a fresh workspace baseline immediately before every bounded work order, including follow-ups in a reused session.
2. Poll `herdr agent get`, `herdr agent read`, and the persistent AGY log.
3. For a reused idle TUI, confirm prompt acceptance and observe `working` before waiting for a new `idle`; inspect appended terminal output when a brief transition is missed.
4. Use `herdr agent explain` when screen-derived state conflicts with the visible terminal.
5. Treat `working`, `blocked`, `idle`, and `done` as attention hints, not proof of task success or process exit.
6. Attach only when interaction is required; never auto-approve login, consent, or expanded permissions.
7. Stop the worker when behavior becomes unsafe or out of scope.

At completion:

1. Capture the final terminal output, AGY log, handoff, and process state.
2. Compare against the baseline for that exact work order, independently inspect workspace changes, and run acceptance checks.
3. Record a redacted per-work-order observation; use a verified-interactive record when the TUI remains alive instead of fabricating an exit code.
4. Close the pane only after evidence is preserved and no interaction remains necessary.
5. Confirm the agent no longer appears in `herdr agent list`; stop a temporary Herdr server if this workflow started it.

Remember: Herdr screen detection for AGY is heuristic. A visible `idle` or `done` state never replaces the handoff and independent verification.

## Concurrency And Worktrees

Parallel read-only workers may share a checkout when their commands do not mutate caches, generated outputs, lockfiles, or build artifacts.

Parallel writing workers must use isolated worktrees or separate checkouts. Assign:

- disjoint file ownership;
- unique branch/worktree;
- unique Herdr agent and log;
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
- no required foreground process or Herdr session remains unobserved;
- changed files and sources have been independently inspected;
- acceptance checks have been rerun or inability is disclosed;
- temporary worktrees/sessions have an explicit retained-or-cleaned state;
- user-facing report separates AGY claims from supervisor verification.
