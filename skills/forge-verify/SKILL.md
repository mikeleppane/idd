---
name: forge-verify
description: Run three-layer verification — Layer 1 code-audit subagent, Layer 2 scenario execution when the project has a BDD framework, Layer 3 conversational UAT for everything still UNVERIFIABLE. Produces VERIFICATION.md and updates state.json. Use after /forge:execute completes for any tier.
disable-model-invocation: true
---

# FORGE Verify

## Goal

Produce `.forge/features/<id>/VERIFICATION.md` where every acceptance criterion AND every negative requirement has Method, Status, and Evidence.

## Inputs

- `.forge/features/<id>/SPEC.md` (acceptance + scenarios + negative requirements).
- The working tree (commits since spec creation; cross-reference `state.commits[]`).
- `tools.bdd_detect.detect(repo_root)` — drives Layer 2 selection.
- `state.json` (skipped phases for the warning section).

## Three layers

### Layer 1 — Automated audit (always)

Subagent dispatch:
- Budget: SPEC § Acceptance + Scenarios + Negative Requirements + Codebase Anchors; `files_in_scope` = whatever the criteria reference.
- Task: for each acceptance criterion AND each negative requirement, search the codebase for evidence (function presence, conditional logic, type signatures). Mark EVIDENCED / FAIL / UNVERIFIABLE with `path:line` evidence. Use PASS only for scenario execution or UAT-confirmed behavior.
- Return: list of `{criterion, status, evidence}`.
- **Required prompt prefix.** The dispatch prompt MUST start with a top-level `context_budget:` block at column 0 (outside any fenced code block). The PreToolUse hook (`hooks/check_budget.py`) refuses dispatches that omit it. Canonical shape — copy verbatim and substitute the bracketed values:

  ```text
  context_budget:
  {
    "spec_sections": ["Acceptance", "Scenarios", "Negative Requirements", "Codebase Anchors"],
    "files_in_scope": [
      ".forge/features/<id>/SPEC.md",
      "<each Codebase Anchors path referenced by acceptance / negative requirements>"
    ],
    "forbidden": [
      "do not edit any file",
      "do not run pytest",
      "do not dispatch additional subagents"
    ],
    "articles": [ <output of Article.to_budget_dict() for each filtered article, or [] when CONSTITUTION.md is absent> ],
    "return_format": {
      "rows": "list[{criterion: str, status: enum[EVIDENCED, FAIL, UNVERIFIABLE], evidence: str}]",
      "max_words": 600
    }
  }

  [task prose follows here, starting with a blank line]
  ```

### Layer 2 — Scenario execution (when BDD framework detected)

1. Call `tools.bdd_detect.detect(repo_root)`. The result is `Detected | Ambiguous | NotDetected`.
2. If `NotDetected`:
   - Skip Layer 2. Log to VERIFICATION § Skipped phases with reason "no BDD framework detected".
3. If `Ambiguous(reason)`:
   - Log the partial signal to VERIFICATION § Skipped phases with the reason. Surface to the user; if they want to enable executable scenarios, they re-run `/forge:scenarios` (which now caches the decision in `.forge/config.json`). Do not invent a framework here.
4. If `Detected(framework)`:
   - Run the framework's command. **pytest-bdd** binds feature files via Python step modules using `from pytest_bdd import scenario, scenarios`; pytest collects from the test module, not from the `.feature` file directly. The `/forge:scenarios` skill (Task 6) creates the binding module under `tests/step_defs/test_<feature-slug>_steps.py` calling `scenarios("<features_dir>/<feature-slug>.feature")`. Layer 2 invokes pytest on that test module:
     - `python` + `pytest-bdd` → `pytest tests/step_defs/test_<feature-slug>_steps.py -v`
       (or, when the project ships a single binding module covering all features, `pytest tests/step_defs/ -v` filtered by `-k <feature-slug>`).
     - `node` + `cucumber-js` → `npx cucumber-js <features_dir>/<feature-slug>.feature`
     - `ruby` + `cucumber-ruby` → `bundle exec cucumber <features_dir>/<feature-slug>.feature`
     - `go` + `godog` → `go test -run TestFeatures ./<features_dir>/...`
   - Capture exit code and per-scenario pass/fail.
   - Map each scenario back to its acceptance criterion (via the `(criterion: <id>)` annotation written by `/forge:scenarios`).
   - For each PASS scenario, set the corresponding criterion's Method = `scenario-exec`, Status = `PASS`, Evidence = `<command> (exit 0)` plus the scenario's name.
   - For each FAIL scenario, set Status = `FAIL`, Evidence = the scenario name and capture the failure summary (≤200 chars).

### Layer 3 — Conversational UAT

For every UNVERIFIABLE criterion from Layer 1 (and any criterion the user requests UAT for), walk the user through the scenario and record their confirmation with timestamp.

## Steps

1. **Validate state.** `phases.execute.status == "done"`. For standard / full, also `phases.review.status == "done"` AND `.forge/features/<id>/REVIEW.code.md` exists with frontmatter `target: code`, `status: resolved`. Otherwise abort.
2. **Transition state.** Run the forge-state Bash CLI (do NOT translate to a Python heredoc):

   ```bash
   forge-state start-phase --feature <id> --phase verify
   ```

   Idempotent. Module fallback: `PYTHONPATH=$CLAUDE_PLUGIN_ROOT python3 -m tools.state_cli ...`.
2a. **Constitution preflight.** Call `tools.constitution.load_and_filter(repo_root, idea_text=<spec_intent>, files_in_scope=<plan_files>)`. Pass `articles[]` to the verify subagent so UAT questions can call out CRITICAL surfaces (e.g., "verify the vault path is the only secret source per [constitution:A1]").
3. **Run Layer 1.** Dispatch the audit subagent. Receive `{criterion, status, evidence}` list (acceptance + negative-requirement rows).
4. **Run Layer 2** per the rules above. Capture command + exit code into VERIFICATION § Coverage rows.
5. **Run Layer 3** for everything still UNVERIFIABLE. Walk user through each, log timestamps.
6. **Aggregate** into `VERIFICATION.md` Coverage and Negative-requirement-checks tables. Compute Gaps section. Compute Skipped phases section from `state.json.skipped`.
7. **Self-review gate:**
   - Every acceptance criterion AND every negative requirement has Method + Status + Evidence.
   - No criterion left at PENDING after Layer 3 (unless user explicitly accepts).
   - Skipped-phase warnings present when applicable.
8. **Transition state.** If all rows are EVIDENCED or PASS, run the forge-state Bash CLI (do NOT translate to a Python heredoc):

   - For tier == "focused":

     ```bash
     forge-state complete-phase --feature <id> --phase verify
     forge-state finish         --feature <id>
     ```

   - For tier in ("standard", "full"):

     ```bash
     forge-state complete-phase --feature <id> --phase verify
     forge-state start-phase    --feature <id> --phase ship
     ```

   Module fallback: `PYTHONPATH=$CLAUDE_PLUGIN_ROOT python3 -m tools.state_cli ...`.
9. **Surface to user:** path to VERIFICATION.md, total verified count, list of FAILs (if any), skipped-phase warnings, next step (`done` for focused, `/forge:ship` for standard / full).

## Done

VERIFICATION.md exists, satisfies the template, and self-review passed. `state.json.current_phase == "done"` for focused tier OR `current_phase == "ship"` for standard / full.
