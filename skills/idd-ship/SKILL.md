---
name: idd-ship
description: Reconcile a verified feature with the canonical capability SPEC.md and archive the feature folder. M2 supports first-ship only — refuses if the capability has already shipped (delta proposals are M3+). Use after /idd:verify completes for standard or full tier.
disable-model-invocation: true
---

# IDD Ship

## Goal

Promote a verified feature to a canonical capability and move the feature folder to the archive.

## Inputs

- `.idd/features/<id>/SPEC.md` — feature source.
- `.idd/features/<id>/VERIFICATION.md` — proof of execute.
- `templates/capability/SPEC.md` — canonical spec template shape.

## Steps

1. **Validate state.** Read `state.json`; require `current_phase == "ship"` and `phases.verify.status == "done"`.
2. **Read SPEC.md frontmatter.** Extract `capability` slug. If missing or invalid, abort and surface required edit.
3. **Compose canonical spec body** (in memory; nothing written yet).
   - Start from `templates/capability/SPEC.md`.
   - Set frontmatter: `capability: <slug>`, `status: shipped`, `created: <YYYY-MM-DD>`, `last_updated: <YYYY-MM-DD>`, `evidence: [{<feature-id>: features/archive/<feature-id>/}]`, `bounded_context: null`.
   - Body sections come from the feature SPEC.md: Intent, Scope, Domain, Scenarios, Acceptance Criteria, Negative Requirements (verbatim or lightly edited for tense — feature spec may be future-tense, canonical is present-tense for shipped behavior).
   - Decisions section links to `features/archive/<feature-id>/decisions.md` (relative path).
4. **Atomic ship with state-mutation hook.** Do NOT touch state.json before this step — `tools.archive.ship_feature` runs preflight first, and any state mutation before preflight risks marking state `done` when the ship aborts (capability already shipped, archive target exists, source missing). Call:

   ```python
   def _mark_done(source: Path) -> None:
       state_path = source / "state.json"
       tools.state.complete_phase(state_path, "ship")
       tools.state.finish_feature(state_path)

   tools.archive.ship_feature(
       repo_root, feature_id, capability, body, pre_archive_hook=_mark_done,
   )
   ```

   This single helper:
   - preflights all three target paths (feature source exists, archive target absent, canonical spec absent);
   - writes the canonical spec at `.idd/specs/<capability>/SPEC.md`;
   - runs `_mark_done` against the still-live source folder so the archived `state.json` reflects `current_phase: done` and `phases.ship.status: done`;
   - moves the feature folder to `.idd/features/archive/<feature-id>/`;
   - rolls back the canonical-spec write if the hook OR the archive move fails.
   - On any preflight failure, raises `ArchiveError` with the M2 limitation message and leaves the repo untouched (state.json was never mutated). The skill should append the failure to `decisions.md` § Open and halt.
5. **Self-review gate:**
   - Canonical spec exists at the expected path.
   - Feature folder no longer exists at `.idd/features/<id>/`.
   - Archive folder exists at `.idd/features/archive/<id>/` with the full set of feature artifacts (SPEC.md, PLAN.md, UNDERSTANDING.md, `REVIEW.plan.md`, `REVIEW.code.md`, VERIFICATION.md, decisions.md, state.json).
   - state.json (now under the archive) shows `current_phase == "done"`, `phases.ship.status == "done"`.
6. **Surface to user:** canonical spec path, archive path, capability slug, summary of what shipped (criteria count, scenarios count, evidence link).

## Done

Canonical capability SPEC.md exists at `.idd/specs/<capability>/SPEC.md`. Feature folder archived. State (now under the archive) reflects done.
