---
name: forge-next
description: Print and optionally dispatch the next FORGE lifecycle phase command for the active feature. Use when the user asks "what's next?" or wants to advance to the next phase without remembering the command.
disable-model-invocation: true
---

# FORGE Next

## Goal

Tell the user which slash command to run next. Optionally dispatch it.

## Inputs

- Active feature folder (resolved via `tools.state.find_active_feature(repo_root, feature_id)`).
- `--run` flag: dispatch the next command directly.
- `--feature <id>` flag: override active-feature resolution.

## Steps

1. Resolve `repo_root` (cwd of invocation).
2. Call `tools.state.find_active_feature(repo_root, feature_id=<flag value or None>)`. Surface any `StateError` to the user verbatim and exit. Capture the resolved feature folder name (`feature_id`) — needed for `--run` propagation in step 7.
3. Read the resolved `state.json` via `tools.state.read_state(state_path, schema_path)`.
4. Compute the next command via `tools.state.next_phase_command(state)`.
5. If `None`: print `Done.` and exit.
6. Else print: `Next: <command>`.
7. If `--run`: dispatch the command via the user's slash-command runner. The skill MUST NOT mutate state directly. **Always propagate `--feature <resolved-id>`** to the dispatched command (e.g. `/forge:execute --feature 2026-05-04-demo`) so the downstream resolver acts on the same feature `/forge:next` printed — required when more than one feature exists in `.forge/features/`. If the next command already contains `--feature`, leave it as-is; otherwise append the flag verbatim.

## Done

User sees the next command (or `Done.`); state.json is unchanged.
