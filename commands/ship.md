---
name: ship
description: Run the ship phase against the active feature — write or refuse to merge the canonical capability SPEC.md and archive the feature folder. Use after /forge:verify completes for standard or full tier. M2 supports first-ship only; subsequent changes use delta proposals (M3+).
---

# /forge:ship

Run the FORGE ship phase against the active feature.

## Args

- `--change <change_id>` — delta-merge mode. Routes the slash command to
  the merge-delta-proposal subroutine (see `skills/forge-ship/SKILL.md`).
  When set, skips the feature-ship lifecycle (no Constitution gate, no
  REVIEW.code.md parsing, no feature state advance). Calls
  `tools.archive.merge_delta_proposal` with
  `_mark_change_merged_hook(proposal_path)` as the
  `pre_archive_hook`. On success, prints the canonical path, archive
  path, and the two retained snapshots. On `ArchiveError`, surfaces the
  error and exits without retry.

## Behavior

1. Determine active feature.
2. Read `state.json`. Require `tier in ("standard", "full")` and `phases.verify.status == "done"`. Otherwise abort.
3. Read SPEC.md frontmatter to obtain the `capability` slug.
4. Call `tools.state.start_phase(path, "ship")`.
5. Invoke the `forge-ship` skill. The skill calls `tools.archive.ship_feature` — a single transactional helper that runs an all-or-nothing preflight (`.forge/features/<id>/` exists, `.forge/features/archive/<id>/` absent, `.forge/specs/<capability>/SPEC.md` absent), then writes the canonical spec, then archives the feature folder, rolling back the canonical write if the archive move fails. On any preflight collision, the helper raises `ArchiveError` ("capability already shipped — delta proposals (M3+) required for changes"); the skill logs to `decisions.md` § Open and halts.
6. On completion, print canonical spec path, archive path, capability slug, next step (none — feature done).

## Constitution gate (M3 §5.3.9)

When `.forge/CONSTITUTION.md` is present, `/forge:ship` parses `REVIEW.code.md` for findings tagged `[constitution:A<n>]` whose `Status` is `open` and partitions them by article level:

- **CRITICAL** + severity ≥ MEDIUM → gate prompt; user must resolve, log exception (Status: accepted-risk), or type `ACKNOWLEDGE` exactly.
- **SHOULD** + severity ≥ MEDIUM → printed once in ship summary; not gated.
- **MAY** or severity < MEDIUM → informational only.

On `ACKNOWLEDGE`, the feature ships with `state.json.deviations[]` appended (`phase: "ship"`, `resolution: "user_acknowledged"`) and a `decisions.md` entry. The ACK mutation runs INSIDE `tools.archive.ship_feature(pre_archive_hook=...)` so a preflight failure cannot leave a ghost deviation. Audit trail survives the archive.

Findings whose `Status` is `resolved` or `accepted-risk` are convergence-loop history and are NOT surfaced — the gate acts on unresolved findings only.

## Failure modes

- `tier == "focused"` → abort: "Focused tier finishes at /forge:verify; ship is standard / full only."
- `tools.archive.ship_feature` raises ArchiveError on preflight (capability already shipped, archive target exists, feature folder missing) → surface the error and instruct user about delta proposals (M3+). No state mutation occurred.
- `tools.archive.ship_feature` raises ArchiveError mid-operation (rare; archive move fails after canonical-spec write) → the helper rolls back the canonical-spec write before re-raising. Repo returns to pre-ship state; user can retry once the underlying issue is fixed.
- Working tree has uncommitted changes → warn, ask user to commit before ship (preserves ship as a clean atomic step).
