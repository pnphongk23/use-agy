---
name: paseo-handoff
description: Hand off the current task to another agent with full context. Use when the user says "handoff", "hand off", "hand this to", or wants to pass work to another agent.
user-invocable: true
---

# Handoff Skill

Transfer the current task — context, decisions, failed attempts, constraints — to a fresh agent. The receiving agent starts with **zero context**, so the handoff prompt must be a self-contained briefing.

**User's arguments:** $ARGUMENTS

## Prerequisites

Read the **paseo** skill. Before choosing a provider, read `~/.paseo/orchestration-preferences.json` unless the user explicitly named a provider in this request. Do not create the receiving agent until you have read it.

## Parsing arguments

1. **Provider** — explicit user request first; otherwise resolve from `impl` preference (or `ui` if the task is styling-only).
2. **Isolation** — "in a worktree" / "worktree" → create a workspace with `isolation: "worktree"`, using a short branch name derived from the task.
3. **Task description** — anything else the user said.

## The handoff prompt

The receiving agent has zero context. Include:

```
## Task
[Imperative description.]

## Context
[Why this task exists, required context.]

## Relevant files
- `path/to/file.ts` — [what it is and why it matters]

## Current state
[What's done, what works, what doesn't.]

## What was tried
- [Approach] — [why it failed or was abandoned]

## Decisions
- [Decision — rationale]

## Acceptance criteria
- [ ] [Criterion]

## Constraints
- [Must-not / must-preserve]
```

**Preserve task semantics.** Investigate-only → "DO NOT edit files." Fix → "implement the fix." Refactor → "refactor, not rewrite." Carry the user's exact intent.

## Launch

Prepare the handoff in a dedicated workspace:

1. Select the current workspace or call `create_workspace` with the requested isolation.
2. Call `create_agent` with a `[Handoff] <task>` title, the briefing as initial prompt, and the selected `workspaceId` when explicit placement is needed. Keep `notifyOnFinish: true` so the parent receives the completion callback.
3. Return the agent and workspace to the user, explaining that it remains in your subagent track until they detach it manually.

Do not encode independence as a create mode and do not invoke CLI or wire-level detach operations. Detach is a user gesture in the subagents track.

## Completion contract

The receiving agent must end its final response with this exact five-line report. Use one literal status token and keep one field per line:

```text
status: done
summary: <what was actually completed>
artifacts: <files, commits, or none>
checks: <commands/results, or not run>
limitations: <remaining uncertainty or none>
```

`status: done` means every acceptance criterion is verified. Use `status: partial` when the agent returned but one or more criteria remain incomplete or unverifiable. Use `status: blocked` only when the agent cannot continue because of a concrete external blocker (for example, a missing permission or unavailable provider).

The callback is a notification, not proof of success. The parent must inspect the reported artifacts and checks before claiming `done`. If the callback is missing while the agent is still reachable, report `partial` with the agent ID and last known state; use `blocked` only when a concrete provider, permission, or daemon failure prevents progress.

## Optional hierarchy

Use the current agent as **leader**, and only add the following hierarchy when the task benefits from coordination:

```text
leader
└── supervisor
    ├── peer: implementation
    ├── peer: tests
    └── peer: review
```

The supervisor creates its peers with `create_agent` and gathers their reports before replying to the leader. `workspaceId` controls where a child works; it does not change who its parent is. Labels such as `role=supervisor` and `role=peer` are descriptive only and do not grant permissions.

Set `notifyOnFinish: false` only when the user explicitly wants no callback.

Don't wait by default — the user decides whether to follow along or move on. Tell them the agent ID and how to follow along (the paseo skill explains).
