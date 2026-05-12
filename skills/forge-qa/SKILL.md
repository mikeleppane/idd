---
name: forge-qa
description: Run a fresh-outsider black-box acceptance pass, edge probe, adversarial probe, and Negative-Requirement regrep against a feature. Use when /forge:ship requests a pre-PR gate against the working tree, or after a feature ships to drive the terminal qa phase against the merged artifact. Writes QA.md.
disable-model-invocation: true
---

# FORGE QA

Outside black-box quality pass. Four sections — Acceptance, Edge Probing,
Adversarial, NR Regrep — folded into one `QA.md` with a mechanically-derived
verdict and confidence. The skill orchestrates fresh-outsider subagent
dispatches; it never reads the implementation tree itself.

## When this skill applies

Two entry paths:

- **Pre-PR gate.** Invoked by `/forge:ship` when the operator opted into the
  QA prompt. State machine is still in `current_phase == "ship"` with
  `phases.ship.status == "in_progress"`. The skill runs against the working
  tree at HEAD of the feature branch and returns a verdict + confidence to
  the caller; ship decides whether to proceed.
- **Post-merge phase.** Invoked directly via
  `/forge:qa --feature <id>` after ship completes. Requires
  `flow_version == 3` and `phases.qa.status == "pending"`. Runs against the
  merged tree and is the feature's terminal phase before `done`.

## Inputs

- `--feature <id>` — required for `--against working-tree`; optional for
  `--against merged` (single-active rule applies).
- `--against {working-tree|merged}` — defaults to `merged` for direct
  invocation, `working-tree` when `forge-ship` invokes the skill.
- `--artifact-kind {cli|library|service|ui|other}` — required when running
  against working-tree; optional when merged (read from `state.json.artifact`
  if present, else prompt).
- `--artifact-identifier <string>` — opaque handle the runner passes to the
  outsider subagent (CLI invocation hint, importable module name, URL, ...).
  Required for working-tree runs.
- `--non-interactive` — when set, edge-probe + adversarial use defaults;
  acceptance still runs (it carries the value-delivery check).
- `--no-adversarial` — skip the adversarial section; mark its Status as
  `skipped` with reason `"operator opted out"`.

## Steps

1. **Resolve feature.** Use `--feature <id>` when supplied; otherwise apply
   the single-active rule via `tools.state.find_active_feature`.
2. **Guard.**
   - Pre-PR mode: require `current_phase == "ship"` AND
     `phases.ship.status == "in_progress"`.
   - Post-merge mode: require `flow_version == 3` AND
     `phases.qa.status == "pending"` AND `state.json.shipped_at` is set.
   Abort with the matching failure-mode message when either guard fails.
3. **Build `ArtifactDescriptor`.** From `--artifact-kind` plus
   `--artifact-identifier`. When missing in interactive mode, prompt the
   user; in non-interactive mode, abort with a clear message. The descriptor
   is opaque — this skill never branches on `kind`.
4. **Acceptance section.** Dispatch a FRESH outsider subagent. Budget is
   tightly scoped to: `SPEC.md` (Intent + Acceptance Criteria + Scenarios
   sections), the `ArtifactDescriptor`, and ZERO implementation files. The
   subagent's task: act as a fresh user, exercise the artifact black-box,
   return one `PromiseCheck` per `SpecPromise`. The skill aggregates results
   via `tools.qa.acceptance.run_acceptance` with the subagent wired in as
   the injected `runner`.

   **Required prompt prefix.** The dispatch prompt MUST start with a top-level `context_budget:` block at column 0 (outside any fenced code block). The PreToolUse hook (`hooks/check_budget.py`) refuses dispatches that omit it. Canonical shape — copy verbatim and substitute the bracketed values:

   ```text
   context_budget:
   {
     "spec_sections": ["Intent", "Acceptance Criteria", "Scenarios"],
     "files_in_scope": [
       ".forge/features/<id>/SPEC.md"
     ],
     "forbidden": [
       "do not read any implementation file under tools/, hooks/, src/, or schemas/",
       "do not edit any file",
       "do not dispatch additional subagents"
     ],
     "artifact_descriptor": {
       "kind": "<cli | library | service | ui | other>",
       "identifier": "<opaque handle from --artifact-identifier>"
     },
     "return_format": {
       "promise_checks": "list[PromiseCheck per SpecPromise]",
       "max_words": 600
     }
   }

   [task prose follows here, starting with a blank line]
   ```

5. **Edge Probing section.** Dispatch a SECOND fresh subagent with the same
   budget shape. Task: enumerate normal-user mistakes (mistypes, empty
   input, oversized input, malformed input). Cap at 20 attempts. Each
   attempt becomes a finding row with pass/fail. Aggregate Status:
   `pass` when no failures, `partial` when mostly-pass with ≤2 fails,
   `fail` otherwise.

   **Required prompt prefix.** Reuse the Step 4 canonical shape; swap `return_format` to:

   ```text
   context_budget:
   {
     "spec_sections": ["Intent", "Acceptance Criteria", "Scenarios"],
     "files_in_scope": [
       ".forge/features/<id>/SPEC.md"
     ],
     "forbidden": [
       "do not read any implementation file under tools/, hooks/, src/, or schemas/",
       "do not edit any file",
       "do not dispatch additional subagents",
       "do not exceed 20 probe attempts"
     ],
     "artifact_descriptor": {
       "kind": "<cli | library | service | ui | other>",
       "identifier": "<opaque handle from --artifact-identifier>"
     },
     "return_format": {
       "probe_rows": "list[{attempt: str, status: enum[pass, fail], note: str}]",
       "max_attempts": 20,
       "max_words": 600
     }
   }

   [task prose follows here, starting with a blank line]
   ```

6. **Adversarial section.** Dispatch a THIRD fresh subagent (red-team).
   Call `tools.qa.adversarial.run_adversarial` with this subagent as the
   injected runner. Cap policy is the module default — 5-minute walltime,
   50 attempts. When `--no-adversarial` is set, skip the dispatch entirely
   and write the section with `Status: skipped` plus the opt-out reason.

   **Required prompt prefix.** Reuse the Step 4 canonical shape with the red-team `forbidden` + `return_format`:

   ```text
   context_budget:
   {
     "spec_sections": ["Intent", "Acceptance Criteria", "Scenarios", "Negative Requirements"],
     "files_in_scope": [
       ".forge/features/<id>/SPEC.md"
     ],
     "forbidden": [
       "do not read any implementation file under tools/, hooks/, src/, or schemas/",
       "do not edit any file",
       "do not dispatch additional subagents",
       "do not exceed the walltime or attempt cap"
     ],
     "artifact_descriptor": {
       "kind": "<cli | library | service | ui | other>",
       "identifier": "<opaque handle from --artifact-identifier>"
     },
     "caps": {
       "walltime_seconds": 300,
       "max_attempts": 50
     },
     "return_format": {
       "attack_rows": "list[{attempt: str, status: enum[blocked, escaped], note: str}]",
       "max_words": 600
     }
   }

   [task prose follows here, starting with a blank line]
   ```
7. **NR Regrep section.** Call `tools.qa.nr_regrep.run_nr_regrep` (pure
   Python; no subagent). Working-tree mode scans the tree at HEAD;
   merged mode scans the merged tree. Algorithm is identical — only the
   file-set the grepper sees differs.
8. **Aggregate verdict + confidence.** Apply the rule documented in the
   `templates/feature/QA.md` footer. Verdict mirrors Acceptance Status;
   confidence folds all four sections per the template's table.
9. **Write `QA.md`.** Copy the template skeleton; populate frontmatter
   (`feature_id`, `shipped_at` from state, `qa_at = now()`, `verdict`,
   `confidence`, `flow_version: 3`); fill the four sections from the
   aggregated results. Evidence pointers must be either filesystem paths
   under `repo_root` or stable identifiers (commit shas, URLs).
10. **Validate `QA.md` shape.** Run
    `python -m tools.validate --target qa_shape .forge/features/<id>`.
    Any `BLOCK` finding fails phase exit; surface findings to the operator
    with the option to edit `QA.md` and re-run validation.
11. **Phase transition.**
    - Pre-PR mode: do NOT call `start_phase("qa")` or `complete_phase`.
      The skill is auxiliary to ship in this mode. Return verdict +
      confidence to the caller (`forge-ship`); the caller decides whether
      to gate the PR.
    - Post-merge mode: run the forge-state Bash CLI (do NOT translate to a Python heredoc):

      ```bash
      forge-state start-phase --feature <id> --phase qa        # idempotent when in_progress
      # ... run sections ...
      forge-state complete-phase --feature <id> --phase qa
      forge-state finish         --feature <id>                # if not already terminal
      ```

      Then call `tools.archive.archive_feature_after_qa(repo_root, feature_id)` to perform the deferred folder move from `.forge/features/<id>/` to `.forge/features/archive/<id>/`. The helper is idempotent and version-guarded; a retry after partial failure is safe. Module fallback: `PYTHONPATH=$CLAUDE_PLUGIN_ROOT python3 -m tools.state_cli ...`.
12. **Surface to operator.** Print verdict, confidence, blocker count, and
    the path to `QA.md`.

## Failure modes

- **Acceptance subagent timeout / no response** → mark Acceptance Status
  `partial`; observation reads `"acceptance subagent did not respond
  within budget"`. The verdict aggregation makes this `partial`.
- **Working-tree run with uncommitted changes** → warn the operator and
  offer to abort. Continue only on explicit confirmation.
- **Pre-PR mode invoked outside the ship phase** → abort with
  `"forge-qa pre-PR mode requires forge-ship to be the active phase"`.
- **Post-merge mode invoked before ship completes** → abort with
  `"forge-qa post-merge mode requires shipped_at to be set; did
  /forge:ship complete?"`.
- **Missing `--artifact-identifier` in non-interactive mode** → abort with
  `"forge-qa requires --artifact-kind and --artifact-identifier when
  --non-interactive is set"`.

## State writes

- `QA.md` — created (or replaced) under `.forge/features/<id>/`.
- `current_phase` — transitions only in post-merge mode (`qa → done`).
- `state.json.phases.qa` — created/updated only in post-merge mode.
- `archive_feature_after_qa` runs at qa-done in post-merge mode; the
  feature folder moves to `.forge/features/archive/<id>/` once
  `phases.qa.status == "done"`.
- Pre-PR mode performs NO state mutation; ship-phase state is owned by
  `forge-ship`. Pre-PR mode does NOT trigger archive (it is a gate, not
  a phase transition).

## Out of scope

- No ecosystem-specific runners. The artifact descriptor stays opaque; the
  dispatched subagent figures out how to exercise the artifact from its
  `kind` + `identifier`.
- No real-time soak / memory monitoring.
- No automatic PR creation; that stays in `forge-ship`.

## See also

- `tools.qa.acceptance` — black-box value-delivery check.
- `tools.qa.adversarial` — capped red-team probe.
- `tools.qa.nr_regrep` — Negative-Requirement re-grep.
- `tools.validate.qa_shape` — `QA.md` structural validator.
- `templates/feature/QA.md` — artifact template + aggregation rule.
- `forge-ship` — invokes this skill in pre-PR mode when the operator
  accepts the QA prompt.
