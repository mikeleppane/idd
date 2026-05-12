---
name: forge-crucible
description: Run the three-step crucible ritual — assumptions inversion, adversarial Q&A, pre-mortem — producing UNDERSTANDING.md. Writes refutations back to SPEC.md or PLAN.md inline. Use after /forge:plan completes for standard or full tier.
disable-model-invocation: true
---

# FORGE Crucible

## When this skill applies

Active feature has SPEC.md and PLAN.md. `current_phase == "crucible"`.

## Inputs

- `.forge/features/<id>/SPEC.md` (full).
- `.forge/features/<id>/PLAN.md` (full).
- `.forge/features/<id>/decisions.md` (append-only log).
- `templates/feature/UNDERSTANDING.md`.

## Steps

1. **Validate state.** Read `state.json`; abort if not in crucible phase.
2. **Copy template** if UNDERSTANDING.md does not exist: copy `templates/feature/UNDERSTANDING.md` into `.forge/features/<id>/UNDERSTANDING.md`. Set frontmatter: `spec: <feature-id>`, `ritual: assumptions → adversarial → pre-mortem`, `generated: <YYYY-MM-DD>`.
2a. **Constitution preflight.** Call `tools.constitution.load_and_filter(repo_root, idea_text=<spec_intent>, files_in_scope=<plan_files>)`. Keep the filtered `articles[]` (via `Article.to_budget_dict()`) in this skill's working context while drafting the adversarial steps so they target Constitution-flagged surfaces.
3. **Step A — Assumptions inversion.**
   - Read SPEC § Intent, Scope, Acceptance. List the implicit assumptions (e.g., "user is authenticated", "input is well-formed", "DB is reachable").
   - For each: invert it. State the opposite. Decide: is the spec robust under inversion, or does it break?
   - Surface inverted assumptions that break the spec to the user. Capture confirmations or refutations into UNDERSTANDING § Confirmed Assumptions.
4. **Step B — Adversarial Q&A.**
   - Generate 3–5 hard questions designed to expose weak spots: "What if X is false?", "Why this approach over <alternative>?", "What evidence supports criterion N?".
   - Ask the user one at a time. Capture each Q+A+Resolution into UNDERSTANDING § Adversarial Q&A.
   - When a resolution requires a spec or plan change, edit SPEC.md or PLAN.md inline AND log the change to `decisions.md`.
5. **Step C — Pre-mortem.**
   - Imagine the feature shipped and broke in production. List 2–4 plausible failure modes ("scenario fails under concurrent writes", "criterion N untestable in CI", "new dep yanked from registry").
   - For each: identify mitigation in the spec, plan, or as accepted risk in `decisions.md`. Capture into UNDERSTANDING § Pre-Mortem.
6. **Synthesize Shared Model Statement.** One paragraph: "We are building X to solve Y for Z, knowing W, explicitly excluding V."
7. **Self-review gate:**
   - All three sections (Confirmed Assumptions, Adversarial Q&A, Pre-Mortem) are non-empty.
   - Every Q+A has a Resolution.
   - Every failure mode has a Mitigation.
   - Spec/plan edits made during the ritual are logged to `decisions.md` with timestamps.
8. **Transition state.** Call `tools.state.complete_phase(path, "crucible")`, then `tools.state.start_phase(path, "review")`.
9. **Surface to user:** UNDERSTANDING.md path, counts (assumptions confirmed, Q&A pairs, failure modes), decisions logged, next phase = `review`.

## Done

`.forge/features/<id>/UNDERSTANDING.md` exists with all three ritual sections filled and a Shared Model Statement. `state.json` reflects crucible=done, current_phase=review.
