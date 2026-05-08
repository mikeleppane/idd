---
name: verify
description: Run the verify phase against the active feature — three-layer verification (code-audit, scenario execution when executable, conversational UAT). Use after /forge:execute (focused tier) or after /forge:review --target code resolves (standard / full tier). Produces VERIFICATION.md and updates state.json; Layer 2 runs the project's BDD command when tools.bdd_detect resolves to Detected.
---

# /forge:verify

Run the FORGE verify phase against the active feature.

## Behavior

1. Determine active feature.
2. Read `state.json`. Require `phases.execute.status == "done"`. For standard / full, additionally require `phases.review.status == "done"` AND `.forge/features/<id>/REVIEW.code.md` exists with frontmatter `target: code`, `status: resolved`.
3. Call `tools.state.start_phase(path, "verify")`.
4. Invoke the `forge-verify` skill.
5. On completion, print VERIFICATION.md path, totals, FAILs (if any), skipped-phase warnings, next recommended step.

## Failure modes

- `state.json` reports execute incomplete → abort.
- Layer 2 BDD command exits non-zero → record exit code in VERIFICATION.md and surface failures; phase remains in progress so user can fix.
- `tools.bdd_detect.detect` returns `Ambiguous(reason)` → log `reason` to VERIFICATION § Skipped phases; do not run Layer 2. User can re-run `/forge:scenarios` to cache a `bdd_framework` config decision in `.forge/config.json`.

## Constitution preflight

When `.forge/CONSTITUTION.md` is present, the skill calls `tools.constitution.load_and_filter` before its primary work and passes filtered `articles[]` into every subagent dispatch budget. No-op when absent.
