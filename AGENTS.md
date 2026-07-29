# use-agy Router

Start with [SKILL.md](SKILL.md). Read only the matching reference:

- Prompt and handoff: [references/work-orders.md](references/work-orders.md)
- Guardrails: [references/security-and-permissions.md](references/security-and-permissions.md)
- CLI flags and failure routing: [references/agy-cli.md](references/agy-cli.md)

The skill runs one fresh foreground primary AGY process and allows one useful read-only AGY subagent by default. Consume `stream-json` live, validate [handoff.schema.json](handoff.schema.json), then verify changed artifacts and checks. Read full logs only when failure evidence requires it.
