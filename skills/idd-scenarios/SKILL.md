---
name: idd-scenarios
description: Expand SPEC.md § Scenarios into rigorous markdown Gherkin and, when the project's BDD framework is detected, emit executable .feature files. Use after /idd:spec for standard or full tier features. Refuses on focused tier.
disable-model-invocation: true
---

# IDD Scenarios

## When this skill applies

Active feature `state.json` has `tier in ("standard", "full")` and `phases.spec.status == "done"`. The user invoked `/idd:scenarios` (or the standard-tier flow advanced to it).

## Inputs

- `.idd/features/<id>/SPEC.md` § Scenarios (existing markdown Gherkin, often loose).
- `.idd/features/<id>/SPEC.md` § Acceptance Criteria (each criterion must map to ≥1 scenario after this phase).
- Project root (passed in by the command) for `tools.bdd_detect.detect(repo_root)`.

## Steps

1. **Validate state.** Read `state.json`; abort if `tier == "focused"` or `phases.scenarios.status` is not `in_progress`.
2. **Detect BDD framework.** Call `tools.bdd_detect.detect(repo_root)`. Three outcomes:
   - **Detected** → escalate to executable `.feature` files. Record the decision in `decisions.md` (timestamp + framework + features_dir).
   - **Not detected** → markdown Gherkin only. No file emission outside SPEC.md.
   - **Ambiguous** (deps present but features dir missing, or partial config) → ask user once: "I see <signal>. Do you want to enable executable scenarios (creates `<features_dir>/`)?" Cache the answer in `.idd/config.json` so future runs skip the prompt.
3. **Rewrite SPEC.md § Scenarios in place.** Each scenario must:
   - Use `Scenario:` header followed by `Given/When/Then` lines.
   - Map to exactly one acceptance criterion (cross-link via inline note `(criterion: <id>)`).
   - Be self-contained (no implicit shared state from prior scenarios).
   - Cap at 8 in M2; if more are needed, surface to user — the slice is too big.
4. **If escalating to executable .feature files:**
   - Compute target path: `<features_dir>/<feature-slug>.feature`.
   - Write the file with the same scenarios, framework-correct syntax (e.g., `pytest-bdd` step decorators handled by step files, not the .feature itself).
   - Add the file path to `state.json.commits[]` evidence later when execute commits it.
   - DO NOT generate step definitions — that is execute-phase work.
5. **Self-review gate:**
   - Every acceptance criterion has ≥1 scenario.
   - Every scenario maps to ≥1 acceptance criterion (no orphan scenarios).
   - No scenario contains "should", "might", "TBD".
   - Escalation decision is logged to `decisions.md` when detection ran.
6. **Transition state.** Call `tools.state.complete_phase(path, "scenarios")`, then `tools.state.start_phase(path, "plan")`.
7. **Surface to user:** scenarios count, escalation decision, path to `.feature` file (if emitted), next phase = `plan`.

## Done

`SPEC.md` § Scenarios is normalized; if escalation triggered, `<features_dir>/<feature-slug>.feature` exists. `state.json` reflects scenarios=done, current_phase=plan.
