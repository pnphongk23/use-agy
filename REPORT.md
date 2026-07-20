# Incident Report: `use-agy` Herdr Launch Failure

Date: 2026-07-20  
Status: Root cause confirmed on 2026-07-20; fixed and verified on 2026-07-21  
Affected components: `scripts/orchestrate_herdr.py`, Herdr 0.7.3 integration, AGY 1.1.4 fallback flow

## Executive Summary

The two AGY sessions launched through Herdr did not crash because of AGY, the selected Gemini model, or `--sandbox`. Both sessions were started successfully, then terminated by the orchestration helper itself.

The primary defect is an output-contract mismatch:

1. `herdr pane read` returns terminal content as raw stdout text. An empty terminal is represented by empty stdout with exit code `0`.
2. `read_terminal_capture()` assumes that the same stdout is a JSON object and passes it to `parse_json_output()`.
3. `parse_json_output()` raises `OrchestrationError("command returned no JSON object")` for both empty text and ordinary non-JSON terminal text.
4. The broad exception handler in `launch()` interprets this diagnostic-read failure as a failed launch.
5. The handler closes the run-owned workspace as cleanup.
6. Closing that workspace sends SIGTERM to the newly started AGY process.

Therefore, the observed AGY `signal 15` exit is a consequence of helper cleanup, not the initiating failure.

The retry with `--no-sandbox` reproduced the same failure because sandboxing was unrelated. The later foreground failures were separate permission, configuration, and AGY tool-selection problems introduced after the supervisor incorrectly abandoned the valid Herdr launch path.

## Incident Inputs

The original transcript is stored at:

```text
/Users/phamnhuphong/.codex/attachments/ebeb80f8-dd1d-4707-967a-5bb28a4d4186/pasted-text.txt
```

The complete Codex rollout containing command outputs omitted from the pasted transcript is stored at:

```text
/Users/phamnhuphong/.codex/sessions/2026/07/19/rollout-2026-07-19T23-16-50-019f7b2a-7eba-7be2-a001-627577cb32a2.jsonl
```

The two failed Herdr run directories are:

```text
/private/tmp/agy-wi2-root-EdJuhH/run
/private/tmp/agy-wi2-retry-root-Fvy6X0/run
```

Herdr server metadata is recorded at:

```text
/Users/phamnhuphong/.config/herdr/herdr-server.log
```

Sensitive account data, authentication material, complete prompts, and handoff tokens are intentionally omitted from this report.

## Resolution Addendum

Status as of 2026-07-21: the runtime bug described in this report has been fixed and verified against local AGY `1.1.4` and Herdr `0.7.3`.

Implemented fixes:

1. `read_terminal_capture()` treats successful `herdr pane read --format text` output as raw terminal text instead of JSON.
2. Initial launch terminal-capture failure is recorded as diagnostic evidence and no longer destroys an otherwise healthy AGY pane.
3. `observe` treats terminal reads as bounded diagnostics and uses the raw AGY conversation SQLite response as the completion channel.
4. `raw_handoff_block()` accepts the observed AGY 1.1.4 protobuf trailing tag after a valid closing marker while still requiring the exact generated token and structured `STATUS`.
5. Skill guidance now retires the dead contracts that caused repeated failures:
   - no terminal scrollback or TUI wrapping recovery as completion evidence;
   - no handoff line-count limit;
   - no corrective retry merely to repair markers after a completed stream;
   - no treating `idle` or `done` as proof of completion;
   - no treating the diagnostic terminal sample size as the handoff size.

Verification performed after the fix:

- `scripts/test_orchestrate_herdr.py`: 45 tests passed.
- `scripts/quick_validate.py`: skill frontmatter validation passed.
- `git diff --check` passed for the modified helper and test files.
- A live Herdr lifecycle probe launched AGY, captured the raw SQLite handoff, snapshotted, recorded, and cleaned up the owned pane without modifying the empty test workspace.

## Primary Root Cause

### Actual Herdr CLI contract

The installed Herdr version is 0.7.3. Its command reference exposes:

```bash
herdr pane read <pane_id> \
  --source visible|recent|recent-unwrapped \
  --lines N \
  --format text|ansi
```

There is no JSON-output option for `pane read`. This is consistent with the official [Herdr repository and CLI examples](https://github.com/ogulcancelik/herdr), where `pane read` is the terminal-output command.

A read-only probe against the installed binary confirmed both relevant cases:

| Probe | Exit code | Stdout | JSON object |
|---|---:|---:|---|
| `pane read --lines 1` against an empty tail | `0` | `0` bytes | No |
| `pane read --lines 120` against populated history | `0` | `142` bytes raw text | No |

The important property is not whether the terminal is empty. Both empty and populated successful reads return text rather than the JSON structure expected by the helper.

### Incorrect helper assumption

[`read_terminal_capture()`](scripts/orchestrate_herdr.py) currently does the following:

```python
result = run_herdr(
    manifest,
    ["pane", "read", manifest["pane_id"], ...],
    check=False,
)
if result.returncode != 0:
    return result.stdout + result.stderr, False
payload = parse_json_output(result.stdout)
read = payload.get("result", {}).get("read", {})
```

This code treats exit code `0` as proof that stdout contains a JSON response. That assumption is valid for commands such as `workspace create`, `agent start`, `pane list`, and `pane layout`, but not for the specialized terminal-output command `pane read`.

[`parse_json_output()`](scripts/orchestrate_herdr.py) skips every non-JSON line and finally raises:

```text
command returned no JSON object
```

The complete rollout shows this exact error for both Herdr runs.

### Why AGY received SIGTERM

The launch sequence successfully completed all topology operations before the exception:

- the run-owned workspace was created;
- the AGY pane was started;
- returned workspace, tab, pane, and terminal IDs were recorded;
- the bootstrap pane was verified and closed;
- only the owned AGY pane remained;
- the final single-pane layout was verified;
- `layout-after-launch.json` was written;
- `launch-1.json` was written.

The next operation was the initial terminal capture:

```python
terminal = read_terminal(manifest, lines=80)
```

That call raised the JSON parsing error. The broad launch handler then ran:

```python
except BaseException:
    if manifest.get("workspace_owned"):
        close_owned_workspace(manifest, require_single_pane=False)
    manifest["phase"] = "launch_failed"
    save_manifest(run_dir, manifest)
    raise
```

Closing the workspace terminated its only remaining pane and therefore its AGY child process.

## Confirmed Timeline

### First Herdr run

Run directory:

```text
/private/tmp/agy-wi2-root-EdJuhH/run
```

Relevant server events:

| UTC time | Event |
|---|---|
| `16:33:07.954` | `agent.start` request received |
| `16:33:07.959` | AGY pane child spawned successfully, PID 31583 |
| `16:33:07.973` | Bootstrap-pane close requested |
| `16:33:07.995` | Bootstrap-pane close completed |
| `16:33:08.024` | Helper requested `workspace.close` |
| `16:33:08.287` | AGY child exited with `Terminated: 15` |
| `16:33:08.305` | Workspace close completed |

The AGY job log contains only language-server startup followed by:

```text
RAW: Raising signal 15 with default behavior
```

No AGY conversation was created. The helper terminated the process before AGY could begin the work order.

### Second Herdr run

Run directory:

```text
/private/tmp/agy-wi2-retry-root-Fvy6X0/run
```

Relevant server events:

| UTC time | Event |
|---|---|
| `16:34:02.223` | `agent.start` request received |
| `16:34:02.225` | AGY pane child spawned successfully, PID 33182 |
| `16:34:02.234` | Bootstrap-pane close requested |
| `16:34:02.260` | Bootstrap-pane close completed |
| `16:34:02.282` | Helper requested `workspace.close` |
| `16:34:02.558` | AGY child exited with `Terminated: 15` |
| `16:34:02.576` | Workspace close completed |

The second run used `--no-sandbox` but failed at exactly the same post-launch stage with the same error:

```text
{"status": "error", "error": "command returned no JSON object"}
```

This falsifies the transcript's explanation that the initial Herdr failure was caused by `--sandbox`.

## Why Model, Authentication, and Sandbox Were Not the Root Cause

### Model health passed

Both `prepare` runs completed the explicit Gemini 3.5 Flash Medium smoke test with exact stdout:

```text
AGY_OK
```

The selected model was therefore reachable and able to complete a no-tool request.

### Authentication was usable

The smoke test authenticated successfully after transient startup warnings. Those early warnings were not the final state and did not prevent the exact smoke response.

### Sandbox was not causal

The first Herdr launch included `--sandbox`; the second deliberately omitted it. Both runs:

- reached successful `agent.start`;
- wrote valid launch and layout evidence;
- failed on the same `command returned no JSON object` error;
- were terminated by `workspace.close` within a fraction of a second.

The only common failure point after the successful topology evidence is the helper's initial terminal read.

## Why Existing Tests Passed

The test suite did not exercise the real `pane read` output contract.

The launch test mocks `read_terminal()` directly:

```python
mock.patch.object(orchestration, "read_terminal", return_value="ready")
```

As a result, it verifies launch topology but bypasses `read_terminal_capture()` and its incorrect JSON parsing.

Other terminal tests mock `read_terminal_capture()` itself and supply synthetic `(text, truncated)` tuples. They therefore test expansion and accumulation logic but not Herdr CLI compatibility.

The previous real integration probe verified:

- isolated Herdr server startup;
- runtime protocol and socket pinning;
- workspace creation;
- explicit workspace/tab/pane ownership;
- bootstrap-pane cleanup;
- final full-pane layout;
- workspace cleanup.

It did not execute a real `herdr pane read`. Consequently, the integration could pass while the first real skill run failed immediately afterward.

This is a contract-test gap, not a nondeterministic race.

## Secondary Issues Found in the Same Transcript

These issues contributed to the failed overall workflow but did not cause the two Herdr launches to terminate.

### 1. `--run-dir` existence contract is easy to misuse

The first prepare attempt used:

```bash
RUN_DIR=$(mktemp -d /tmp/agy-wi2-XXXXXX)
orchestrate_herdr.py prepare --run-dir "$RUN_DIR" ...
```

`mktemp -d` creates the directory. `prepare()` then calls:

```python
run_dir.mkdir(parents=True, exist_ok=False)
```

The result was:

```text
[Errno 17] File exists
```

The agent recovered by creating a parent temporary directory and passing a nonexistent `<parent>/run` child. The helper behavior is internally consistent, but the documented placeholder `<UNIQUE_RUN_DIR>` does not clearly state that the path must not exist.

### 2. The supervisor misclassified helper cleanup as an AGY failure

After the first run, the supervising agent stated that AGY had exited because the helper enabled sandbox. The evidence available at that point already contradicted that conclusion:

- `launch-1.json` proved `agent.start` succeeded;
- the manifest recorded all owned IDs and `phase: launch_failed`;
- the job log showed SIGTERM rather than a sandbox error;
- no conversation was created;
- the helper returned a JSON-parsing error.

The second retry changed sandbox behavior without changing the failing adapter code, so it could not test the real hypothesis.

### 3. Failure classification was not performed with valid interactive-run evidence

The transcript attempted to classify the failed Herdr run using unavailable job stdout/stderr files, then used smoke stdout and a fabricated exit code for an interactive process. That evidence does not represent the failed job.

The skill explicitly requires persistent Herdr runs to be recorded only with job-specific evidence and a verified interactive status after a handoff. A helper-caused launch failure should instead be classified as an orchestration/adapter failure using the helper error, manifest phase, launch artifacts, server log, and child termination evidence.

### 4. The foreground fallback was inappropriate for a permission-sensitive editing job

Once Herdr was incorrectly abandoned, the supervisor moved the implementation into headless `agy -p`. AGY 1.1.3 and later deliberately soft-deny tools that require confirmation in headless mode.

The first foreground attempt reached a Bash permission request and logged:

```text
Print mode: soft-denying tool confirmation "Bash"
```

The skill's own permission policy says this should stop unattended execution and return to an attended TUI or request a narrower verified allow rule. Instead, the supervisor retried headless with a narrower prompt.

### 5. The second foreground attempt exposed separate AGY/configuration failures

The second foreground run progressed further but encountered three independent errors:

1. A terminal command failed sandbox setup because a deny rule used the relative path `.git/`:

   ```text
   sandbox configuration error: deny .git/: non-absolute file path
   ```

2. AGY attempted to use an artifact-writing tool with a project source path:

   ```text
   lib/supabase.ts is not a valid artifact path
   ```

   Artifact files were required to be inside the conversation's AGY brain directory, while project source edits needed the code-edit tool.

3. The subsequent source edit required confirmation and was soft-denied because print mode could not present an attended permission decision:

   ```text
   Print mode: soft-denying tool confirmation "Edit"
   ```

These are real foreground-run failures, but they occurred only after the supervisor incorrectly routed away from Herdr.

### 6. Version guidance is stale

The installed CLI currently reports:

```text
agy 1.1.4
```

The skill references still describe the installed version as 1.1.3 in several evidence notes. The primary Herdr parsing bug is independent of this drift, but permission, sandbox, artifact, and headless behavior should not be inferred solely from the older probe record.

## Impact

The defect affects every new Herdr launch that reaches the initial terminal capture:

- a successful AGY child is mislabeled as `launch_failed`;
- the run-owned workspace is immediately closed;
- AGY is terminated before creating a conversation;
- no raw handoff can ever be produced;
- switching `--sandbox` cannot help;
- repeated retries consume model smoke calls and supervisor time;
- incorrect fallback to headless mode introduces permission failures and can obscure the original adapter defect.

Because populated terminal output is also non-JSON, waiting briefly before the read would not solve the contract mismatch. A delay might avoid an empty string but would still pass ordinary terminal text to a JSON parser.

## Recommended Fix Direction

At the time this report was first written on 2026-07-20, no fixes had been applied. The following recommendations describe the required repair direction; the current implementation status is captured in the Resolution Addendum above.

### A. Correct the `pane read` adapter

Choose one explicit contract:

1. Treat `herdr pane read` stdout as raw terminal text and return it directly; or
2. Use Herdr's socket API if structured text plus authoritative truncation metadata is required.

Do not call `parse_json_output()` on the normal CLI output of `pane read`.

If the raw CLI path is retained, truncation handling must be redesigned because the current helper expects a JSON `truncated` boolean that the CLI text command does not expose.

### B. Distinguish launch/control failures from child failures

Before cleanup, record:

- failing lifecycle operation;
- exact sanitized Herdr argv;
- return code;
- whether stdout was expected to be raw or JSON;
- stderr;
- manifest phase and owned IDs.

An evidence-capture failure after a verified `agent.start` should not be reported as an AGY process crash.

### C. Avoid killing a healthy child for a non-critical initial snapshot failure

The initial terminal snapshot is diagnostic evidence, not a prerequisite for establishing that `agent.start` succeeded. A terminal-read adapter error should preserve the owned workspace and surface a recoverable orchestration state, unless ownership or safety checks themselves fail.

### D. Add real CLI contract tests

At minimum, add tests for:

- successful empty `pane read`: exit `0`, empty raw stdout;
- successful populated `pane read`: exit `0`, non-JSON raw stdout;
- nonzero `pane read` error;
- launch remaining alive after an initial diagnostic-read failure;
- isolated real Herdr launch followed by a real `pane read` before cleanup.

The integration acceptance test must cover the complete production path through the first terminal capture, not stop after layout verification.

### E. Clarify `--run-dir`

Either:

- document that `--run-dir` must not exist and show the parent-plus-child pattern; or
- allow an existing empty directory after validating that it contains no prior run state.

### F. Tighten recovery policy

- Do not change sandbox mode until evidence points to a sandbox failure.
- Do not fabricate an exit code for a Herdr-managed process.
- Do not use smoke stdout as the failed job's stdout.
- Treat adapter failures separately from AGY/model failures.
- After a headless permission soft-deny, stop or return to an attended TUI rather than retrying another headless editing prompt.

### G. Revalidate AGY 1.1.4 behavior

Refresh version-scoped evidence for:

- `accept-edits` file confirmation behavior;
- headless soft-deny behavior;
- terminal sandbox path requirements;
- project-source edit versus artifact tool routing;
- Git metadata visibility under `--sandbox`.

## Required Regression Acceptance

A corrected implementation should not be accepted until all of the following are demonstrated:

1. A real isolated Herdr session launches AGY and survives the initial terminal read.
2. Empty terminal output is accepted as valid diagnostic text rather than a JSON error.
3. Populated terminal output is preserved exactly as text.
4. The run reaches `phase: launched` and remains alive long enough to create an AGY conversation.
5. `observe` can pin that conversation ID from the owned job log.
6. Raw conversation handoff extraction remains independent of terminal wrapping and scrollback.
7. A diagnostic terminal-read failure does not terminate a verified healthy AGY pane.
8. Sandbox and no-sandbox launch variants are tested separately without conflating adapter errors with child errors.
9. The full unit suite, skill validation, and `git diff --check` pass.
10. The integration test includes launch, first read, observe readiness, and exact owned-resource cleanup.

## Final Diagnosis

The incident's initiating defect is:

```text
Herdr raw terminal stdout
    -> incorrectly parsed as JSON
    -> OrchestrationError
    -> broad launch cleanup
    -> workspace close
    -> SIGTERM delivered to AGY
```

The sandbox explanation in the original transcript is disproven by the identical no-sandbox failure. Model selection and authentication passed their health gate. Later headless permission, sandbox-rule, and artifact-path failures are separate downstream effects of an incorrect recovery route.
