---
name: idd-execute
description: Run the execute phase. Use when the active feature has current_phase=execute. Focused tier drives directly from SPEC.md acceptance criteria with one subagent dispatch. Standard and full tiers drive from PLAN.md slice-by-slice with wave parallelism and per-subagent context budgets. The PreToolUse hook enforces budget contracts.
disable-model-invocation: true
---

# IDD Execute

## When this skill applies

Active feature `state.json` has `current_phase == "execute"`. Branches on `tier`:

- **Focused** (M1): no PLAN.md; drive from SPEC.md acceptance criteria with one dispatch.
- **Standard / Full** (M2): PLAN.md exists with `REVIEW.plan.md` `status: resolved` for `target: plan`. Dispatch slice-by-slice, wave-by-wave.

## Inputs

- `.idd/features/<id>/SPEC.md` — source of truth.
- `.idd/features/<id>/PLAN.md` — required for standard / full.
- `.idd/features/<id>/UNDERSTANDING.md` — read by review-time decisions; not always re-read here.
- `.idd/intel/modules.md` — only when `Files in scope` for the slice spans more than one module.

## Steps

1. **Validate tier and state.** Read `state.json`. For `tier in ("standard", "full")`, also require PLAN.md with `status: ready` and `REVIEW.plan.md` with `target: plan` and `status: resolved`.
2. **Transition state.** Call `tools.state.start_phase(path, "execute")` (idempotent if already in_progress).
3. **Branch on tier.**

### Focused branch (M1 behavior, unchanged)

3a. Derive a one-slice plan in memory. List all acceptance criteria as the slice's wave 1 tasks. Do NOT write a PLAN.md.
3b. Dispatch ONE execute subagent per the M1 focused-tier behavior:
   - Budget: SPEC § [Intent, Codebase Anchors, Scope, Scenarios, Acceptance, Negative Requirements]; `files_in_scope` = union of Codebase Anchors paths.
   - Task: implement each acceptance criterion via TDD.
3c. Append summary to `.idd/features/<id>/slice-1.summary`. Append commit shas to `state.commits[]` with the schema-required fields only — `{ "sha": "...", "phase": "execute", "subject": "...", "logged_at": "..." }`. Slice membership lives in `slice-<N>.summary`, not in `state.commits[]` (the schema rejects extra keys).

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
   - Receive subagent summary (≤500 words). Append commit shas to `state.commits[]` with the schema-required fields only — `{ "sha": "...", "phase": "execute", "subject": "...", "logged_at": "..." }`. **Do NOT add a `slice` key** — `state.schema.json` enforces `additionalProperties: false` on `commits[]` and the write will be rejected. Record slice membership in `slice-<N>.summary` instead.
3e. After each slice completes:
   - Write `.idd/features/<id>/slice-<N>.summary` with the aggregated wave outputs.
   - Self-review gate per slice: every acceptance criterion mapped to this slice has ≥1 commit; no Negative Requirement violated by the diff.
   - If working tree has uncommitted changes after the slice, warn and let the user decide (config.git.auto_commit handles this in M3+).
3f. Update PLAN.md frontmatter `status: done` after the last slice completes.

4. **Final self-review gate (both branches):**
   - Every acceptance criterion maps to ≥1 commit recorded in `state.commits`.
   - Every Negative Requirement passes a code-audit search (no MUST-NOT behavior present).
   - No deviations marked unresolved.
   - All tests in scope pass on the working tree.
5. **Transition state.** Call `tools.state.complete_phase(path, "execute")`. Standard / full → `tools.state.start_phase(path, "review")` (target: code) per the standard-tier flow. Focused → `tools.state.start_phase(path, "verify")`.
6. **Surface to user:** slice summaries written, commit count, criteria-with-commit map, next phase.

## Done

All acceptance criteria have at least one commit recorded in `state.commits`. PLAN.md status is `done` for standard / full. `state.json` reflects execute=done.
