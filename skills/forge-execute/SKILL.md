---
name: forge-execute
description: Run the execute phase. Use when the active feature has current_phase=execute. Focused tier drives directly from SPEC.md acceptance criteria with one subagent dispatch. Standard and full tiers drive from PLAN.md slice-by-slice with wave parallelism and per-subagent context budgets. The PreToolUse hook enforces budget contracts.
disable-model-invocation: true
---

# FORGE Execute

## When this skill applies

Either:
- **Focused** (M1): `current_phase == "execute"` and no PLAN.md; drive from SPEC.md acceptance criteria with one dispatch.
- **Standard / Full** (M2): `current_phase == "review"` with `phases.review.status == "in_progress"` and `"plan"` in `phases.review.targets_done` (the plan-review pass closed but the review phase remains open until target=code completes), OR `current_phase == "execute"` (re-entry on a partial execute). PLAN.md exists with `REVIEW.plan.md` `status: resolved` for `target: plan`. Dispatch slice-by-slice, wave-by-wave.

## Inputs

- `.forge/features/<id>/SPEC.md` — source of truth.
- `.forge/features/<id>/PLAN.md` — required for standard / full.
- `.forge/features/<id>/UNDERSTANDING.md` — read by review-time decisions; not always re-read here.
- `.forge/intel/modules.md` — only when `Files in scope` for the slice spans more than one module.

## Steps

1. **Validate tier and state.** Read `state.json`. For `tier in ("standard", "full")`, require PLAN.md with `status: ready`, `REVIEW.plan.md` with `target: plan` and `status: resolved`, and `"plan"` recorded in `phases.review.targets_done` (the gate's audit trail). The review phase will be `status: in_progress` at this point — that is expected; do not abort.
2. **Transition state.** Call `tools.state.start_phase(path, "execute")` (idempotent if already in_progress). For standard/full, this changes `current_phase` from `review` to `execute` while leaving `phases.review` untouched so the plan-pass audit (`targets_done`, `current_target`) survives until the second review pass completes.
2a. **Constitution preflight.** Call `tools.constitution.load_and_filter(repo_root, idea_text=<spec_intent>, files_in_scope=<plan_files_union>)`. The resulting `articles[]` (serialized via `Article.to_budget_dict()`) is included in EVERY per-task subagent dispatch budget under the `articles` field.
3. **Branch on tier.**

### Focused branch (M1 behavior, unchanged)

3a. Derive a one-slice plan in memory. List all acceptance criteria as the slice's wave 1 tasks. Do NOT write a PLAN.md.
3b. Dispatch ONE execute subagent per the M1 focused-tier behavior:
   - Budget: SPEC § [Intent, Codebase Anchors, Scope, Scenarios, Acceptance, Negative Requirements]; `files_in_scope` = union of Codebase Anchors paths.
   - Task: implement each acceptance criterion via TDD.
3c. Append summary to `.forge/features/<id>/slice-1.summary`. Append commit shas to `state.commits[]` with the schema-required fields only — `{ "sha": "...", "phase": "execute", "subject": "...", "logged_at": "..." }`. Slice membership lives in `slice-<N>.summary`, not in `state.commits[]` (the schema rejects extra keys).

### Standard / Full branch (M2)

3d. Read PLAN.md. Parse slices in order. For each slice:
   - Mark `state.json.phases.execute.current_slice = <N>` (already permitted by schema).
   - Walk waves in order. Within a wave, dispatch tasks in parallel; between waves, sequential.
   - For each task: dispatch ONE subagent. Budget block MUST include:
     - `spec_sections`: SPEC sections this task implements (e.g. `[Intent, Scenarios.scenario-2]`).
     - `files_in_scope`: the slice's Files in scope union, scoped down to the task when possible.
     - `owned_files`: files this specific task writes.
     - `read_only_files`: files the task reads but does not modify.
     - `prior_summaries`: slice summaries from prior slices (always); prior task summaries from THIS slice (only when needed).
     - `articles`: filtered Constitution articles (empty list when `.forge/CONSTITUTION.md` is absent).
   - Receive subagent summary (≤500 words). Append commit shas to `state.commits[]` with the schema-required fields only — `{ "sha": "...", "phase": "execute", "subject": "...", "logged_at": "..." }`. **Do NOT add a `slice` key** — `state.schema.json` enforces `additionalProperties: false` on `commits[]` and the write will be rejected. Record slice membership in `slice-<N>.summary` instead.
3e. After each slice completes:
   - Write `.forge/features/<id>/slice-<N>.summary` with the aggregated wave outputs.
   - Self-review gate per slice: every acceptance criterion mapped to this slice has ≥1 commit; no Negative Requirement violated by the diff.
   - If working tree has uncommitted changes after the slice, warn and let the user decide (config.git.auto_commit handles this in M3+).
3f. Update PLAN.md frontmatter `status: done` after the last slice completes.

4. **Final self-review gate (both branches):**
   - Every acceptance criterion maps to ≥1 commit recorded in `state.commits`.
   - Every Negative Requirement passes a code-audit search (no MUST-NOT behavior present).
   - Run `python -m tools.validate --target deviations .forge/features/<id>` to confirm every `state.json` deviation has a matching `decisions.md` entry. Any finding with severity `BLOCK` or `HIGH` blocks phase exit. `MEDIUM`, `LOW`, and `INFO` are advisory; surface to the user.
   - All tests in scope pass on the working tree.
5. **Transition state.** Call `tools.state.complete_phase(path, "execute")`. Standard / full → leave further state changes to `/forge:review --target code`, which resumes the open review phase (carrying the `targets_done=["plan"]` audit through `start_phase`'s preservation, so the per-target gate is satisfied once the code pass closes). Focused → `tools.state.start_phase(path, "verify")`.
6. **Surface to user:** slice summaries written, commit count, criteria-with-commit map, next phase.

## Done

All acceptance criteria have at least one commit recorded in `state.commits`. PLAN.md status is `done` for standard / full. `state.json` reflects execute=done.
