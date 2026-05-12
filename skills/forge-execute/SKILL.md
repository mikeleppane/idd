---
name: forge-execute
description: Run the execute phase. Use when the active feature has current_phase=execute. Focused tier drives directly from SPEC.md acceptance criteria with one subagent dispatch. Standard and full tiers drive from PLAN.md slice-by-slice with wave parallelism and per-subagent context budgets. The PreToolUse hook enforces budget contracts.
disable-model-invocation: true
---

# FORGE Execute

> **`state.json` is hook-protected.** Mutate it only through the
> `tools.state.*` helpers — `complete_phase`, `start_phase`,
> `record_routing_decision`, `record_refined_idea`, `record_commit`,
> `append_deviation`, `set_execute_current_slice`. The PreToolUse hook
> at `hooks/check_state_writer.py` refuses direct `Write` / `Edit` /
> `MultiEdit` on `.forge/features/<id>/state.json` and surfaces a
> permission-deny with guidance toward the correct helper.

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

The per-task subagent dispatch's `# Steps` section MUST embed the
RED→TEST-COMMIT→IMPL-COMMIT scaffold defined between the
`<!-- scaffold:begin -->` / `<!-- scaffold:end -->` markers in
[forge-tdd](../forge-tdd/SKILL.md). That skill is the single source of
truth for the TDD-pairing discipline this orchestrator enforces; copy the
text verbatim into the dispatch prompt.

1. **Validate tier and state.** Read `state.json`. For `tier in ("standard", "full")`, require PLAN.md with `status: ready`, `REVIEW.plan.md` with `target: plan` and `status: resolved`, and `"plan"` recorded in `phases.review.targets_done` (the gate's audit trail). The review phase will be `status: in_progress` at this point — that is expected; do not abort.
2. **Transition state.** Call `tools.state.start_phase(path, "execute")` (idempotent if already in_progress). For standard/full, this changes `current_phase` from `review` to `execute` while leaving `phases.review` untouched so the plan-pass audit (`targets_done`, `current_target`) survives until the second review pass completes.
2a. **Constitution preflight.** Call `tools.constitution.load_and_filter(repo_root, idea_text=<spec_intent>, files_in_scope=<plan_files_union>)`. The resulting `articles[]` (serialized via `Article.to_budget_dict()`) is included in EVERY per-task subagent dispatch budget under the `articles` field.
2b. **Lessons preflight.** Call
    `tools.intel.lessons.load_and_filter(repo_root, idea_text=<spec_intent>, files_in_scope=<plan_files_union>)`.
    The resulting `lessons[]` (serialized via
    `Lesson.to_budget_dict()`) is included in EVERY per-task subagent
    dispatch budget under the `traps` field — parallel to the
    `articles` field documented in 2a. The PreToolUse hook
    (`hooks/check_budget.py`) is permissive on the `traps` field; this
    skill owns shape. Missing `.forge/intel/lessons.md` is a no-op (the
    loader returns `([], [])`); pass the empty list through to every
    per-task budget unchanged.
3. **Branch on tier.**

### Focused branch (M1 behavior, unchanged)

3a. Derive a one-slice plan in memory. List all acceptance criteria as the slice's wave 1 tasks. Do NOT write a PLAN.md.
3b. Dispatch ONE execute subagent per the M1 focused-tier behavior:
   - Budget: SPEC § [Intent, Codebase Anchors, Scope, Scenarios, Acceptance, Negative Requirements]; `files_in_scope` = union of Codebase Anchors paths.
   - Budget MUST also include `phase: "execute"` (literal) so the PreToolUse hook applies the TDD-pair check, and `tests_in_scope: string[]` listing the test files this dispatch creates or modifies (drives the validator's pairing check). When a paired test genuinely does not fit, set `tdd_exception_ref` to the matching ADR id from `decisions.md` (recorded as a `## TDD Exception: <AC-id>` heading with `Rationale`, `Reviewer`, and `Date` keys) — only then may `tests_in_scope` be empty.
   - Task: implement each acceptance criterion via TDD per [forge-tdd](../forge-tdd/SKILL.md); embed that skill's `<!-- scaffold:begin -->` / `<!-- scaffold:end -->` block verbatim in the dispatched subagent's `# Steps`.
   - **Required prompt prefix.** The dispatch prompt MUST start with a top-level `context_budget:` block at column 0 (outside any fenced code block). The PreToolUse hook (`hooks/check_budget.py`) refuses dispatches that omit it. Canonical shape — copy verbatim and substitute the bracketed values:

     ```text
     context_budget:
     {
       "phase": "execute",
       "spec_sections": ["Intent", "Codebase Anchors", "Scope", "Scenarios", "Acceptance", "Negative Requirements"],
       "files_in_scope": [
         "<each Codebase Anchors path from SPEC.md>"
       ],
       "tests_in_scope": [
         "<each test file this dispatch creates or modifies>"
       ],
       "forbidden": [
         "do not read outside files_in_scope",
         "do not edit any file under .forge/",
         "do not skip the RED commit"
       ],
       "articles": [ <output of Article.to_budget_dict() for each filtered article, or [] when CONSTITUTION.md is absent> ],
       "traps": [ <output of Lesson.to_budget_dict() for each filtered lesson, or [] when lessons.md is absent> ]
     }

     [task prose follows here, starting with a blank line]
     ```

     When `tests_in_scope` cannot be populated, set `"tdd_exception_ref": "<ADR-id>"` to a matching ADR in `decisions.md` and the hook will accept an empty `tests_in_scope`.
3c. Append summary to `.forge/features/<id>/slice-1.summary`. Record each commit via `tools.state.record_commit(path, sha=<sha>, phase="execute", subject=<subject>)` — the helper stamps `logged_at`, schema-validates the entry, and writes through the hook-protected path. Do NOT attempt to `Write`/`Edit`/`MultiEdit` `state.json` to append commits; the PreToolUse hook refuses those calls and a Bash-bypass would skip schema validation. Slice membership lives in `slice-<N>.summary`, not in `state.commits[]` (the schema rejects extra keys on commits[] items).

### Standard / Full branch (M2)

3d. Read PLAN.md. Parse slices in order. For each slice:
   - Stamp the slice cursor via `tools.state.set_execute_current_slice(path, slice_number=<N>)`. The helper validates `<N> >= 1`, requires `phases.execute.status == "in_progress"`, and writes through the hook-protected path. Do NOT `Write`/`Edit` `state.json` to set this field directly.
   - Walk waves in order. Within a wave, dispatch tasks in parallel; between waves, sequential.
   - For each task: dispatch ONE subagent. Budget block MUST include:
     - `phase`: literal `"execute"` so the PreToolUse hook applies the TDD-pair check.
     - `spec_sections`: SPEC sections this task implements (e.g. `[Intent, Scenarios.scenario-2]`).
     - `files_in_scope`: the slice's Files in scope union, scoped down to the task when possible.
     - `owned_files`: files this specific task writes.
     - `read_only_files`: files the task reads but does not modify.
     - `prior_summaries`: slice summaries from prior slices (always); prior task summaries from THIS slice (only when needed).
     - `articles`: filtered Constitution articles (empty list when `.forge/CONSTITUTION.md` is absent).
     - `traps`: filtered cross-feature trap lessons (empty list when
       `.forge/intel/lessons.md` is absent or no lessons match scope).
     - `tests_in_scope`: the test files this task creates or modifies — drives the validator's pairing check. The dispatched subagent's `# Steps` MUST embed the [forge-tdd](../forge-tdd/SKILL.md) `<!-- scaffold:begin -->` / `<!-- scaffold:end -->` block verbatim against these test files.
     - `tdd_exception_ref` (optional): an ADR id from `decisions.md` recorded as a `## TDD Exception: <AC-id>` heading with `Rationale`, `Reviewer`, and `Date` keys. Only with this set may `tests_in_scope` be empty; the rationale lives in the ADR.
   - **Required prompt prefix.** The dispatch prompt MUST start with a top-level `context_budget:` block at column 0 (outside any fenced code block). The PreToolUse hook (`hooks/check_budget.py`) refuses dispatches that omit it. Canonical shape — copy verbatim and substitute the bracketed values:

     ```text
     context_budget:
     {
       "phase": "execute",
       "spec_sections": ["<sections this task implements, e.g. Intent, Scenarios.scenario-2>"],
       "files_in_scope": [
         "<each file the slice scopes down to this task>"
       ],
       "owned_files": [
         "<each file this specific task writes>"
       ],
       "read_only_files": [
         "<each file the task reads but does not modify>"
       ],
       "prior_summaries": [
         "<each slice-N.summary from prior slices; add prior task summaries from this slice only when needed>"
       ],
       "tests_in_scope": [
         "<each test file this task creates or modifies>"
       ],
       "forbidden": [
         "do not read outside files_in_scope",
         "do not edit any file under .forge/",
         "do not skip the RED commit"
       ],
       "articles": [ <output of Article.to_budget_dict() for each filtered article, or [] when CONSTITUTION.md is absent> ],
       "traps": [ <output of Lesson.to_budget_dict() for each filtered lesson, or [] when lessons.md is absent> ]
     }

     [task prose follows here, starting with a blank line]
     ```

     When `tests_in_scope` cannot be populated, set `"tdd_exception_ref": "<ADR-id>"` to a matching ADR in `decisions.md` and the hook will accept an empty `tests_in_scope`.
   - Receive subagent summary (≤500 words). Record each commit via `tools.state.record_commit(path, sha=<sha>, phase="execute", subject=<subject>)` — the helper stamps `logged_at`, schema-validates the entry, and writes through the hook-protected path. **Do NOT** add a `slice` key (or any other extra) — the helper passes the payload through the schema, which enforces `additionalProperties: false` on `commits[]` items. Record slice membership in `slice-<N>.summary` instead. Direct `Write`/`Edit`/`MultiEdit` on `state.json` is refused by the PreToolUse hook.
3e. After each slice completes:
   - Write `.forge/features/<id>/slice-<N>.summary` with the aggregated wave outputs.
   - Self-review gate per slice: every acceptance criterion mapped to this slice has ≥1 commit; no Negative Requirement violated by the diff.
   - If working tree has uncommitted changes after the slice, warn and let the user decide (config.git.auto_commit handles this in M3+).
3f. Update PLAN.md frontmatter `status: done` after the last slice completes.

4. **Final self-review gate (both branches):**
   - Every acceptance criterion maps to ≥1 commit recorded in `state.commits`.
   - Every Negative Requirement passes a code-audit search (no MUST-NOT behavior present).
   - Run `python -m tools.validate --target deviations .forge/features/<id>` to confirm every `state.json` deviation has a matching `decisions.md` entry. Any finding with severity `BLOCK` or `HIGH` blocks phase exit. `MEDIUM`, `LOW`, and `INFO` are advisory; surface to the user.
   - Run `python -m tools.validate --target tdd_evidence .forge/features/<id>` to confirm every acceptance criterion has a paired test commit. Findings of severity `BLOCK` block phase exit; `LOW` and `INFO` are advisory.
   - All tests in scope pass on the working tree.
5. **Transition state.** Call `tools.state.complete_phase(path, "execute")`. Standard / full → leave further state changes to `/forge:review --target code`, which resumes the open review phase (carrying the `targets_done=["plan"]` audit through `start_phase`'s preservation, so the per-target gate is satisfied once the code pass closes). Focused → `tools.state.start_phase(path, "verify")`.
6. **Surface to user:** slice summaries written, commit count, criteria-with-commit map, next phase.

## Done

All acceptance criteria have at least one commit recorded in `state.commits`. PLAN.md status is `done` for standard / full. `state.json` reflects execute=done.
