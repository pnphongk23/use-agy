# AGY Reliability And Continuous Learning

## Purpose

Diagnose automated AGY failures from evidence, choose a bounded recovery, and improve this skill without overfitting to one incident.

## Session Health Gate

1. Run `agy --help`, `agy models`, and `agy agents` when capabilities matter.
2. Do not use the implicit default model for automation. Only use explicitly named Gemini 3.5 models.
3. Smoke-test `Gemini 3.5 Flash (Medium)` first, then Low and High only as bounded fallbacks. Never select GPT, Claude, Gemini 3.1, or another family.
4. Require exit `0` and exact stdout. A model name in logs or a successful OAuth event is not a passing result.
5. Reuse the passing model for the current session. Recheck after a quota, capacity, adapter, or repeated empty-output failure.

The July 11, 2026 local evidence showed the implicit/default model path emitting `PlannerResponse without ModifiedResponse` with no usable stdout. Explicitly selecting `Gemini 3.5 Flash (Medium)` then passed both the plain no-tool smoke test and the `--mode plan --sandbox` smoke test. The workaround is explicit Gemini 3.5 selection, not a cross-family fallback.

## Failure Classes

| Class | Evidence | Recovery |
|---|---|---|
| `onboarding` | first-run theme or onboarding screen is visible | stop unattended execution and require the user to complete it in an attended TUI |
| `adapter_empty` | empty/unusable stdout plus repeated `PlannerResponse without ModifiedResponse` | explicitly smoke-test the configured Gemini 3.5 tier, then another 3.5 tier if needed |
| `quota` | `Resource has been exhausted` or quota error | choose another healthy Gemini 3.5 tier or stop |
| `capacity` | 503, high traffic, or `No capacity available` | one delayed retry or another healthy Gemini 3.5 tier |
| `auth` | final failure says not logged in; do not misclassify startup noise if OAuth later succeeds | stop and ask user to authenticate in attended TUI |
| `permission` | pending/denied permission or confirmation with no unattended resolution | attended TUI or narrower authorized work |
| `sandbox` | `SANDBOX_COMMAND_BLOCKED`, operation-not-permitted, or containment violation | revise the command/scope; do not drop sandbox silently |
| `contract` | process succeeds but output omits or replaces required deliverable | one shorter corrective retry |
| `transient` | network reset, service unavailable, temporary backend error | one bounded retry |
| `unknown` | evidence does not match a known class | stop after two total attempts and diagnose directly |

Authentication warnings during startup are not decisive. On this installation, logs may initially say “not logged in” and then show successful silent OAuth. Classify the final state, not the first warning.

## Web And Permission Routing

Headless `-p` is suitable only when required tools can proceed without human approval. Before broad research, run a one-URL web probe with the selected healthy Gemini 3.5 tier. If it waits for approval, switch to an attended TUI; do not use `--dangerously-skip-permissions`. If it returns quota/capacity errors, change only the Gemini 3.5 tier or retry once according to the table.

## Observation Ledger

The classifier appends redacted JSON Lines to `evals/runtime-observations.jsonl` when called with `--record`. Store only:

- UTC timestamp;
- model label;
- job type;
- exit code;
- elapsed seconds if known;
- classification;
- whether stdout was non-empty;
- short evidence markers, never raw prompts, URLs with secrets, tokens, emails, or full logs.
- a content-derived run fingerprint used only to deduplicate repeated classification of the same run.

For foreground automation, require the real process exit code. For a bounded work order completed inside a persistent Herdr TUI, capture only that work order's redacted terminal segment and use `--verified-interactive` after the handoff and independent verification both pass. This records `exit_code: null`. Never classify a whole reused-session log as one job, and never fabricate exit `0` merely because Herdr reports `idle`.

Re-classifying the same log must not create another incident. Promotion counts unique fingerprints, distinct AGY conversations/runs, and at least two task contexts; repeated retries or repeated classifier calls against one log count once.

## Promotion Rule

Change permanent skill guidance only when one of these is true:

1. an installed-CLI smoke test directly proves the behavior and an alternate path passes;
2. the same classified failure occurs in at least three independent runs across two tasks;
3. upstream release notes or official docs explicitly establish the behavior.

Before promotion, reproduce with the smallest safe test. After promotion, update or add an eval, validate the skill, and run at least one positive and one negative smoke case. Keep workarounds version/date scoped. Retire them after the original failing path passes twice on separate sessions.

## Sources Used For This Policy

- `jacob-bd/antigravity-cli-skill`: usage patterns emphasize explicit models, print timeouts, and log files; its unsafe auto-approval examples are not adopted.
- `jacob-bd/antigravity-cli-skill`: cheat sheet confirms no stream-JSON output and documents project-specific permissions and model selection.
- `SafeMantella/claude-code-agy-CLI-skill`: reinforces treating AGY as a delegated worker with explicit prompts and verification.
- Installed AGY help, models, changelog, and local redacted logs remain authoritative for local behavior.
