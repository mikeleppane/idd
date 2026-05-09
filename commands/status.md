---
name: status
description: Print a one-line FORGE status for the active feature. Use when the user wants a quick "where am I?" without opening state.json or running git log.
---

# /forge:status

Print a one-line status summary for the active feature.

## Behavior

1. Parse args: optional `--feature <id>`, optional `--verbose`, optional `--report`.
2. Invoke the `forge-status` skill (see `skills/forge-status/SKILL.md`).
3. Skill resolves the active feature, reads state, prints the summary.
4. With `--verbose`, also prints the phase history table.
5. With `--report`, the one-line summary is replaced by a structured
   markdown report; `--report` and `--verbose` are mutually exclusive.

## Args

- `--feature <id>`
   Override active-feature resolution; must match a folder under
   `.forge/features/`.
- `--verbose`
   Append the canonical phase history table after the summary line.
- `--report`
   Print a structured markdown report covering current phase, the last 5
   commits (in `state.commits[]` insertion order, most-recent first), open
   BLOCK + HIGH findings scoped to the active feature (with recovery
   hints when available), and the recommended next command. Mutually
   exclusive with `--verbose`.

When `current_phase == "refine"`, the summary line appends `(round X/5)`
where `X` is `state.json.routing.refine_attempts` (defaulting to `0` when
the field is not yet set). The cap matches the Socratic-loop ceiling
enforced by `tools.state.increment_refine_attempts` (`_REFINE_ATTEMPTS_CAP`).

**Local-only logs.** `tools.feature_log` writes per-feature lifecycle
events to `.forge/logs/<feature_id>.jsonl` *when callers append to it*.
Today the writer ships without a default producer; the path is reserved
and gitignored, so any caller that opts in stays local — no network sink.
Inspect with `cat .forge/logs/<feature_id>.jsonl | jq` or any JSONL tool
when the file exists.

## Failure modes

- No active feature → `StateError`.
- Multiple active features without `--feature` → `StateError` listing candidates.
- Explicit `--feature <id>` not found → `StateError`.
- state.json malformed → `StateError`.
