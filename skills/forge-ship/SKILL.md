---
name: forge-ship
description: Reconcile a verified feature with the canonical capability SPEC.md and archive the feature folder. M2 supports first-ship only — refuses if the capability has already shipped (delta proposals are M3+). Use after /forge:verify completes for standard or full tier.
disable-model-invocation: true
---

# FORGE Ship

## Mode selection

`/forge:ship` accepts two optional flags:

- `--change <change_id>` — delta-merge mode (see below).
- `--promote-domain` — full-tier only, opt-in repo-wide glossary
  promotion (see "Optional: repo-wide glossary promotion" below).

Behaviour by flag:

- **Without flags** — feature ship (default). Run the existing
  feature-ship lifecycle below: validate phase=ship, parse REVIEW.code.md,
  apply Constitution gate, call `tools.archive.ship_feature`. This is the
  M2/M3-P3 path; no behaviour change.
- **With `--change <change_id>`** — delta merge. Skip the entire feature
  lifecycle and dispatch to the delta-merge subroutine below. The
  Constitution gate (P3 §5.3.9) does NOT apply — there is no feature
  folder, no REVIEW.code.md, no `articles[]` to surface.
- **With `--promote-domain`** — feature-ship lifecycle runs unchanged.
  After `tools.archive.ship_feature` returns successfully, an advisory
  glossary-promotion step fires against the archived feature path.
  Conflicts on diverging definitions are surfaced in the ship summary as
  a non-blocking advisory; ship has already committed.

## Optional: repo-wide glossary promotion (`--promote-domain`)

Glossary promotion is **post-ship and advisory**. It runs only after
`tools.archive.ship_feature` returns successfully (canonical spec written,
feature folder moved into the archive). Two reasons:

- The ship is the only transactional unit. Mutating
  `.forge/domain/glossary.md` before ship preflight runs would leave a
  repo glossary that references a feature that did not actually ship.
- The locked plan
  (`docs/plans/2026-05-08-m7-confidence-and-ux-polish.md` P1.7) requires
  that conflicts on diverging definitions never block ship.

When the user invokes `/forge:ship --promote-domain` AND
`state.json.tier == "full"` AND the feature carried a `DOMAIN.md`,
proceed as follows:

1. Capture the archived feature directory returned by step 4 (it is the
   second element of the `ship_feature` return tuple). The archived path
   is `.forge/features/archive/<feature_id>/`. Call:
   ```python
   result = tools.archive.promote_domain_to_repo(
       repo_root, feature_id, feature_dir=archived_path,
   )
   ```
2. When `result.status == "ok"`, surface the count of promoted and
   skipped terms (e.g.
   `Promoted 2 term(s); skipped 1 term(s) already in repo glossary.`).
3. When `result.status == "skipped"` (one or more glossary conflicts),
   surface the conflict list as a non-blocking advisory in the ship
   summary. Each row in `result.conflicts` carries `term`,
   `feature_definition`, `repo_definition`. **Do NOT abort the ship —
   ship has already committed.** Recommend that the user reconcile
   `.forge/domain/glossary.md` manually before promoting the next
   feature.

If the flag is set but the tier is not `full` or the feature has no
`DOMAIN.md`, surface a single-line notice and continue without promotion.

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
3.5 **Ship-time Constitution + trap-memory gate.**
   - Load filtered articles: `articles, _dropped_a = tools.constitution.load_and_filter(repo_root)`.
   - Load filtered lessons: `lessons, _dropped_l = tools.intel.lessons.load_and_filter(repo_root)` — fresh repo without `.forge/intel/lessons.md` returns `([], [])`.
   - Parse REVIEW.code.md: `findings = tools.ship_gate.parse_review_findings(.forge/features/<id>/REVIEW.code.md)` — the parser filters out `Status: resolved` and `Status: accepted-risk` rows automatically and emits one finding per `[constitution:A<n>]` tag AND one per `[lesson:L<NNN>]` tag in the row's Problem cell.
   - Partition by article level: `gate_a, warn_a, info_a = tools.ship_gate.partition_by_article_level(findings, articles)`.
   - Partition by lesson severity: `gate_l, warn_l, info_l = tools.ship_gate.partition_by_lesson_severity(findings, lessons)`.
   - Merge: `gate = gate_a + gate_l`, `warn = warn_a + warn_l`.
   - When `warn` is non-empty: print `tools.ship_gate.render_warn_summary(warn, articles, lessons=lessons)` (no acknowledge required; SHOULD-tagged articles and MEDIUM-severity lessons WARN only).
   - When `gate` is non-empty: print `tools.ship_gate.render_gate_prompt(gate, articles, lessons=lessons)` and read user input.
     - User types `ACKNOWLEDGE` (literal uppercase): build the ack hook via `ack_hook = tools.ship_gate.make_acknowledgement_hook(state_path=..., decisions_path=..., gate_findings=gate, articles=articles, lessons=lessons)` and **carry it into step 4** — do NOT call ack_hook here.
     - Anything else: abort ship with the user's choice surfaced; halt without state mutation.
   - When `gate` is empty: continue to step 3.55 with `ack_hook = None`.
3.55 **Ship-time git-conventions gate (WS2).**
   - Evaluate against the feature's `state.commits[]`:
     `partition = tools.ship_gate.evaluate_git_conventions_gate(.forge/features/<id>/)`.
   - When `partition.warn` is non-empty: print `tools.ship_gate.render_git_conventions_warn_summary(partition)` and continue (MEDIUM findings are advisory; ship is not blocked).
   - When `partition.gate` is non-empty (BLOCK / HIGH findings): print
     `tools.ship_gate.render_git_conventions_gate_prompt(partition)` and **abort ship**. The operator must either:
     - amend the offending commits and re-run `/forge:ship`, or
     - record an explicit ADR in `decisions.md` accepting the deviation, mark the findings as `Status: accepted-risk` in the relevant artifact, then re-run.
     The git-conventions gate has no `ACKNOWLEDGE` short-circuit — commit-message shape is mechanical, not subject to discretion. Skill exits non-zero with the rendered prompt as the surfaced message.
   - When `partition.gate` and `partition.warn` are both empty: continue to step 4.
3.6 **Pre-PR QA gate (optional, prompt-driven).** Before the atomic ship / archive step:

   - Compute the prompt default: `Y` when `state.json.tier in {"standard", "full"}`; `N` for `focused`. Surface as: `"Run QA before creating PR? [Y/n]"` (or `"[y/N]"` when default is `N`).
   - When the user passes `--no-qa-prompt`, skip the prompt entirely and behave as if the user answered with the tier-aware default.
   - When the user passes `--qa-override-with-rationale "<text>"`, the prompt is suppressed AND a `## QA Override` ADR is appended to `.forge/features/<id>/decisions.md` with the supplied rationale + reviewer + date — and the QA gate is bypassed for this ship. Mirror the `## TDD Exception` ADR block style from `templates/feature/decisions.md`:

     ```markdown
     ## QA Override
     **Rationale:** <text supplied via --qa-override-with-rationale>
     **Reviewer:** <operator handle>
     **Date:** YYYY-MM-DD
     **Scope:** pre-PR QA gate bypass for this ship
     ```

   - On user response `Y` (or default `Y` when prompt skipped):
     1. Build an `ArtifactDescriptor` by asking the user for `--artifact-kind {cli|library|service|ui|other}` and `--artifact-identifier <string>`. Both can also be passed as flags to `/forge:ship`; in that case do not re-prompt.
     2. Dispatch the `forge-qa` skill in pre-PR mode, equivalent to invoking it with `--against working-tree --feature <id> --artifact-kind <k> --artifact-identifier <i>`. Forward `--non-interactive` and `--no-adversarial` through if `forge-ship` received them.
     3. The QA skill produces `.forge/features/<id>/QA.md` and returns `verdict ∈ {delivers, partial, does-not-deliver}` plus `confidence ∈ {high, partial, low}`. The skill performs no `state.json` mutation in pre-PR mode (the qa phase remains pending; only post-merge `/forge:qa` flips its status).
     4. Gate behavior:
        - `verdict == "delivers"` → continue to step 4.
        - `verdict == "partial"` → prompt the user: `"QA verdict is partial. Continue ship? [y/N]"`. On `n` (default): abort ship without state mutation. On `y`: append a `## QA Override` ADR to `decisions.md` noting the partial verdict, the operator's confirmation, and the date, then continue to step 4.
        - `verdict == "does-not-deliver"` → abort ship with: `"forge-ship blocked by QA verdict 'does-not-deliver'. Review .forge/features/<id>/QA.md, fix findings, then re-run /forge:ship. Override with --qa-override-with-rationale '<reason>' if you must ship anyway."`. The `QA.md` is preserved on disk for review; no state mutation occurs.
   - On user response `N` (or default `N` when prompt skipped): skip QA. Print: `"QA gate skipped. /forge:qa --feature <id> --against merged is available after ship completes."`.
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
   - preflights the canonical-spec target (and, for legacy v1 features, the archive target);
   - writes the canonical spec at `.forge/specs/<capability>/SPEC.md`;
   - runs `_composed` against the still-live source folder so the in-place `state.json` reflects the ACK deviation (when present) AND `phases.ship.status: done`;
   - **For v3 features (default for new features):** the feature folder remains at `.forge/features/<id>/` after ship; archival is deferred to `/forge:qa` post-merge completion via `tools.archive.archive_feature_after_qa`. The returned `archive_path` is the still-live source folder.
   - **For v1 features (legacy, no `flow_version`):** moves the feature folder to `.forge/features/archive/<feature-id>/` at ship time and rolls back the canonical-spec write if the move fails.
   - On any preflight failure, raises `ArchiveError` with the limitation message and leaves the repo untouched (state.json was never mutated, no ghost deviation — Open Scoping #14). The skill should append the failure to `decisions.md` § Open and halt.

   For v1 features, when `_composed` runs successfully and the archive move then fails, `ship_feature` rolls back the canonical spec but the live `state.json` retains both the ACK deviation and the `phases.ship.status: done` mutation. The retry contract is documented in `tools/archive.py`; forge-ship surfaces the failure and instructs the user to re-run after resolving the archive blocker. v3 features have no archive-move step at ship and therefore cannot hit this retry path.

   When the §5.3.9 gate produced an ACKNOWLEDGE, the ship summary printed to the user includes a banner: `WITH UNRESOLVED CONSTITUTION FINDINGS - see decisions.md`. The audit trail (`state.json.deviations[]` entry + `decisions.md` heading) persists into the archive folder. Note: M3 does NOT modify the ship-feature commit subject itself — `tools.archive.ship_feature` composes its own subject and accepting a prefix arg is M4 work (see "Out of scope"). The deviation + decisions.md trail makes "didn't see it" non-credible after the fact even without a commit-subject signal.
5. **Self-review gate:**
   - Canonical spec exists at the expected path.
   - **v1 features:** Feature folder no longer exists at `.forge/features/<id>/`; archive folder exists at `.forge/features/archive/<id>/` with the full set of feature artifacts (SPEC.md, PLAN.md, UNDERSTANDING.md, `REVIEW.plan.md`, `REVIEW.code.md`, VERIFICATION.md, decisions.md, state.json); state.json (now under the archive) shows `current_phase == "done"`, `phases.ship.status == "done"`.
   - **v3 features:** Feature folder still present at `.forge/features/<id>/`; live state.json shows `phases.ship.status == "done"` and (after `complete_phase("ship")`) carries `shipped_at`. The folder will move to `.forge/features/archive/<id>/` at qa completion.
6. **Surface to user:** canonical spec path, archive path, capability slug, summary of what shipped (criteria count, scenarios count, evidence link).

## Done

Canonical capability SPEC.md exists at `.forge/specs/<capability>/SPEC.md`.

- **v1 features:** Feature folder archived at `.forge/features/archive/<id>/`; archived state reflects `done`.
- **v3 features:** Feature folder remains at `.forge/features/<id>/` with `shipped_at` recorded; archival is deferred to `/forge:qa` post-merge completion (`archive_feature_after_qa`).

## State writes

- `state.json` — `phases.ship.status` flips to `done` and `shipped_at` is recorded by `complete_phase("ship")`. For v1 features, `current_phase` also advances to `done` via `finish_feature`. For v3 features, `current_phase` advances to `qa` (the terminal phase before `done`); the qa phase entry is created with `status: pending`. On a `§5.3.9` ACKNOWLEDGE, an entry is appended to `state.json.deviations[]` (`phase: "ship"`, `resolution: "user_acknowledged"`).
- `.forge/specs/<capability>/SPEC.md` — canonical spec written for both v1 and v3 (rolled back on hook or archive failure for v1; v3 has no archive-move rollback path because the move is deferred).
- **v1 features only:** feature folder moved from `.forge/features/<id>/` to `.forge/features/archive/<id>/` at ship.
- **v3 features:** feature folder remains at `.forge/features/<id>/` until `/forge:qa --against merged` completes; the deferred move runs in `archive_feature_after_qa`.
- `.forge/domain/glossary.md` — modified only when `--promote-domain` is set, the tier is `full`, and no glossary conflict is surfaced (advisory; never blocks ship).
- `.forge/features/<id>/QA.md` — written by the optional pre-PR QA gate (step 3.6) when the operator accepts the prompt. Authored by `forge-qa`; ship does not edit it.
- `.forge/features/<id>/decisions.md` — appended to in two cases unrelated to the canonical lifecycle: (1) a `## QA Override` ADR when `--qa-override-with-rationale` is supplied or when the operator chooses to continue past a `partial` QA verdict; (2) `§5.3.9` ACKNOWLEDGE deviation entry.
- The pre-PR QA gate does NOT mutate `state.json`. The post-ship `qa` phase remains `pending`; only `/forge:qa --against merged` flips its status to `done`.

## See also

- `forge-qa` — the skill dispatched by the pre-PR QA gate; also the post-merge terminal phase.
- `tools.qa` — black-box acceptance, edge probing, capped adversarial probe, and Negative-Requirement re-grep helpers used by `forge-qa`.
- `templates/feature/QA.md` — `QA.md` artifact template and verdict + confidence aggregation rule.
- `templates/feature/decisions.md` — `## TDD Exception` ADR block style mirrored by the `## QA Override` ADR.
- `commands/ship.md` — slash-command surface; documents the QA-gate flags.
