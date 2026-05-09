---
name: do
description: Adaptive routing entry-point. Proposes a tier (focused/standard/full) for a free-text idea, seeds the feature folder + state.json + routing block, and dispatches tier-deterministically (focused/standard → /forge:spec; full → /forge:refine). Use when the user wants to start a new FORGE feature from a free-text idea without picking the tier upfront.
argument-hint: "<idea> [--focused | --standard | --full]"
model: sonnet
---

# /forge:do

Adaptive routing entry-point for FORGE features. Given a free-text idea,
the skill proposes a tier (focused, standard, or full) plus a phase
list, confirms with the user, seeds the feature folder under
`.forge/features/<feature_id>/`, writes the `routing` block on
`state.json`, and dispatches tier-deterministically to `/forge:spec`
(focused/standard) or `/forge:refine` (full).

## Args

- `<idea>` — required positional. Free-text feature description.
  Persisted verbatim into `state.json.routing.idea`.
- `--focused` / `--standard` / `--full` — optional tier override. The
  flag wins over the LLM's proposal.
  `Pass at most one of --focused / --standard / --full` — multi-flag
  input is rejected at parse time before any disk mutation.

## Behavior

1. Parses args and prints a one-line secrets warning before any disk
   mutation (`routing.idea` persists the idea text verbatim).
2. Constitution preflight (`.forge/CONSTITUTION.md` skip / bootstrap /
   cancel; default = skip).
3. Lightweight health preflight via `python -m tools.validate --target
   health`; surfaces BLOCK/HIGH findings and offers abort.
4. Capability scan; on collision routes to `/forge:change` or accepts a
   disambiguating slug suffix (proceed-as-new is NOT offered).
5. Pure LLM call proposes tier + phase list with one-sentence rationale.
6. Renders the proposal as a numbered checkbox list; user confirms with
   `y` or overrides via `--focused` / `--standard` / `--full`.
7. Seeds the feature folder via `tools.routing.seed_routed_feature`
   (composes `create_feature_folder` + `record_routing_decision` with
   schema validation).
8. Cleanup hook on `KeyboardInterrupt` and user-decline-after-seed
   removes the partial folder via
   `tools.archive.cleanup_seeded_feature`.
9. Self-reviews the seed shape (routing block, `current_phase ∈
   {"spec", "refine"}` matching the seeded tier,
   `phases.<current_phase>.status == "in_progress"`, research deferral
   entry, exactly three files in the folder).
10. Prints the tier-deterministic dispatch literal: focused/standard →
    `Next: /forge:spec --feature <feature_id>`; full →
    `Next: /forge:refine --feature <feature_id>`.

## Dispatch literal

The skill prints exactly one of two lines on success, resolved by
`state.json.current_phase` after the seed:

- focused / standard → `Next: /forge:spec --feature <feature_id>`
- full → `Next: /forge:refine --feature <feature_id>`

The `--feature <id>` form is REQUIRED on both branches — a bare
`/forge:spec` (or `/forge:refine`) would re-run new-feature creation
against the pre-seeded folder. The `forge-spec` pre-seed branch detects
the seeded `routing` block and skips its own capability scan + folder
creation steps; the `forge-refine` pre-seed branch (T4) does the same
for full-tier seeds.

## See also

- `skills/forge-do/SKILL.md` — full lifecycle (11 steps).
- `tools.routing.seed_routed_feature` — Python entry-point for the
  post-confirm half.
- `/forge:spec --feature <feature_id>` — next phase for focused/standard.
- `/forge:refine --feature <feature_id>` — next phase for full.
- `/forge:change` — capability-collision delta route.
