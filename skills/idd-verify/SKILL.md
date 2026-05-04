---
name: idd-verify
description: Run three-layer verification of a feature against SPEC.md acceptance criteria — code-audit, scenario execution (when executable), and conversational UAT. Use after /idd:execute completes for any tier. Produces VERIFICATION.md and updates state.json.
disable-model-invocation: true
---

# IDD Verify

## Goal

Produce `.idd/features/<id>/VERIFICATION.md` where every acceptance criterion AND every negative requirement has Method, Status, and Evidence.

## Inputs

- `.idd/features/<id>/SPEC.md` (acceptance criteria + scenarios + negative requirements).
- The working tree (commits since spec creation; cross-reference `state.commits[]`).
- Project's BDD framework if Scenarios were emitted as `.feature` files (M2+).
- `state.json` (skipped phases for the warning section).

## Three layers

### Layer 1 — Automated audit (always)

Subagent dispatch:
- Budget: SPEC.md § Acceptance + Scenarios + Negative Requirements + Codebase Anchors; `files_in_scope` = whatever the criteria reference.
- Task: for each acceptance criterion AND each negative requirement, search the codebase for evidence (function presence, conditional logic, type signatures). Mark EVIDENCED / FAIL / UNVERIFIABLE with `path:line` evidence. Use PASS only for scenario execution or UAT-confirmed behavior.
- Return: list of `{criterion, status, evidence}`.

### Layer 2 — Scenario execution (when executable)

If `.feature` files exist (M2+), run the project's BDD command (e.g. `pytest tests/features/`). Record exit code and per-scenario pass/fail.

In M1, Layer 2 is skipped automatically if no `.feature` files are present and the absence is logged to the Skipped phases section.

### Layer 3 — Conversational UAT

For every UNVERIFIABLE criterion from Layer 1 (and any criterion the user requests UAT for), walk the user through the scenario and record their confirmation with timestamp.

## Steps

1. **Validate state.** `phases.execute.status == "done"`. Otherwise abort.
2. **Transition state.** `tools.state.start_phase(path, "verify")`.
3. **Run Layer 1.** Dispatch the audit subagent. Receive `{criterion, status, evidence}` list (acceptance + negative-requirement rows).
4. **Run Layer 2** if `.feature` files exist. Capture command + exit code. Map scenario results to criteria.
5. **Run Layer 3** for everything still UNVERIFIABLE. Walk user through each, log timestamps.
6. **Aggregate** into `VERIFICATION.md` Coverage and Negative-requirement-checks tables. Compute Gaps section. Compute Skipped phases section from `state.json.skipped`.
7. **Self-review gate:**
   - Every acceptance criterion AND every negative requirement has Method + Status + Evidence.
   - No criterion left at PENDING after Layer 3 (unless user explicitly accepts).
   - Skipped-phase warnings present when applicable.
8. **Transition state.** If all rows are EVIDENCED or PASS, call `tools.state.complete_phase(path, "verify")`. For focused tier, then call `tools.state.finish_feature(path)` so `current_phase` becomes `done` (no `phases.done` entry — the schema's `propertyNames` rule forbids it).
9. **Surface to user:** path to VERIFICATION.md, total verified count, list of FAILs (if any), skipped-phase warnings.

## Done

VERIFICATION.md exists, satisfies the template, and self-review passed. `state.json.current_phase == "done"` for focused tier. The feature folder remains under `.idd/features/<id>/` (archival is a `/idd-ship` concern, M2+).
