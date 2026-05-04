---
name: ship
description: Run the ship phase against the active feature — write or refuse to merge the canonical capability SPEC.md and archive the feature folder. Use after /idd:verify completes for standard or full tier. M2 supports first-ship only; subsequent changes use delta proposals (M3+).
---

# /idd:ship

Run the IDD ship phase against the active feature.

## Behavior

1. Determine active feature.
2. Read `state.json`. Require `tier in ("standard", "full")` and `phases.verify.status == "done"`. Otherwise abort.
3. Read SPEC.md frontmatter to obtain the `capability` slug.
4. Call `tools.state.start_phase(path, "ship")`.
5. Invoke the `idd-ship` skill. The skill calls `tools.archive.ship_feature` — a single transactional helper that runs an all-or-nothing preflight (`.idd/features/<id>/` exists, `.idd/features/archive/<id>/` absent, `.idd/specs/<capability>/SPEC.md` absent), then writes the canonical spec, then archives the feature folder, rolling back the canonical write if the archive move fails. On any preflight collision, the helper raises `ArchiveError` ("capability already shipped — delta proposals (M3+) required for changes"); the skill logs to `decisions.md` § Open and halts.
6. On completion, print canonical spec path, archive path, capability slug, next step (none — feature done).

## Failure modes

- `tier == "focused"` → abort: "Focused tier finishes at /idd:verify; ship is standard / full only."
- `tools.archive.ship_feature` raises ArchiveError on preflight (capability already shipped, archive target exists, feature folder missing) → surface the error and instruct user about delta proposals (M3+). No state mutation occurred.
- `tools.archive.ship_feature` raises ArchiveError mid-operation (rare; archive move fails after canonical-spec write) → the helper rolls back the canonical-spec write before re-raising. Repo returns to pre-ship state; user can retry once the underlying issue is fixed.
- Working tree has uncommitted changes → warn, ask user to commit before ship (preserves ship as a clean atomic step).
