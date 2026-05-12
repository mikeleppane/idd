---
name: forge-plan
description: Build a slice + wave PLAN.md from a validated SPEC.md. File-bound, acceptance-mapped, with a Verified Dependencies section when new external deps are introduced. Use after /forge:scenarios completes for standard or full tier.
disable-model-invocation: true
---

# FORGE Plan

## When this skill applies

Active feature `state.json` has `tier in ("standard", "full")`, `phases.scenarios.status == "done"`, and `current_phase == "plan"`.

## Inputs

- `.forge/features/<id>/SPEC.md` (full).
- `.forge/intel/modules.md` if it exists (consulted only when slicing crosses module boundaries).
- `templates/feature/PLAN.md` (template shape).

## Steps

1. **Validate state.** Read `state.json`; abort if not in plan phase.
2. **Copy template** if PLAN.md does not exist: copy `templates/feature/PLAN.md` into `.forge/features/<id>/PLAN.md`. Set frontmatter: `spec: <feature-id>`, `slices: <integer>`, `status: ready`.
2a. **Constitution preflight.** Call `tools.constitution.load_and_filter(repo_root, idea_text=<spec_intent>, files_in_scope=<spec_anchors>)`. Keep `articles[]` in this skill's working context. The planner MUST flag any slice whose `Files in scope` overlaps a CRITICAL article's domain (e.g., `repository/`, `vault.ts`, `secrets/`) under the slice's notes.
2b. **Lessons preflight.** Call
    `tools.intel.lessons.load_and_filter(repo_root, idea_text=<spec_intent>, files_in_scope=<spec_anchors>)`.
    Keep `traps[]` (Lesson records via `Lesson.to_budget_dict()`) in
    this skill's working context. The planner MUST flag any
    slice whose `Files in scope` overlaps a CRITICAL trap's tag domain
    (e.g. `secrets`, `fixtures`, `async`) under the slice's notes — the
    same flagging discipline that 2a enforces for Constitution articles
    applies here for trap tags. The PreToolUse hook
    (`hooks/check_budget.py`) is permissive on the `traps` field;
    shape ownership lives in this skill, not the hook. Missing
    `.forge/intel/lessons.md` is a no-op (the loader returns
    `([], [])`); pass the empty list through unchanged.
3. **Derive vertical slices.** Each slice ships end-to-end user-visible behavior, not a horizontal layer. Slice count rule: aim for 1–4 in standard tier; > 4 means feature is too big — surface to user.
4. **Per slice, define:**
   - **Goal:** end-to-end behavior the slice delivers.
   - **Spec sections:** which Intent / Scenario / Acceptance bullets the slice satisfies.
   - **Files in scope:** explicit relative paths inside `target_repo/`. Each file appears in exactly one slice unless flagged as `shared:` in a wave.
   - **Wave 1 (parallel):** independent tasks that can ship in parallel without shared state. Each task has a checkbox.
   - **Wave 2+ (sequential):** tasks that depend on Wave 1 output. Each wave is its own checklist block.
   - **Acceptance:** the spec sections / scenarios / criteria the slice unblocks.
5. **Verified Dependencies (only when new external deps introduced):**
   - Fill the table per design §7.3: package · version range · registry · source checked · key APIs used · notes.
6. **Self-review gate (delegates to validator):**
   - Run: `python -m tools.validate --target plan-tasks .forge/features/<id>/PLAN.md` (covers slice↔acceptance mapping, file-collision across slices).
   - Run: `python -m tools.validate --target verified-deps .forge/features/<id>/PLAN.md` (covers Verified Dependencies table shape; pass `--check-registries` for a live registry probe).
   - Any finding with severity `BLOCK` or `HIGH` blocks phase exit. `MEDIUM`/`LOW` are advisory; surface to the user.
   - Inline check (not migrated): slice count ≤ 4 in standard tier.
7. **Transition state.** Run the forge-state Bash CLI (do NOT translate to a Python heredoc):

   ```bash
   forge-state complete-phase --feature <id> --phase plan
   forge-state start-phase    --feature <id> --phase crucible
   ```

   Module fallback: `PYTHONPATH=$CLAUDE_PLUGIN_ROOT python3 -m tools.state_cli ...`
8. **Surface to user:** slice count, files-in-scope summary, deps decision, next phase = `crucible`.

## Done

`.forge/features/<id>/PLAN.md` exists, satisfies the template shape, and self-review passed. `state.json` reflects plan=done, current_phase=crucible.
