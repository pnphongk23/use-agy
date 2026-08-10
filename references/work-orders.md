# AGY Work Orders

Use the smallest prompt that preserves independent reasoning and explicit authority.

Write that prompt into one `<RUN_DIR>/handoff.md` file. If a native AGY skill is available, start the file with `/<skill-slug>`. If it is not, embed only the necessary project rules directly in the same handoff file.

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
- Never run dev servers, watchers, tail -f, or other non-terminating commands in AGY foreground.

PROJECT RULES
- Native skill: [exact slug, or NONE]
- Embedded rules: [only the task-applicable instructions AGY must follow when native skill is absent]
- Critical invariants: [task-applicable rules]
- Approved fallbacks: [exact alternatives, or NONE]
- Forbidden substitutions: [changes that require renewed approval]
- Stop/block conditions: [semantic failures that prohibit final output]

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
- Do not run servers or watchers in the foreground. If a server/service is required for checks, start it in the background, run verification commands (e.g. curl), and ensure it is cleaned up before finishing.

DONE WHEN
- [Observable criterion]
- [Exact check and expected result]
- Return only JSON matching the provided handoff schema.

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

## Skill Activation

Do not put `Read <skill> SKILL.md` in a handoff and treat it as activation.

- Native path: preflight the exact slug, then begin `handoff.md` with `/<slug>` followed by the work order.
- Fallback path: the supervisor reads the source skill/references and embeds only the necessary task-specific rules directly in `handoff.md`.
- Mandatory-native path: if the exact skill is unavailable and embedded rules are insufficient, return blocked before implementation.

## Construction Rules

1. State outcomes, facts, and real constraints; do not transmit the supervisor's whole reasoning history.
2. Separate verified facts from hypotheses and open decisions.
3. Do not ask AGY to confirm a preferred answer unless comparison of that answer is the actual task.
4. Grant workspace discovery broadly and mutations narrowly.
5. Keep one mission and one foreground primary AGY per invocation. Allow at most one read-only subagent by default and require the primary to consolidate its work.
6. Require evidence in the handoff, then verify it independently.
7. Pass the mission through `handoff.md` (or `corrective-handoff.md`) only. Keep the full task briefing self-contained in that file.
8. Explicitly forbid non-terminating foreground commands (like `npm run dev`, `python3 -m http.server`, `webpack --watch`) in the work order. If server verification is necessary, specify starting a background process, verifying it (e.g., via a health check endpoint or curl), and cleaning it up (killing the PID) before completion.

## Read-Only Order

Use `--mode plan`, `Write: NONE`, and ask for cited evidence, uncertainty, and a recommendation only when the evidence supports one. The supervisor materializes `report.md` from terminal structured output after exit; do not ask AGY to write the report file in plan mode.

## Edit Order

Use `--mode accept-edits`. Name the writable scope and exact checks. Ask for the smallest correct diff, not speculative cleanup or abstractions.

## Corrective Retry

### Runtime / contract drift

Retry once only for a transient runtime error or clear prompt/argument drift. Prefer a fresh run unless resume-eligible and the same conversation clearly helps:

```text
Continue the same goal and authority boundary.
The prior run was incomplete because: [specific evidence].
Complete the task and return only the required handoff.
```

Write this into `<RUN_DIR>/corrective-handoff.md`. Do not mutate the original `handoff.md`.

### Supervisor review failure (large fix)

After independent review, if fixes are large (≥2 files, or one cross-cutting/broad file, or checks still fail after one supervisor self-fix), send one corrective AGY run:

```text
Continue the same goal and authority boundary.
Supervisor review found these defects after your prior handoff:
- [concrete defect with file/path evidence]
- [failed check and observed vs expected]
Do not reopen unrelated scope.
Make the smallest correct fix and return only the required handoff.
```

Resume with `--conversation <id>` only when resume-eligible (rule b in `references/agy-cli.md`). Otherwise start fresh with the full corrective handoff.

Do not retry login, consent, secret, or genuinely new-authority blockers. Do not auto-loop beyond one corrective AGY run.

For corrective work, preserve honest acceptance claims. If the original requirement is still uncovered after a fix, return `partial` or `blocked`, never `done`.
