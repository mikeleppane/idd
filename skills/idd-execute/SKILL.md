---
name: idd-execute
description: Run focused-tier execute — implement a feature directly from SPEC.md acceptance criteria with subagent context-budget discipline. Use when the active feature has tier=focused and SPEC.md status is done. For standard/full tiers, refer to /idd-plan + /idd:execute (M2+).
disable-model-invocation: true
---

# IDD Execute (Focused Tier)

## When this skill applies

Active feature `state.json` has `tier == "focused"` and `phases.spec.status == "done"`. There is no `PLAN.md`. Implementation drives directly from `SPEC.md` acceptance criteria.

For standard/full tiers, this skill must abort and direct the user to `/idd-plan` (M2 territory).

## Inputs

- `.idd/features/<id>/SPEC.md` — source of truth.
- `.idd/intel/modules.md` — only when `Files in scope` for this feature span more than one module.

## Steps

1. **Validate tier.** Read `state.json`. If `tier != "focused"`, abort with: "This skill is focused-tier only. Run /idd-plan to author PLAN.md first."
2. **Transition state.** Call `tools.state.start_phase(path, "execute")`.
3. **Derive a one-slice plan in memory.** For focused tier, the slice is the whole feature: list all acceptance criteria as the slice's wave 1 tasks. Do NOT write a PLAN.md.
4. **Dispatch ONE execute subagent.**
   - Build the dispatch with the `context_budget:` block at the top (the `PreToolUse` hook will block it otherwise).
   - Apply the `idd-subagent-dispatch` shape rules.
   - Budget: SPEC.md sections [Intent, Codebase Anchors, Scope, Scenarios, Acceptance, Negative Requirements], `files_in_scope` = the union of Codebase Anchors paths (or, if absent, derive a tight set from the spec and confirm with the user before dispatch).
   - Task: implement each acceptance criterion via TDD. Tests first per criterion, then minimal implementation, then run.
   - Return: list of commit shas, decision_refs, deviations.
5. **Receive subagent summary.** Append it to `.idd/features/<id>/slice-1.summary`. Append each commit sha to `state.commits[]` with `phase: "execute"`.
6. **Self-review gate:**
   - Every acceptance criterion maps to ≥1 commit recorded in `state.commits`.
   - Every Negative Requirement passes a code-audit search (no MUST-NOT behavior present).
   - No deviations marked unresolved.
   - All tests in scope pass on the working tree.
7. **Transition state.** Call `tools.state.complete_phase(path, "execute")`, then `tools.state.start_phase(path, "verify")`.
8. **Surface to user:** path to slice-1.summary, commit count, criteria-with-commit map.

## Done

All acceptance criteria have at least one commit recorded in `state.commits`. `state.json` reflects execute=done. The user is ready for `/idd:verify`.
