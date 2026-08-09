# AGY Work Orders

Use the smallest prompt that preserves independent reasoning and explicit authority.

Write that prompt into one `<RUN_DIR>/handoff.md` file. When a domain skill is required, compile the applicable contract into the invocation payload and mirror its machine-checkable expectations in `<RUN_DIR>/context-manifest.json`.

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

DOMAIN CONTRACT
- Native skill: [slug + native-slash/contract-pack + SHA-256, or NONE]
- Required references: [path + SHA-256]
- Critical invariants: [task-applicable rules]
- Role semantics: [project-defined role names and their exact acceptance meaning]
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
- [Every required semantic row has honest coverage, when applicable]
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
When DOMAIN CONTRACT is not NONE, also return context_receipt.
When the context manifest requires traceability, also return requirement_matrix.
```

## Skill Bridge Contract

Do not put `Read <skill> SKILL.md` in a handoff and treat it as activation.

- Native path: preflight the exact slug, then make the invocation payload begin with `/<slug>` followed by the work order.
- Fallback path: the supervisor reads the source skill/references and compiles only the applicable invariants into `DOMAIN CONTRACT`.
- Mandatory-native path: if the exact skill is unavailable and a contract pack is insufficient, return blocked before implementation.

Create `context-manifest.json` alongside the handoff:

```json
{
  "require_context_receipt": true,
  "require_requirement_matrix": true,
  "corrective_run": false,
  "native_skill": {
    "slug": "project-domain-skill",
    "activation": "native-slash",
    "version_hash": "<64-char sha256>"
  },
  "references": [
    {"path": "references/domain-rules.md", "sha256": "<64-char sha256>"}
  ],
  "critical_rules": [
    "Project rule IDs and exact invariant text go here"
  ],
  "requirements": [
    {
      "requirement_id": "output-1",
      "role": "PROJECT_DEFINED_ROLE",
      "required_checks": ["project-check-id"]
    }
  ]
}
```

The terminal receipt must echo the exact expected values. The supervisor still verifies the real slash invocation/compiled payload and relevant timeline reads; receipt alone is a claim.

## Requirement Traceability Matrix

Require one row per semantically meaningful output. Each row records:

- `requirement_id`, `role`, and `planned_requirement`;
- `resolved_output`;
- project-defined checks as `{name, status, evidence}` entries;
- whether a fallback was used and previously approved;
- final `coverage`: `pass`, `fail`, or `unresolved`.

`context-manifest.json` declares the expected requirement IDs, project-defined roles, and required check names. `status: done` requires every declared row/check, coverage pass, no failed/unresolved check, and a non-empty resolved output. The project contract—not `use-agy`—defines what each role/check means and what evidence satisfies it.

## Construction Rules

1. State outcomes, facts, and real constraints; do not transmit the supervisor's whole reasoning history.
2. Separate verified facts from hypotheses and open decisions.
3. Do not ask AGY to confirm a preferred answer unless comparison of that answer is the actual task.
4. Grant workspace discovery broadly and mutations narrowly.
5. Keep one mission and one foreground primary AGY per invocation. Allow at most one read-only subagent by default and require the primary to consolidate its work.
6. Require evidence in the handoff, then verify it independently.
7. Pass the mission through `handoff.md` (or `corrective-handoff.md`); if a Skill Bridge applies, include the compiled contract in the invocation and pass its separate manifest only to the summarizer for verification.
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

For work with project-defined acceptance semantics, set `corrective_run: true` in the context manifest and retain the original requirement rows. Removing an invalid output does not restore coverage by itself. If the original requirement is still uncovered, return `partial` or `blocked`, never `done`.
