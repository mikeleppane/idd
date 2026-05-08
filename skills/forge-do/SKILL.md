---
name: forge-do
description: Adaptive routing entry-point for FORGE features. Use when the user invokes /forge:do "<idea>" — proposes a tier (focused/standard/full) for the idea, seeds the feature folder + state.json + routing block, and dispatches tier-deterministically to /forge:spec (focused/standard) or /forge:refine (full).
model: sonnet
disable-model-invocation: true
---

# FORGE Do — adaptive routing

## When this skill applies

The user invoked `/forge:do "<idea>" [--focused | --standard | --full]`.
All three tiers route end-to-end. Focused/standard seed
`current_phase="spec"` and dispatch to `/forge:spec`; full seeds
`current_phase="refine"` and dispatches to `/forge:refine`. The skill
resolves the dispatch literal from `state.json.current_phase` after the
seed completes.

## Inputs

- `<idea>` — required positional. Free-text feature idea. Persisted
  verbatim into `state.json.routing.idea` by the routing helper.
- `--focused` / `--standard` / `--full` — optional tier override. Wins
  over the LLM proposal when supplied.

## Steps

1. **Parse args + emit secrets warning.** Read the positional `<idea>`
   and any tier flag. Before anything is written to disk, print the
   one-line warning:

   `sensitive content (tokens, passwords) discouraged — text is persisted to state.json.routing.idea verbatim`

   The `--focused`, `--standard`, and `--full` flags all seed normally
   via `tools.routing.seed_routed_feature`. The flag (when supplied)
   wins over the LLM proposal at step 7.
2. **Constitution preflight (per spec §5.3.1 + D-10).** If
   `.forge/CONSTITUTION.md` is absent, present the user with three
   choices: skip, bootstrap, cancel. The default = skip so a brand-new
   repo is not forced into Constitution authoring on every `/forge:do`
   invocation. On `bootstrap`, call
   `tools.constitution_amend.bootstrap_constitution(repo_root)` and
   continue. On `cancel`, abort without seeding. Record
   `constitution_present: bool` for the routing block (`True` iff the
   file exists or was just bootstrapped).
3. **Health preflight (per spec §5.3.2 + D-HEALTH).** Run the
   lightweight subset of `python -m tools.validate --target health`.
   Surface any `BLOCK` or `HIGH` findings (orphan folders, capability
   collisions, schema drift) and offer the user the option to abort.
   Default behavior on findings is to halt and ask the user. The
   `--force` flag that bypasses `WARN`-level findings is documented as
   P6.2 territory — not implemented in P6.1.
4. **Capability scan (per spec §5.3.5 + P5 lock).** Call
   `tools.archive.scan_existing_capabilities(repo_root)`. Compute a slug
   via `tools.archive.slug_from_idea(idea)`. If the slug clashes with an
   existing capability, mirror the `forge-spec` capability-scan contract
   exactly: prompt the user with

   `route to /forge:change for delta proposal, or supply a disambiguating slug suffix (<slug>-v2, <slug>-bulk)?`

   The skill never offers a no-suffix new-folder escape hatch (mirrors
   the forge-spec contract — suffix-disambig is the only alternative to
   the change route).
   On `/forge:change` choice, exit and dispatch
   `/forge:change --capability <slug> "<delta_description>"`. On a
   suffix, the user types the FULL disambiguated slug (e.g.
   `add-oauth-login-flow-v2`); validate it locally against
   `^[a-z0-9][a-z0-9-]{2,}$`, re-run the scan with the new slug, and
   abort on persistent collision. Carry the chosen slug into step 8 as
   the `feature_slug=` argument to `tools.routing.seed_routed_feature`
   so the operator's suffix flows into `feature_id` while `idea`
   continues to carry the user's original phrasing verbatim. NEVER edit
   `idea` to bake the suffix in — that would corrupt the
   `state.json.routing.idea` audit record.
5. **LLM tier proposal.** Issue a pure LLM call with project signals
   (top-level dir tree plus `pyproject.toml` / `package.json` /
   `Cargo.toml` if present) and the relevance-filtered Constitution
   article list (per P3). Prompt verbatim:

   `Given idea + project signals + Constitution (if any), propose tier (focused/standard/full) + phase list. One-sentence rationale.`

   All three tiers are valid proposal targets in P6.2; the override flag
   (when supplied) still wins per step 7.
6. **Print proposal as numbered checkbox list.** Render the LLM's
   tier + phase list as a numbered checkbox list in the terminal. Ask
   the user to confirm with `y` or override by re-invoking with
   `--focused` / `--standard`. P6.1 does not yet expose a `--phases`
   freeform override; tier flag is the only override.
7. **Resolve final tier.** The override flag wins over the LLM
   proposal. The resolved tier is one of `focused` / `standard` / `full`
   and is passed verbatim to `seed_routed_feature` as `final_tier=`.
8. **Seed via routing helper.** Call
   `tools.routing.seed_routed_feature(repo_root, idea=<idea>, final_tier=<tier>, proposed_tier=<llm_tier>, rationale=<one_sentence>, constitution_present=<bool>, feature_slug=<chosen_slug_or_None>)`.
   When step 4 took the suffix-disambig branch, pass the chosen
   disambiguated slug as `feature_slug=` — the helper uses it verbatim
   for `feature_id` while `idea` is persisted into `routing.idea`
   unchanged, so the user's original phrasing remains in the audit
   record. When step 4 found no collision, omit `feature_slug` (or
   pass `None`) and the helper derives the slug via
   `tools.archive.slug_from_idea(idea)` as before.
   The helper composes `tools.archive.create_feature_folder` and
   `tools.state.record_routing_decision` (both with `schema_path` set to
   `<repo_root>/schemas/state.schema.json` so an invalid payload refuses
   before disk mutation). On any post-seed exception inside the helper,
   it invokes `tools.archive.cleanup_seeded_feature(repo_root, feature_id)`
   before re-raising. `ArchiveError`, `StateError`, and `ValueError`
   surface to the user with the seeded folder already cleaned up by the
   helper.
9. **Cleanup hook (UI cancel paths).** Wrap step 8 in a try/finally
   that calls `tools.archive.cleanup_seeded_feature(repo_root, feature_id)`
   on `KeyboardInterrupt` and on user-decline-after-seed. Best-effort
   per spec D-2a: log on cleanup failure, do not re-raise. Division of
   labour: the helper handles post-seed-failure cleanup; this skill
   handles post-seed-cancel cleanup. Both routes converge on the same
   `_orphan_conditions_met` predicate generalized in T0.5.

   **Caveat — best-effort cleanup, decisions.md edits NOT preserved.**
   The cleanup predicate is filename-based, not content-based: it removes
   the folder when `commits == []` AND folder contents are a strict
   subset of `{state.json, SPEC.md, decisions.md}`. User edits to
   `decisions.md` made between seed and cancel are silently lost on
   cleanup because the file is still present (its filename passes the
   subset check). If the user has logged a decision worth keeping, they
   must commit it before cancelling — otherwise the cleanup hook will
   delete the folder along with the unsaved decisions.
10. **Self-review.** Verify the seed shape before dispatch:
    - `state.json` validates against `schemas/state.schema.json`.
    - `routing` block present with `idea`, `final_tier`, `decided_at`,
      and `constitution_present` populated.
    - `current_phase ∈ {"spec", "refine"}` matching the seeded tier
      (focused/standard → `spec`; full → `refine`) AND the corresponding
      `phases.<current_phase>.status == "in_progress"`.
    - `skipped` contains the `research` deferral entry
      (`{"phase": "research", "reason": "M3 deferred — manual research acceptable"}`)
      on every tier — research stays skipped on all three.
    - The feature folder contains exactly three files: `state.json`,
      `SPEC.md`, and `decisions.md`.
11. **Dispatch to first phase.** Resolve the dispatch literal by
    `state.json.current_phase` after the seed:
    `spec` → `/forge:spec --feature <feature_id>`;
    `refine` → `/forge:refine --feature <feature_id>`. Print exactly one
    of:

    `Next: /forge:spec --feature <feature_id>`

    `Next: /forge:refine --feature <feature_id>`

    The `--feature <id>` form is REQUIRED on both branches. A bare
    `/forge:spec` (or `/forge:refine`) would re-run the new-feature
    creation path against the pre-seeded folder and clash on the
    collision check. The forge-spec pre-seed branch detects the seeded
    `routing` block and skips steps 1–4 of its own lifecycle; the
    forge-refine pre-seed branch (T4) does the same for full-tier
    seeds.

## Failure modes

- **Constitution bootstrap declined / cancel chosen.** Skill aborts
  without seeding. No state mutation.
- **Health preflight surfaces findings.** Default is to halt and ask
  the user. If the user opts to abort, no folder is created. `--force`
  is a P6.2 affordance.
- **Capability collision with no suffix offered.** Skill exits and
  dispatches `/forge:change` per the disambig contract. No folder is
  created.
- **`KeyboardInterrupt` after seed but before user accepts dispatch.**
  The cleanup hook in step 9 invokes
  `tools.archive.cleanup_seeded_feature` to remove the partial folder.
  Best-effort: a cleanup failure is logged, not re-raised.
- **`record_routing_decision` raises mid-helper.** The routing helper
  cleans up the seeded folder before re-raising. The skill surfaces the
  underlying `StateError` to the user.

## State writes

- `.forge/features/<feature_id>/state.json` — created by
  `tools.archive.create_feature_folder` via the routing helper. Seeds
  `feature_id`, `tier`, `current_phase` (= `spec` for focused/standard,
  `refine` for full), `phases.<current_phase>.status: "in_progress"`,
  `phases.<current_phase>.started_at: <utc>`, and the research deferral
  entry in `skipped[]`.
- `state.json.routing` — written by `tools.state.record_routing_decision`
  (called inside the routing helper). Contains `idea`, `final_tier`,
  `proposed_tier`, `rationale`, `constitution_present`, `decided_at`.
- `.forge/features/<feature_id>/SPEC.md` and `decisions.md` — copied
  unmodified from `templates/feature/`. The forge-spec pre-seed branch
  populates `SPEC.md`; `decisions.md` stays empty until the first
  decision is logged.

## See also

- `tools.routing.seed_routed_feature` — single Python entry-point for the
  post-confirm half of `/forge:do`.
- `tools.archive.create_feature_folder` — composed by the routing helper
  to seed the feature folder.
- `tools.archive.cleanup_seeded_feature` — invoked on UI-cancel paths
  and post-seed-failure paths via the shared orphan predicate.
- `tools.state.record_routing_decision` — sole writer of the routing
  block (P1 contract).
- `commands/do.md` — slash spec.
- `/forge:spec --feature <feature_id>` — next phase for focused/standard
  tiers; pre-seed branch consumes the seeded routing block and skips
  its own steps 1–4.
- `/forge:refine --feature <feature_id>` — next phase for full tier;
  pre-seed branch (T4) consumes the seeded routing block and enters the
  Socratic loop directly.
- `/forge:change` — capability-collision delta route.
