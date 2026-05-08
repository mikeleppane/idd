---
name: next
description: Show or dispatch the next FORGE phase command for the active feature. Use when the user asks "what's next?" mid-feature or wants to advance without typing the command from memory.
---

# /forge:next

Print the next slash command for the active feature based on `state.json`. Optional `--run` dispatches it.

## Behavior

1. Parse args: optional `--feature <id>`, optional `--run`.
2. Invoke the `forge-next` skill (see `skills/forge-next/SKILL.md`).
3. Skill resolves the active feature, reads state, computes the next command, prints it.
4. If `--run` was passed and a next command exists, the skill dispatches it via the slash-command runner. The dispatched command always carries `--feature <resolved-id>` so the downstream command runs against the same feature `/forge:next` printed — guarantees correctness in multi-feature repos.

## Failure modes

- No active feature → `StateError` from `tools.state.find_active_feature`.
- Multiple active features without `--feature` → `StateError` listing the candidates.
- Explicit `--feature <id>` not found → `StateError`.
- state.json malformed → `StateError` from `tools.state.read_state`.

All errors surface to the user verbatim; no partial state writes occur because the skill is read-only.
