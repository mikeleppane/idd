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
- `--promote-domain` — full-tier only. After ship completes, merge this
  feature's `DOMAIN.md` glossary into the repo-wide
  `.forge/domain/glossary.md`. Promotion is post-ship advisory:
  conflicts (duplicate term with diverging definition) are surfaced in
  the ship summary as a non-blocking advisory and never block ship. The
  on-disk glossary is left untouched on conflict; reconcile manually
  before promoting the next feature.
- `--no-qa-prompt` — skip the pre-PR QA prompt. Treat as if the user
  accepted the tier-aware default (`Y` for `standard` / `full`, `N` for
  `focused`).
- `--qa-override-with-rationale "<text>"` — bypass the pre-PR QA gate
  entirely. Appends a `## QA Override` ADR to
  `.forge/features/<id>/decisions.md` with the supplied rationale,
  reviewer, and date. Use only when there is a justified reason to ship
  without QA.
- `--artifact-kind {cli|library|service|ui|other}` — required when the
  operator accepts the QA prompt. Identifies the kind of artifact the QA
  agent will exercise. Forwarded verbatim to `forge-qa`; see
  `/forge:qa` for details.
- `--artifact-identifier <string>` — required when the operator accepts
  the QA prompt. Opaque string the QA agent passes to its outsider
  subagent (e.g., a CLI invocation hint, a module name, a URL).

## Behavior

1. Determine active feature.
2. Read `state.json`. Require `tier in ("standard", "full")` and `phases.verify.status == "done"`. Otherwise abort.
3. Read SPEC.md frontmatter to obtain the `capability` slug.
4. Call `tools.state.start_phase(path, "ship")`.
5. Invoke the `forge-ship` skill. The skill calls `tools.archive.ship_feature` — a single transactional helper that runs an all-or-nothing preflight (`.forge/features/<id>/` exists, `.forge/specs/<capability>/SPEC.md` absent, and for legacy v1 features `.forge/features/archive/<id>/` absent), then writes the canonical spec. For v3 features (default for new features) the feature folder remains at `.forge/features/<id>/` after ship; archival is deferred to `/forge:qa --against merged`. For v1 features the helper additionally moves the feature folder to `.forge/features/archive/<id>/` at ship, rolling back the canonical write if the archive move fails. On any preflight collision, the helper raises `ArchiveError`; the skill logs to `decisions.md` § Open and halts.
6. On completion, print canonical spec path, archive (or live) feature path, capability slug, next step (`/forge:qa --against merged` for v3; none for v1).

## Constitution gate (M3 §5.3.9)

When `.forge/CONSTITUTION.md` is present, `/forge:ship` parses `REVIEW.code.md` for findings tagged `[constitution:A<n>]` whose `Status` is `open` and partitions them by article level:

- **CRITICAL** + severity ≥ MEDIUM → gate prompt; user must resolve, log exception (Status: accepted-risk), or type `ACKNOWLEDGE` exactly.
- **SHOULD** + severity ≥ MEDIUM → printed once in ship summary; not gated.
- **MAY** or severity < MEDIUM → informational only.

On `ACKNOWLEDGE`, the feature ships with `state.json.deviations[]` appended (`phase: "ship"`, `resolution: "user_acknowledged"`) and a `decisions.md` entry. The ACK mutation runs INSIDE `tools.archive.ship_feature(pre_archive_hook=...)` so a preflight failure cannot leave a ghost deviation. Audit trail survives the archive.

Findings whose `Status` is `resolved` or `accepted-risk` are convergence-loop history and are NOT surfaced — the gate acts on unresolved findings only.

## Pre-PR QA gate

Before the atomic ship / archive step, `/forge:ship` offers an optional pre-PR QA pass. The prompt — `"Run QA before creating PR? [Y/n]"` — defaults to `Y` for `standard` and `full` tiers and to `N` for `focused`. `--no-qa-prompt` skips the prompt and applies the tier-aware default; `--qa-override-with-rationale "<text>"` suppresses both the prompt and the gate, recording a `## QA Override` ADR in `decisions.md`. On accept, ship dispatches `forge-qa` against the working tree (`forge-qa --against working-tree --feature <id> --artifact-kind <k> --artifact-identifier <i>`); the skill writes `.forge/features/<id>/QA.md` and returns a verdict and confidence. Three outcomes:

- `delivers` — ship continues unchanged.
- `partial` — operator is prompted (`"QA verdict is partial. Continue ship? [y/N]"`); declining aborts ship without state mutation, accepting appends a `## QA Override` ADR to `decisions.md` and continues.
- `does-not-deliver` — ship aborts with the path to `QA.md` and instructions to fix findings or re-run with `--qa-override-with-rationale`. The `QA.md` is preserved on disk for review.

The pre-PR gate does NOT mutate `state.json`. The post-ship `qa` phase remains `pending`; flipping it to `done` is reserved for `/forge:qa --against merged`.

## Failure modes

- `tier == "focused"` → abort: "Focused tier finishes at /forge:verify; ship is standard / full only."
- `tools.archive.ship_feature` raises ArchiveError on preflight (capability already shipped, archive target exists, feature folder missing) → surface the error and instruct user about delta proposals (M3+). No state mutation occurred.
- `tools.archive.ship_feature` raises ArchiveError mid-operation (rare; archive move fails after canonical-spec write) → the helper rolls back the canonical-spec write before re-raising. Repo returns to pre-ship state; user can retry once the underlying issue is fixed.
- Working tree has uncommitted changes → warn, ask user to commit before ship (preserves ship as a clean atomic step).
