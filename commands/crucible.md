---
name: crucible
description: Run the crucible phase against the active feature — three-step adversarial ritual (assumptions inversion → adversarial Q&A → pre-mortem) producing UNDERSTANDING.md. Use after /forge:plan to surface hidden assumptions and failure modes before review and execute.
---

# /forge:crucible

Run the FORGE crucible phase against the active feature.

## Behavior

1. Determine active feature (same rules as /forge:plan).
2. Read `state.json`. Require `tier in ("standard", "full")` and `phases.plan.status == "done"`. Otherwise abort.
3. Call `tools.state.start_phase(path, "crucible")`.
4. Invoke the `forge-crucible` skill.
5. On completion, print: feature id, assumptions confirmed count, failure modes identified, decisions logged, next phase = `review`.

## Failure modes

- `tier == "focused"` → abort: "Crucible is standard-tier+. Re-run /forge:spec --standard or use /forge:execute directly for focused work."
- SPEC.md or PLAN.md missing → abort with: "Crucible requires both SPEC.md and PLAN.md to challenge."
- User declines all adversarial questions → log and surface; phase remains `in_progress` so user can resume.

## Constitution preflight

When `.forge/CONSTITUTION.md` is present, the skill calls `tools.constitution.load_and_filter` before its primary work and passes filtered `articles[]` into every subagent dispatch budget. No-op when absent.
