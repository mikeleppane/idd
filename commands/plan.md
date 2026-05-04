---
name: plan
description: Run the plan phase against the active feature. Use after /idd:scenarios to author PLAN.md with vertical slices, waves, and Verified Dependencies. Standard and full tiers only.
---

# /idd:plan

Run the IDD plan phase against the active feature.

## Behavior

1. Determine active feature (same rules as /idd:scenarios).
2. Read `state.json`. Require `tier in ("standard", "full")` and `phases.scenarios.status == "done"`. Otherwise abort.
3. Call `tools.state.start_phase(path, "plan")`.
4. Invoke the `idd-plan` skill.
5. On completion, print: feature id, slice count, files-in-scope (deduped union), Verified Dependencies count.

## Failure modes

- `tier == "focused"` → abort: "Focused tier drives execute directly from SPEC.md; PLAN.md is not used."
- New external deps proposed but PLAN.md § Verified Dependencies left empty → abort, instruct skill to fill it before phase exit.
- `templates/feature/PLAN.md` missing → instruct user to reinstall plugin.
