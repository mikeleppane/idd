---
name: execute
description: Run the execute phase against the active feature. Use when the user is ready to implement after spec (focused) or after PLAN.md review is resolved (standard/full). For tier=focused, drives directly from SPEC.md acceptance criteria. For tier=standard or full, drives from PLAN.md slice-by-slice with wave parallelism and per-subagent context budgets. The PreToolUse hook enforces the budget contract mechanically.
---

# /idd:execute

Run the IDD execute phase against the active feature.

## Behavior

1. Determine active feature: `--feature <id>` flag, otherwise the most recently modified `.idd/features/*/state.json`.
2. Read `state.json`.
   - If `tier == "focused"` and `phases.spec.status == "done"` → invoke `idd-execute` skill (focused branch — M1 behavior).
   - If `tier in ("standard", "full")`:
     - Require `phases.review.status == "done"` AND `.idd/features/<id>/REVIEW.plan.md` exists with frontmatter `target: plan`, `status: resolved`.
     - Require `PLAN.md` exists with frontmatter `status: ready`.
     - Invoke `idd-execute` skill (standard branch).
   - Otherwise → abort with reason.
3. The `idd-context-budget` and `idd-subagent-dispatch` skills apply automatically; the `PreToolUse` hook (`hooks/check_budget.py`) blocks malformed dispatches mechanically.
4. On completion, print summary: feature id, slices completed, commits made (from `state.commits`), deviations.

## Failure modes

- `state.json` missing or fails schema → abort, surface validator output.
- Standard tier with no `REVIEW.plan.md` or `REVIEW.plan.md` `status: open` → abort: "Run /idd:review --target plan and resolve HIGH+ findings before /idd:execute."
- Subagent dispatch blocked by `PreToolUse` hook → surface the hook's reason; the model edits the dispatch to comply, then retries.
- Subagent returns `status: blocked` → halt, log to `decisions.md` § Open, surface blocker.
- Working tree has uncommitted changes when execute starts → warn, ask user to commit or stash.
