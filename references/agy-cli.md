# AGY CLI Runtime Reference

Use this reference to select supported CLI behavior. Prefer the installed binary over examples from web docs when they conflict.

## Sources Checked

- Local AGY 1.1.2: `agy --version`, `agy --help`, `agy agents`, `agy models`, and `agy changelog` on 2026-07-14.
- Built-in guide: `~/.gemini/antigravity-cli/builtin/skills/antigravity_guide/`.
- Official docs: `https://antigravity.google/docs/cli-overview`, `/cli-using`, `/cli-best-practices`, `/cli-prompting`, `/cli-reference`, `/cli-conversations`, `/cli-subagents`, `/cli-permissions`, and `/cli-sandbox`.

Older URLs shaped like `/docs/cli/overview` may redirect or expose only the JavaScript shell. Use the routes above for live retrieval.

## Installed CLI: verify at runtime

The installed binary reports these top-level options. The local changelog may include versions newer than old cached skill notes, so do not infer the installed version from this heading; inspect current help/changelog and behavior.

```text
--add-dir                       Add a workspace directory; repeatable
--agent                         Select agent for this CLI session
-c, --continue                  Continue the most recent conversation
--conversation                  Resume a conversation by ID
--dangerously-skip-permissions  Auto-approve tool permission requests
-i, --prompt-interactive        Submit an initial prompt and continue in TUI
--log-file                      Override CLI log path
--mode                          accept-edits or plan
--model                         Select model for this session
--new-project                   Create a project for this session
-p, --print                     One-shot non-interactive prompt
--print-timeout                 Print-mode wait limit; default 5m0s
--project                       Select project ID
--prompt                        Alias for --print
--sandbox                       Enable terminal restrictions
```

Subcommands: `agent`, `agents`, `changelog`, `help`, `install`, `models`, `plugin`, `plugins`, `update`.

The local CLI currently lists one named agent, `mcp-manager`. Never assume a role name exists; run `agy agents` before `--agent <name>`. Run `agy models` before selecting a model.

The current local help does not expose `--cwd`, although an official best-practices example uses it. Run AGY with the process working directory set to the workspace instead of passing undocumented flags.

## Invocation Selection

Within this skill, the default is `--prompt-interactive` in a Herdr-managed TUI. Use `--print` or `-p` only when direct process exit status or structured stdout is essential, the user explicitly requests it, Herdr is unavailable/incompatible, or the job is exceptionally small and latency-sensitive. Put the prompt immediately after the flag, then append other options:

```bash
agy --model 'Gemini 3.5 Flash (Medium)' -p '<WORK_ORDER>' \
  --mode plan --sandbox --print-timeout 10m --log-file '<UNIQUE_LOG>'
```

## What `--mode` Means

The installed CLI accepts `--mode plan` and `--mode accept-edits`.

- `plan` selects the agent's planning/review-oriented execution behavior. It is appropriate for research, exploration, diagnosis, and verification where the worker should inspect and reason without implementing changes. It may still call read, search, web, and other discovery tools.
- `accept-edits` selects edit-oriented execution for a user-authorized implementation. It does not authorize unlimited commands, network access, commits, pushes, or external side effects.
- omitting `--mode` leaves execution behavior to the current CLI/project configuration, such as `request-review` or other configured policies.

`--mode plan` is not a filesystem sandbox, not a network allowlist, and not a formal proof that no write occurred. `--sandbox` separately restricts terminal execution. Permission configuration separately governs file, URL, command, and MCP actions. The work order's `Write: NONE` and post-run inspection remain required.

Community examples often omit `--mode` because they rely on configured defaults or use `--dangerously-skip-permissions` for autonomous execution. This skill intentionally does not copy that behavior. Keep `plan` for bounded non-mutating jobs unless a verified installed-CLI bug requires omission.

Set `--print-timeout` above five minutes for builds or broad research.

Use `--prompt-interactive` only for a human-attended TUI session. First launch may show theme, sign-in, terms, telemetry, and privacy choices. Never complete those choices for the user.

For delegated work, launch that TUI through Herdr by default as described in [herdr-runtime.md](herdr-runtime.md). Herdr preserves and exposes the real terminal but does not change AGY permissions, sandboxing, model selection, or the requirement for independent verification.

Use `--continue` only for the latest conversation in the current working directory. Conversations are directory-scoped. Use `--conversation <id>` when an exact session is required. Fresh one-shot calls reduce context contamination.

Use `--project <id>` for an existing project and `--add-dir <path>` for approved multi-root context. Do not invent project IDs or add unrelated directories.

## TUI Capabilities

Official docs describe:

- `@` path completion; `!` direct terminal command; `?` help.
- `/agents` background subagent manager; `/tasks` background command manager.
- `/btw` side query; `/diff` modified-file diff; `/skills` loaded skills.
- `/config` or `/settings`; `/permissions`; `/planning`; `/model`.
- `/resume`, `/fork`, `/rewind`, `/clear`, `/exit`.
- `Esc` interrupts an active turn; `/help` or `/usage` shows current help.

These are TUI features, not guaranteed top-level command-line flags. Do not put slash commands into `--print` prompts as if they were flags.

## Subagents

Official docs say the primary agent may spawn background subagents for slow builds, research sweeps, and multi-file work. TUI users monitor them through `/agents`; background shell work appears in `/tasks`. Subagents inherit parent safety scopes and bubble permission requests to the UI.

In unattended `--print` mode, do not rely on manual subagent approval flows. Ask for subagents only when parallelism is truly useful, give each a disjoint deliverable, and require the primary AGY agent to consolidate their evidence before returning.

## Known Reliability Rule

On this installation, placing `--print-timeout` between `--print` and the prompt caused AGY to answer about the timeout flag instead of the work order. Keeping the prompt immediately after `-p` produced a correctly structured repository exploration result. Treat any objective drift as a failed contract. Retry once with corrected argument order and a short prompt whose first lines are `JOB`, `OBJECTIVE`, and `RETURN`; otherwise fall back to direct tools and disclose the failure.

Do not rely on the implicit model. On 2026-07-11, the implicit/default path produced empty print output with repeated `PlannerResponse without ModifiedResponse`. Explicit `Gemini 3.5 Flash (Medium)` then passed both plain and `--mode plan --sandbox` no-tool smoke tests. Always health-check and explicitly pass a Gemini 3.5 tier. This skill must never select GPT or Claude.
