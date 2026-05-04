---
name: idd-review
description: Layered review — cheap self-review pass plus an optional heavy on-demand subagent pass — feeding a convergence loop that drives HIGH+ findings to zero across max 3 cycles. Targets PLAN.md or code diff. Use after /idd:crucible (plan target) or /idd:execute (code target).
disable-model-invocation: true
---

# IDD Review

## When this skill applies

Active feature is in review phase. Target is `plan` (after crucible) or `code` (after execute).

## Inputs

- `.idd/features/<id>/SPEC.md`, `PLAN.md`, `UNDERSTANDING.md`.
- For target=code: the working tree diff since spec creation, plus `state.commits[]`.
- `templates/feature/REVIEW.md`.

## Output naming

The standard-tier flow runs review twice — once after crucible (`target: plan`) and once after execute (`target: code`). The two passes write to **separate, per-target files** so neither audit trail gets clobbered:

- `target=plan` → `.idd/features/<id>/REVIEW.plan.md`
- `target=code` → `.idd/features/<id>/REVIEW.code.md`

The plain name `.idd/features/<id>/REVIEW.md` is reserved (do not write it). Downstream phases read these per-target files: `/idd:execute` requires `REVIEW.plan.md` with `target: plan`, `status: resolved`; `/idd:verify` requires `REVIEW.code.md` with `target: code`, `status: resolved`.

## Steps

1. **Validate state.** Read `state.json`; abort if not in review phase.
2. **Copy template** if `REVIEW.<target>.md` does not exist: write `.idd/features/<id>/REVIEW.<target>.md` from `templates/feature/REVIEW.md` with frontmatter: `spec: <feature-id>`, `target: <plan|code>`, `status: open`, `cycles: 1`.
3. **Cycle N — Self-review pass.**
   - For target=plan: walk every slice. Check (a) every acceptance criterion mapped to exactly one slice; (b) every file in scope appears in exactly one slice unless shared; (c) Verified Dependencies non-empty when new deps; (d) wave dependencies make sense (no Wave 2 task depends on a Wave 3 task).
   - For target=code: walk every commit since spec creation. Check (a) commit message follows Conventional Commits with allowed scope; (b) commit content matches one PLAN.md task; (c) tests added or modified for new behavior; (d) no obvious misalignment with SPEC § Negative Requirements.
   - Append findings to the per-target `REVIEW.<target>.md` § Findings with `Source: self`. Severity: BLOCK / HIGH / MEDIUM / LOW.
4. **Cycle N — Heavy subagent pass (only when self-review surfaces ≥ 1 HIGH+ finding OR user explicitly requests `--heavy`).**
   - Dispatch ONE review subagent. Apply the `idd-context-budget` skill rules and `idd-subagent-dispatch` shape. The PreToolUse hook (`hooks/check_budget.py`) blocks malformed dispatches mechanically.
   - Budget: SPEC § Acceptance + Negative Requirements + UNDERSTANDING § Pre-Mortem; for target=code, also `git diff --stat` plus the touched files.
   - Task: produce findings the self-pass missed. Same severity scale.
   - Append findings to `REVIEW.<target>.md` with `Source: heavy-subagent`.
5. **Update Convergence Log row** for cycle N in `REVIEW.<target>.md`: findings opened, findings resolved (the planning agent or user resolved them), HIGH+ remaining.
6. **Drive convergence.**
   - If HIGH+ remaining > 0 AND cycle N < 3: surface findings to user, accept resolutions (edits to SPEC / PLAN / code or accepted-risk entries in `decisions.md`), bump `REVIEW.<target>.md` frontmatter `cycles: N+1`, repeat steps 3–5.
   - If HIGH+ remaining > 0 AND cycle N == 3: keep `REVIEW.<target>.md` frontmatter `status: open`, surface to user with full residual list, halt without transitioning state. Document blocker in `decisions.md` § Open.
   - If HIGH+ remaining == 0: set `REVIEW.<target>.md` frontmatter `status: resolved`, proceed.
7. **Self-review gate:** `REVIEW.<target>.md` status is `resolved` AND no BLOCK findings remain unresolved.
8. **Transition state.** Call `tools.state.complete_phase(path, "review")`. Next phase: for target=plan → `execute`; for target=code → `verify`. Call `tools.state.start_phase(path, <next>)`.
9. **Surface to user:** `REVIEW.<target>.md` path, findings count by severity, cycles used, next phase.

## Done

`REVIEW.<target>.md` exists, status is `resolved`, no BLOCK or HIGH findings remain unresolved. `state.json` reflects review=done.
