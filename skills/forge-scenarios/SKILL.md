---
name: forge-scenarios
description: Expand SPEC.md § Scenarios into rigorous markdown Gherkin and, when the project's BDD framework is detected, emit executable .feature files. Use after /forge:spec for standard or full tier features. Refuses on focused tier.
disable-model-invocation: true
---

# FORGE Scenarios

## When this skill applies

Active feature `state.json` has `tier in ("standard", "full")` and `phases.spec.status == "done"`. The user invoked `/forge:scenarios` (or the standard-tier flow advanced to it).

## Inputs

- `.forge/features/<id>/SPEC.md` § Scenarios (existing markdown Gherkin, often loose).
- `.forge/features/<id>/SPEC.md` § Acceptance Criteria (each criterion must map to ≥1 scenario after this phase).
- Project root (passed in by the command) for `tools.bdd_detect.detect(repo_root)`.

## Steps

1. **Validate state.** Read `state.json`; abort if `tier == "focused"` or `phases.scenarios.status` is not `in_progress`.
1a. **Constitution preflight.** Call `tools.constitution.load_and_filter(repo_root, idea_text=<spec_intent>, files_in_scope=<spec_anchors>)`. Keep `articles[]` in this skill's working context while drafting Gherkin Given/When/Then steps so they respect article rules.
2. **Detect BDD framework.** Call `tools.bdd_detect.detect(repo_root)`. Three outcomes:
   - **Detected** → escalate to executable `.feature` files. Record the decision in `decisions.md` (timestamp + framework + features_dir).
   - **Not detected** → markdown Gherkin only. No file emission outside SPEC.md.
   - **Ambiguous** (deps present but features dir missing, or partial config) → ask user once: "I see <signal>. Do you want to enable executable scenarios (creates `<features_dir>/`)?" Cache the answer in `.forge/config.json` so future runs skip the prompt.
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
5. **Self-review gate (delegates to validator):**
   - Run: `python -m tools.validate --target scenarios .forge/features/<id>/SPEC.md`
   - Any finding with severity `BLOCK` or `HIGH` blocks phase exit. `MEDIUM`/`LOW` are advisory; surface to the user.
   - Inline check (not migrated): the escalation decision is logged to `decisions.md` when detection ran.
6. **Transition state.** Run the forge-state Bash CLI (do NOT translate to a Python heredoc):

   ```bash
   forge-state complete-phase --feature <id> --phase scenarios
   forge-state start-phase    --feature <id> --phase plan
   ```

   Module fallback: `PYTHONPATH=$CLAUDE_PLUGIN_ROOT python3 -m tools.state_cli ...`
7. **Surface to user:** scenarios count, escalation decision, path to `.feature` file (if emitted), next phase = `plan`.

## Done

`SPEC.md` § Scenarios is normalized; if escalation triggered, `<features_dir>/<feature-slug>.feature` exists. `state.json` reflects scenarios=done, current_phase=plan.
