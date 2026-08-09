# use-agy Router

Start with [SKILL.md](SKILL.md). Read only the matching reference:

- Prompt and handoff: [references/work-orders.md](references/work-orders.md)
- Guardrails: [references/security-and-permissions.md](references/security-and-permissions.md)
- CLI flags, summarize script, and failure routing: [references/agy-cli.md](references/agy-cli.md)

The skill runs one fresh foreground primary AGY process via the `run-agy.py` watchdog runner and allows one useful read-only AGY subagent by default. Supervisor writes one `handoff.md`, uses `--output-format stream-json` redirected to files via the watchdog, monitors asynchronously, runs [scripts/summarize-agy-stream.py](scripts/summarize-agy-stream.py) to build an ordered timeline and `report.md`, validates [handoff.schema.json](handoff.schema.json), verifies artifacts/checks, and may resume once for a large corrective fix. Read full logs only when failure evidence requires it.
