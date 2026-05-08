---
name: forge-ship
description: Reconcile a verified feature with the canonical capability SPEC.md and archive the feature folder. M2 supports first-ship only — refuses if the capability has already shipped (delta proposals are M3+). Use after /forge:verify completes for standard or full tier.
disable-model-invocation: true
---

# FORGE Ship

## Mode selection

`/forge:ship` accepts an optional `--change <change_id>` flag.

- **Without `--change`** — feature ship (default). Run the existing
  feature-ship lifecycle below: validate phase=ship, parse REVIEW.code.md,
  apply Constitution gate, call `tools.archive.ship_feature`. This is the
  M2/M3-P3 path; no behaviour change.
- **With `--change <change_id>`** — delta merge. Skip the entire feature
  lifecycle and dispatch to the delta-merge subroutine below. The
  Constitution gate (P3 §5.3.9) does NOT apply — there is no feature
  folder, no REVIEW.code.md, no `articles[]` to surface.

## Mode: delta merge (`--change <id>`)

1. **Validate `change_id` slug.** Use `tools.archive._CHANGE_ID_RE` /
   `tools.archive._validate_change_id` (the same helper the merger uses
   in preflight) — abort early on malformed ids before reading any file.
2. **Read proposal frontmatter.** Open
   `.forge/changes/<change_id>/proposal.md`; extract `affects_capability`
   from the YAML frontmatter. This is the `capability` argument the merger
   needs.
3. **Construct hook.**
   `hook = tools.archive._mark_change_merged_hook(proposal_path)`.
4. **Call the merger.**
   `tools.archive.merge_delta_proposal(repo_root, change_id, capability,
   pre_archive_hook=hook)`. The hook is part of the merge transaction:
   `merge_delta_proposal` snapshots `proposal.md` before invoking the
   hook, and any post-hook failure restores it from `proposal-pre.md`.
5. **On success**, render the banner:
   ```
   Canonical updated:   <canonical_spec_path>
   Archived to:         <archive_path>
   Snapshots retained:  <archive>/canonical-pre.md, <archive>/proposal-pre.md
   ```
   Do NOT advance any feature state.json (none exists for change proposals).
6. **On `ArchiveError`**, surface the exception's message verbatim to the
   user. Do not retry automatically. Failures are documented as recoverable
   per the merger contract; the user inspects the error, fixes the cause
   (e.g., adjusts the proposal, re-validates, re-flips status to
   `approved`), and re-invokes `/forge:ship --change <change_id>`.

## Goal

Promote a verified feature to a canonical capability and move the feature folder to the archive.

## Inputs

- `.forge/features/<id>/SPEC.md` — feature source.
- `.forge/features/<id>/VERIFICATION.md` — proof of execute.
- `templates/capability/SPEC.md` — canonical spec template shape.

## Steps

1. **Validate state.** Read `state.json`; require `current_phase == "ship"` and `phases.verify.status == "done"`.
2. **Read SPEC.md frontmatter.** Extract `capability` slug. If missing or invalid, abort and surface required edit.
3. **Compose canonical spec body** (in memory; nothing written yet).
   - Start from `templates/capability/SPEC.md`.
   - Set frontmatter: `capability: <slug>`, `status: shipped`, `created: <YYYY-MM-DD>`, `last_updated: <YYYY-MM-DD>`, `evidence: [{<feature-id>: features/archive/<feature-id>/}]`, `bounded_context: null`.
   - Body sections come from the feature SPEC.md: Intent, Scope, Domain, Scenarios, Acceptance Criteria, Negative Requirements (verbatim or lightly edited for tense — feature spec may be future-tense, canonical is present-tense for shipped behavior).
   - Decisions section links to `features/archive/<feature-id>/decisions.md` (relative path).
3.5 **Ship-time Constitution gate (§5.3.9).**
   - Load filtered articles: `articles, _dropped = tools.constitution.load_and_filter(repo_root)`.
   - Parse REVIEW.code.md: `findings = tools.ship_gate.parse_review_findings(.forge/features/<id>/REVIEW.code.md)` — the parser filters out `Status: resolved` and `Status: accepted-risk` rows automatically.
   - Partition: `gate, warn, info = tools.ship_gate.partition_by_article_level(findings, articles)`.
   - When `warn` is non-empty: print `tools.ship_gate.render_warn_summary(warn, articles)` (no acknowledge required; SHOULD-tagged findings WARN once per spec §5.3.9).
   - When `gate` is non-empty: print `tools.ship_gate.render_gate_prompt(gate, articles)` and read user input.
     - User types `ACKNOWLEDGE` (literal uppercase): build the ack hook via `ack_hook = tools.ship_gate.make_acknowledgement_hook(state_path=..., decisions_path=..., gate_findings=gate, articles=articles)` and **carry it into step 4** — do NOT call ack_hook here.
     - Anything else: abort ship with the user's choice surfaced; halt without state mutation.
   - When `gate` is empty: continue to step 4 with `ack_hook = None`.
4. **Atomic ship with composed pre-archive hook.** Do NOT touch state.json before this step — `tools.archive.ship_feature` runs preflight first, and any state mutation before preflight risks marking state `done` when the ship aborts (capability already shipped, archive target exists, source missing). Compose the ack hook (when present) with `_mark_done`:

   ```python
   def _mark_done(source: Path) -> None:
       state_path = source / "state.json"
       tools.state.complete_phase(state_path, "ship")
       tools.state.finish_feature(state_path)

   def _composed(source: Path) -> None:
       if ack_hook is not None:
           ack_hook(source)   # writes deviation + decisions entry
       _mark_done(source)

   tools.archive.ship_feature(
       repo_root, feature_id, capability, body, pre_archive_hook=_composed,
   )
   ```

   This single helper:
   - preflights all three target paths (feature source exists, archive target absent, canonical spec absent);
   - writes the canonical spec at `.forge/specs/<capability>/SPEC.md`;
   - runs `_composed` against the still-live source folder so the archived `state.json` reflects the ACK deviation (when present) AND `current_phase: done` / `phases.ship.status: done`;
   - moves the feature folder to `.forge/features/archive/<feature-id>/`;
   - rolls back the canonical-spec write if the hook OR the archive move fails.
   - On any preflight failure, raises `ArchiveError` with the M2 limitation message and leaves the repo untouched (state.json was never mutated, no ghost deviation — Open Scoping #14). The skill should append the failure to `decisions.md` § Open and halt.

   When `_composed` runs successfully and the archive move then fails, `ship_feature` rolls back the canonical spec but the live `state.json` retains both the ACK deviation and the `current_phase: done` mutation. This matches the existing retry contract (`tools/archive.py:128-129`); forge-ship surfaces the failure and instructs the user to re-run after resolving the archive blocker.

   When the §5.3.9 gate produced an ACKNOWLEDGE, the ship summary printed to the user includes a banner: `WITH UNRESOLVED CONSTITUTION FINDINGS - see decisions.md`. The audit trail (`state.json.deviations[]` entry + `decisions.md` heading) persists into the archive folder. Note: M3 does NOT modify the ship-feature commit subject itself — `tools.archive.ship_feature` composes its own subject and accepting a prefix arg is M4 work (see "Out of scope"). The deviation + decisions.md trail makes "didn't see it" non-credible after the fact even without a commit-subject signal.
5. **Self-review gate:**
   - Canonical spec exists at the expected path.
   - Feature folder no longer exists at `.forge/features/<id>/`.
   - Archive folder exists at `.forge/features/archive/<id>/` with the full set of feature artifacts (SPEC.md, PLAN.md, UNDERSTANDING.md, `REVIEW.plan.md`, `REVIEW.code.md`, VERIFICATION.md, decisions.md, state.json).
   - state.json (now under the archive) shows `current_phase == "done"`, `phases.ship.status == "done"`.
6. **Surface to user:** canonical spec path, archive path, capability slug, summary of what shipped (criteria count, scenarios count, evidence link).

## Done

Canonical capability SPEC.md exists at `.forge/specs/<capability>/SPEC.md`. Feature folder archived. State (now under the archive) reflects done.
