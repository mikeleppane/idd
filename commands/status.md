---
name: status
description: Print a one-line IDD status for the active feature. Use when the user wants a quick "where am I?" without opening state.json or running git log.
---

# /idd:status

Print a one-line status summary for the active feature.

## Behavior

1. Parse args: optional `--feature <id>`, optional `--verbose`.
2. Invoke the `idd-status` skill (see `skills/idd-status/SKILL.md`).
3. Skill resolves the active feature, reads state, prints the summary.
4. With `--verbose`, also prints the phase history table.

## Failure modes

- No active feature → `StateError`.
- Multiple active features without `--feature` → `StateError` listing candidates.
- Explicit `--feature <id>` not found → `StateError`.
- state.json malformed → `StateError`.
