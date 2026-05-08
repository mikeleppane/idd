---
name: execute
description: Run the execute phase against the active feature. Use when the user is ready to implement after spec (focused) or after PLAN.md review is resolved (standard/full). For tier=focused, drives directly from SPEC.md acceptance criteria. For tier=standard or full, drives from PLAN.md slice-by-slice with wave parallelism and per-subagent context budgets. The PreToolUse hook enforces the budget contract mechanically.
---

# /forge:execute

Run the FORGE execute phase against the active feature.

## Behavior

1. Determine active feature using the D-S6 rule (`tools.state.find_active_feature(repo_root, feature_id)`): `--feature <id>` wins; else single-active feature; else error listing the candidates.
2. Read `state.json`.
   - If `tier == "focused"` and `phases.spec.status == "done"` → invoke `forge-execute` skill (focused branch — M1 behavior).
   - If `tier in ("standard", "full")`:
     - Require `"plan"` in `phases.review.targets_done` AND `.forge/features/<id>/REVIEW.plan.md` exists with frontmatter `target: plan`, `status: resolved`. The review phase stays `status: in_progress` between the two target passes; `complete_phase('review')` only fires once `target: code` is also resolved.
     - Require `PLAN.md` exists with frontmatter `status: ready`.
     - Invoke `forge-execute` skill (standard branch).
   - Otherwise → abort with reason.
3. The `forge-context-budget` and `forge-subagent-dispatch` skills apply automatically; the `PreToolUse` hook (`hooks/check_budget.py`) blocks malformed dispatches mechanically. TDD enforced via [forge-tdd](../skills/forge-tdd/SKILL.md). Pair test commits before implementation; whitelist available for refactor/docs/chore work.
4. On completion, print summary: feature id, slices completed, commits made (from `state.commits`), deviations.

## Failure modes

- `state.json` missing or fails schema → abort, surface validator output.
- Standard tier with no `REVIEW.plan.md` or `REVIEW.plan.md` `status: open` → abort: "Run /forge:review --target plan and resolve HIGH+ findings before /forge:execute."
- Subagent dispatch blocked by `PreToolUse` hook → surface the hook's reason; the model edits the dispatch to comply, then retries.
- Subagent returns `status: blocked` → halt, log to `decisions.md` § Open, surface blocker.
- Working tree has uncommitted changes when execute starts → warn, ask user to commit or stash.

## Constitution preflight

When `.forge/CONSTITUTION.md` is present, the skill calls `tools.constitution.load_and_filter` before its primary work and passes filtered `articles[]` into every subagent dispatch budget. No-op when absent.
