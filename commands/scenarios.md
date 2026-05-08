---
name: scenarios
description: Run the scenarios phase against the active feature. Use after /forge:spec to expand SPEC.md § Scenarios into rigorous markdown Gherkin and (when the project's BDD framework is detected) emit executable .feature files.
---

# /forge:scenarios

Run the FORGE scenarios phase against the active feature.

## Behavior

1. Determine active feature: `--feature <id>` flag, otherwise the most recently modified `.forge/features/*/state.json`.
2. Read `state.json`. Require `tier in ("standard", "full")` and `phases.spec.status == "done"`. Otherwise abort.
3. Call `tools.state.start_phase(path, "scenarios")`.
4. Invoke the `forge-scenarios` skill (see `skills/forge-scenarios/SKILL.md`).
5. On completion, print: feature id, scenarios count, escalation decision (markdown only OR emitted to `<bdd_features_dir>/<slug>.feature`).

## Failure modes

- `tier == "focused"` → abort with "Scenarios authoring is standard-tier+. Re-run /forge:spec --standard or use /forge:execute directly."
- SPEC.md missing § Scenarios → abort, instruct user to re-enter spec phase.
- BDD framework partially configured (deps without features dir) → surface ambiguity, ask user once and cache decision in `.forge/config.json` per `tools.bdd_detect`.

## Constitution preflight

When `.forge/CONSTITUTION.md` is present, the skill calls `tools.constitution.load_and_filter` before its primary work and passes filtered `articles[]` into every subagent dispatch budget. No-op when absent.
