# AGY Work Orders

Use the smallest prompt that preserves independent reasoning and explicit authority.

## Template

```text
ROLE
You are the independent engineer responsible for this task.

GOAL
[One observable outcome.]

FACTS
- Workspace: [absolute path]
- Inputs: [files, symptoms, issue, or sources]
- Verified state: [known evidence]

UNKNOWNS
- [Questions AGY should investigate independently]

CONSTRAINTS
- [Real product or technical constraints]
- Do not assume the current implementation or supervisor hypothesis is correct.

ALLOWED EFFECTS
- Read: any relevant file inside the workspace
- Write: [exact scope or NONE]
- Commands: [exact checks or NONE]
- External effects: NONE unless explicitly listed
- Subagents: ALLOW at most one read-only subagent by default when useful. Subagent Write: NONE. The primary AGY owns lifecycle, coordination, and the final handoff.

FORBIDDEN
- Preserve unrelated and pre-existing changes.
- No secrets, login, consent, permission broadening, destructive actions,
  commits, pushes, publishing, deployment, messaging, purchases, or
  production mutation unless explicitly authorized above.

DONE WHEN
- [Observable criterion]
- [Exact check and expected result]

WORK METHOD
Build your own evidence-based model of the problem. Check foundation and
assumptions before adding workarounds. Make the smallest correct change.
Use subagents when they add useful independent analysis, review, or parallel
checks. Require concise findings, keep all writes in the primary, collect
child status/result directly, and continue or return partial if a child fails.

HANDOFF
Return only a JSON object matching the provided handoff schema:
status, summary, evidence, changes, checks, uncertainty, next.
```

## Construction Rules

1. State outcomes, facts, and real constraints; do not transmit the supervisor's whole reasoning history.
2. Separate verified facts from hypotheses and open decisions.
3. Do not ask AGY to confirm a preferred answer unless comparison of that answer is the actual task.
4. Grant workspace discovery broadly and mutations narrowly.
5. Keep one mission and one foreground primary AGY per invocation. Allow at most one read-only subagent by default and require the primary to consolidate its work.
6. Require evidence in the handoff, then verify it independently.

## Read-Only Order

Use `--mode plan`, `Write: NONE`, and ask for cited evidence, uncertainty, and a recommendation only when the evidence supports one.

## Edit Order

Use `--mode accept-edits`. Name the writable scope and exact checks. Ask for the smallest correct diff, not speculative cleanup or abstractions.

## Corrective Retry

Retry once only for a transient runtime error or clear prompt/argument drift:

```text
Continue the same goal and authority boundary.
The prior run was incomplete because: [specific evidence].
Complete the task and return only the required handoff.
```

Do not retry login, consent, secret, or genuinely new-authority blockers.
