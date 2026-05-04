---
name: execute
description: Run the execute phase against the active feature. Use when the user is ready to implement after a spec is done. For tier=focused, drives directly from SPEC.md acceptance criteria via the idd-execute skill. For tier=standard/full, requires PLAN.md (M2+).
---

# /idd:execute

Run the IDD execute phase against the active feature.

## Behavior

1. Determine active feature: `--feature <id>` flag, otherwise the most recently modified `.idd/features/*/state.json`.
2. Read `state.json`.
   - If `tier == "focused"` and `phases.spec.status == "done"` → invoke `idd-execute` skill (M1 path).
   - If `tier in ("standard", "full")` → require `PLAN.md` exists; invoke the M2+ execute path.
   - Otherwise → abort with reason.
3. The `idd-context-budget` and `idd-subagent-dispatch` skills apply automatically; the `PreToolUse` hook (`hooks/check_budget.py`) blocks malformed dispatches mechanically.
4. On completion, print summary: feature id, slice-N summaries written, commits made (from `state.commits`), any deviations.

## Failure modes

- `state.json` missing or fails schema → abort, surface validator output.
- Subagent dispatch blocked by `PreToolUse` hook → surface the hook's reason; the model edits the dispatch to comply, then retries.
- Subagent returns `status: blocked` → halt, log to `decisions.md` § Open, surface blocker to user.
- Working tree has uncommitted changes when execute starts → warn, ask user to commit or stash before continuing.
