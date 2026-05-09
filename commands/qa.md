---
name: qa
description: Run the QA pass against a feature — fresh-outsider acceptance, edge probing, capped adversarial probe, and Negative-Requirement re-grep. Use after /forge:ship completes (post-merge terminal phase) or when /forge:ship invokes the pre-PR gate against the working tree. Produces QA.md.
---

# /forge:qa

Run the FORGE QA pass against a feature.

## Usage

```text
/forge:qa [--feature <id>] [--against {working-tree|merged}] \
          [--artifact-kind {cli|library|service|ui|other}] \
          [--artifact-identifier <string>] \
          [--non-interactive] [--no-adversarial] [--force]
```

## Args

- `--feature <id>` — required for `--against working-tree`; optional for
  `--against merged` (single-active rule applies).
- `--against {working-tree|merged}` — default `merged` for direct
  invocation, `working-tree` when `/forge:ship` invokes the command.
- `--artifact-kind {cli|library|service|ui|other}` — coarse hint to the
  outsider subagent. Required for working-tree runs; merged runs read it
  from `state.json.artifact` when present, otherwise prompt.
- `--artifact-identifier <string>` — opaque handle the runner forwards to
  the subagent (a CLI invocation hint, an importable module name, a URL,
  a container image reference). Required for working-tree runs.
- `--non-interactive` — skip operator prompts; edge-probe and adversarial
  use defaults. Acceptance still runs.
- `--no-adversarial` — skip the adversarial section; it is recorded as
  `Status: skipped` with reason `"operator opted out"`.
- `--force` — allow ad-hoc working-tree runs outside `/forge:ship`'s prompt
  path. Without it, working-tree mode is reserved for ship-driven calls.

## Behavior

1. Gather flags. Resolve the active feature via
   `tools.state.find_active_feature` when `--feature` is omitted.
2. Read `state.json`. Enforce timing precondition:
   - `--against merged` → require `flow_version == 3`,
     `phases.qa.status == "pending"`, and `state.json.shipped_at` set.
   - `--against working-tree` → require `current_phase == "ship"` with
     `phases.ship.status == "in_progress"`, OR `--force` for ad-hoc runs.
3. Resolve `ArtifactDescriptor` from `--artifact-kind` +
   `--artifact-identifier`. Prompt when missing in interactive mode; abort
   in non-interactive mode.
4. Invoke the `forge-qa` skill with the resolved descriptor and timing
   mode. The skill orchestrates four fresh-outsider dispatches
   (acceptance, edge probing, adversarial unless skipped, NR regrep),
   aggregates the verdict + confidence per the `templates/feature/QA.md`
   rule, writes `QA.md`, and runs `tools.validate --target qa_shape`.
5. On completion, surface verdict, confidence, blocker count, and the
   `QA.md` path.
   - Pre-PR mode returns the result to `/forge:ship`; ship decides whether
     to gate the PR. No archival occurs in this mode.
   - Post-merge mode advances state to `phases.qa.status == "done"` and
     `current_phase == "done"`, then calls
     `tools.archive.archive_feature_after_qa` to move the feature folder
     from `.forge/features/<id>/` to `.forge/features/archive/<id>/`. The
     helper is idempotent and version-guarded; safe to retry.

## Precondition

The feature must be shipped for `--against merged` (state carries
`shipped_at` and is at `flow_version 3`). For `--against working-tree`,
the command must be invoked from inside `/forge:ship`'s prompt path,
unless `--force` is supplied for an ad-hoc pre-PR run.

## Failure modes

- Pre-PR mode invoked outside the ship phase without `--force` → abort:
  `"forge-qa pre-PR mode requires forge-ship to be the active phase"`.
- Post-merge mode invoked before ship completes → abort:
  `"forge-qa post-merge mode requires shipped_at to be set; did
  /forge:ship complete?"`.
- Working-tree run with uncommitted changes → warn and ask the operator
  to confirm before proceeding.
- `tools.validate --target qa_shape` reports a `BLOCK` finding → phase
  exit fails; the operator edits `QA.md` and re-runs validation.
