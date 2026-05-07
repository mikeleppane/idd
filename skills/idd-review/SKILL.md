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
1.5. **Constitution preflight.** Call `tools.constitution.load_and_filter(repo_root, idea_text=<spec_intent>, files_in_scope=<spec_anchors_or_plan_files>)`. For target=code, the resulting `articles[]` (serialized via `Article.to_budget_dict()`) MUST be included in the heavy-subagent dispatch budget. **When `len(articles) > 0` AND target=code, the heavy pass is mandatory** — it cannot be skipped on a clean self-review (closes the self-review skip gap; see Open Scoping #13). The reviewer subagent tags every article violation in REVIEW.code.md with `[constitution:A<n>]` (e.g. `[constitution:A1] HIGH src/foo.py:42 — direct session call`). Severity mapping: CRITICAL→HIGH, SHOULD→MEDIUM, MAY→LOW.
2. **Mark active target.** Call `tools.state.set_review_target(path, review_target=<plan|code>)` so `phases.review.current_target` reflects which pass is in flight. Idempotent within an in-progress review — safe to call once per skill invocation.
3. **Copy template** if `REVIEW.<target>.md` does not exist: write `.idd/features/<id>/REVIEW.<target>.md` from `templates/feature/REVIEW.md` with frontmatter: `spec: <feature-id>`, `target: <plan|code>`, `status: open`, `cycles: 1`.
4. **Cycle N — Self-review pass.**
   - For target=plan: walk every slice. Check (a) every acceptance criterion mapped to exactly one slice; (b) every file in scope appears in exactly one slice unless shared; (c) Verified Dependencies non-empty when new deps; (d) wave dependencies make sense (no Wave 2 task depends on a Wave 3 task).
   - For target=code: walk every commit since spec creation. Check (a) commit message follows Conventional Commits with allowed scope; (b) commit content matches one PLAN.md task; (c) tests added or modified for new behavior; (d) no obvious misalignment with SPEC § Negative Requirements.
   - Append findings to the per-target `REVIEW.<target>.md` § Findings with `Source: self`. Severity: BLOCK / HIGH / MEDIUM / LOW.
5. **Cycle N — Heavy subagent pass.** Triggered when ANY of:
   - self-review surfaced ≥ 1 HIGH+ finding, OR
   - user explicitly requested `--heavy`, OR
   - **`target == "code"` AND `len(articles) > 0`** — closes the self-review skip gap (Open Scoping #13). Without this rule a Constitution violation that self-review misses would never get tagged, and the §5.3.9 ship gate would see nothing.

   Steps:
   - Dispatch ONE review subagent. Apply the `idd-context-budget` skill rules and `idd-subagent-dispatch` shape. The PreToolUse hook (`hooks/check_budget.py`) tolerates the optional `articles` budget field.
   - Budget: SPEC § Acceptance + Negative Requirements + UNDERSTANDING § Pre-Mortem; for target=code, also `git diff --stat` plus the touched files. The `articles` budget field carries the filtered Constitution articles serialized via `Article.to_budget_dict()` (Task 5).
   - Task: produce findings the self-pass missed. For target=code, additionally check the diff against every article in `articles`. **Tag every article-related finding** in the REVIEW.code.md row's Problem column with `[constitution:A<n>]` (matching the article's `id`). Severity mapping for article violations:
     - CRITICAL article → HIGH severity finding.
     - SHOULD article → MEDIUM severity finding.
     - MAY article → LOW severity finding.

     Every emitted row MUST include `Status: open` so the §5.3.9 ship gate can identify unresolved findings.

     Example finding row:

     | F-7 | HIGH | open | src/services/checkout.py:142 | [constitution:A1] direct ORM session call in service layer (Article 1 — Repository pattern) | move to `repository/checkout.py` | heavy-subagent |
   - Append findings to `REVIEW.<target>.md` with `Source: heavy-subagent`.
6. **Update Convergence Log row** for cycle N in `REVIEW.<target>.md`: findings opened, findings resolved (the planning agent or user resolved them), HIGH+ remaining.
7. **Drive convergence.**
   - When a finding is resolved (code or spec edit), update its row's `Status` from `open` to `resolved` in `REVIEW.<target>.md`.
   - When the user logs an exception in `decisions.md` referencing the finding id, update the row's `Status` to `accepted-risk`.
   - If HIGH+ remaining > 0 AND cycle N < 3: surface findings to user, accept resolutions (edits to SPEC / PLAN / code or accepted-risk entries in `decisions.md`), bump `REVIEW.<target>.md` frontmatter `cycles: N+1`, repeat steps 4–6.
   - If HIGH+ remaining > 0 AND cycle N == 3: keep `REVIEW.<target>.md` frontmatter `status: open`, surface to user with full residual list, halt without transitioning state. Document blocker in `decisions.md` § Open.
   - If HIGH+ remaining == 0: set `REVIEW.<target>.md` frontmatter `status: resolved`, proceed.
8. **Self-review gate:** `REVIEW.<target>.md` status is `resolved` AND no BLOCK findings remain unresolved.
9. **Record target completion.** Call `tools.state.complete_review_target(path, review_target=<plan|code>)` so `phases.review.targets_done` records this pass. Idempotent within the same target.
10. **Transition state — depends on target:**
   - **target=plan:** review phase stays `in_progress`; `targets_done == ["plan"]`. Do **not** call `complete_phase("review")` yet — the gate requires both targets done. The next phase command is `/idd:execute` (which still observes review as in_progress; `idd-execute` accepts that state).
   - **target=code:** both targets are now in `targets_done`. Call `tools.state.complete_phase(path, "review")` (the gate clears) followed by `tools.state.start_phase(path, "verify")`.
11. **Surface to user:** `REVIEW.<target>.md` path, findings count by severity, cycles used, and the resolved next phase (`/idd:execute` after target=plan; `/idd:verify` after target=code).

## Done

`REVIEW.<target>.md` exists, status is `resolved`, no BLOCK or HIGH findings remain unresolved. `phases.review.targets_done` records the just-completed target. After target=code only, `state.json` reflects review=done.
