---
name: forge-do
description: Adaptive routing entry-point for FORGE features. Use when the user invokes /forge:do "<idea>" — proposes a tier (focused/standard/full) for the idea, seeds the feature folder + state.json + routing block, and dispatches tier-deterministically to /forge:spec (focused/standard), /forge:refine (full), or /forge:research (standard with --research).
model: sonnet
disable-model-invocation: true
---

# FORGE Do — adaptive routing

## When this skill applies

The user invoked
`/forge:do "<idea>" [--focused | --standard | --full] [--research]`.
All three tiers route end-to-end. Focused seeds `current_phase="spec"`
and dispatches to `/forge:spec`. Standard without `--research` seeds
`current_phase="spec"` and dispatches to `/forge:spec`. Standard with
`--research` seeds `current_phase="research"` and dispatches to
`/forge:research`. Full seeds `current_phase="refine"` and dispatches to
`/forge:refine`. The skill resolves the dispatch literal from
`state.json.current_phase` after the seed completes.

## Inputs

- `<idea>` — required positional. Free-text feature idea. Persisted
  verbatim into `state.json.routing.idea` by the routing helper.
- `--focused` / `--standard` / `--full` — optional tier override. Wins
  over the LLM proposal when supplied.
- `--research` — optional flag. Opts the feature into the research
  phase. Standard tier seeds `current_phase="research"` and writes
  `routing.phase_list` with `research` at index 0. Full tier always
  runs research, so the flag is a no-op there. Focused tier refuses
  the flag with the locked escalate hint
  `research escalates to standard tier; use /forge:do --standard --research "<idea>"`.

## Steps

1. **Parse args + emit secrets warning.** Read the positional `<idea>`,
   any tier flag, and the optional `--research` flag. Before anything
   is written to disk, print the one-line warning:

   `sensitive content (tokens, passwords) discouraged — text is persisted to state.json.routing.idea verbatim`

   The `--focused`, `--standard`, and `--full` flags all seed normally
   via `tools.routing.seed_routed_feature`. The flag (when supplied)
   wins over the LLM proposal at step 7. **Multi-flag input is
   rejected at parse time:** if the user passes more than one of
   `--focused` / `--standard` / `--full` simultaneously (e.g.
   `/forge:do "<idea>" --focused --standard`), abort with the literal
   message `Pass at most one of --focused / --standard / --full; got <list>`
   where `<list>` enumerates the supplied flags. No disk mutation
   occurs; the user re-invokes with a single flag.

   **Focused + `--research` refusal.** If the user passes `--focused`
   together with `--research`, abort BEFORE any disk mutation with the
   literal message
   `research escalates to standard tier; use /forge:do --standard --research "<idea>"`.
   The routing helper mirrors this refusal
   (`tools.routing.seed_routed_feature` raises `ValueError` with the
   same wording); skill-side abort surfaces the same hint without
   going through the helper. The user re-invokes with the standard
   tier or drops the flag.

   **Idea length cap:** abort with the literal cap if
   `idea > 4000 chars`. The routing helper mirrors this check
   (`tools.routing.seed_routed_feature` raises `ValueError` with
   `"idea exceeds 4000-char cap (got <n> chars); trim before /forge:do"`);
   skill-side abort surfaces the same wording without going through
   schema validation.
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
   Default behavior on findings is to halt and ask the user. A
   `--force` flag that bypasses `WARN`-level findings is not exposed
   today; halt-and-ask is the only path.
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
   article list. Prompt verbatim:

   `Given idea + project signals + Constitution (if any), propose tier (focused/standard/full) + phase list. One-sentence rationale.`

   All three tiers are valid proposal targets; the override flag (when
   supplied) still wins per step 7.
6. **Print proposal as numbered checkbox list.** Render the LLM's
   tier + phase list as a numbered checkbox list in the terminal. Ask
   the user to confirm with `y` or override by re-invoking with
   `--focused` / `--standard` / `--full`. Tier flag is the only
   override; no `--phases` freeform override exists today.
7. **Resolve final tier.** The override flag wins over the LLM
   proposal. The resolved tier is one of `focused` / `standard` / `full`
   and is passed verbatim to `seed_routed_feature` as `final_tier=`.
8. **Seed via routing helper.** Call
   `tools.routing.seed_routed_feature(repo_root, idea=<idea>, final_tier=<tier>, proposed_tier=<llm_tier>, rationale=<one_sentence>, constitution_present=<bool>, feature_slug=<chosen_slug_or_None>, research_opt_in=<bool>)`.
   When step 4 took the suffix-disambig branch, pass the chosen
   disambiguated slug as `feature_slug=` — the helper uses it verbatim
   for `feature_id` while `idea` is persisted into `routing.idea`
   unchanged, so the user's original phrasing remains in the audit
   record. When step 4 found no collision, omit `feature_slug` (or
   pass `None`) and the helper derives the slug via
   `tools.archive.slug_from_idea(idea)` as before. Pass
   `research_opt_in=True` when the user supplied `--research` (and the
   resolved tier is standard); otherwise pass `False`. The helper
   refuses focused+`research_opt_in` and raises before any disk write,
   but the skill-side abort in step 1 already prevents reaching the
   helper with that combination.
   The helper composes `tools.archive.create_feature_folder` and
   `tools.state.record_routing_decision` (both with `schema_path` set to
   `<repo_root>/schemas/state.schema.json` so an invalid payload refuses
   before disk mutation). On full tier the helper writes the 11-entry
   `routing.phase_list`; on
   standard with `--research` the helper writes the 9-entry list with
   `research` at index 0; on focused or standard-without-flag the
   helper omits the field and consumers fall back to the per-tier
   static table via `tools.state.get_phase_list`'s lazy-derive branch.
   On any post-seed exception inside the helper, it invokes
   `tools.archive.cleanup_seeded_feature(repo_root, feature_id)` before
   re-raising. `ArchiveError`, `StateError`, and `ValueError` surface
   to the user with the seeded folder already cleaned up by the
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
    - `current_phase ∈ {"spec", "refine", "research"}` matching the
      seeded tier (focused → `spec`; standard without `--research` →
      `spec`; standard with `--research` → `research`; full →
      `refine`) AND the corresponding
      `phases.<current_phase>.status == "in_progress"`.
    - `skipped` is empty when the effective phase list contains
      `research` (full tier, standard with `--research`); otherwise
      `skipped` carries the legacy deferral entry
      `{"phase": "research", "reason": "M3 deferred — manual research acceptable"}`
      so health-validate recognises research as intentionally skipped
      on the focused / standard-without-flag paths.
    - `routing.phase_list` is present and unique-valued on full tier
      and on standard with `--research`; absent on focused and
      standard-without-flag (consumers lazy-derive via
      `tools.state.get_phase_list`).
    - The feature folder contains exactly three files: `state.json`,
      `SPEC.md`, and `decisions.md`.
11. **Dispatch to first phase.** Resolve the dispatch literal by
    `state.json.current_phase` after the seed:
    `spec` → `/forge:spec --feature <feature_id>`;
    `refine` → `/forge:refine --feature <feature_id>`;
    `research` → `/forge:research --feature <feature_id>`. Print
    exactly one of:

    `Next: /forge:spec --feature <feature_id>`

    `Next: /forge:refine --feature <feature_id>`

    `Next: /forge:research --feature <feature_id>`

    The `--feature <id>` form is REQUIRED on every branch. A bare
    `/forge:spec` (or `/forge:refine` / `/forge:research`) would
    re-run the new-feature creation path against the pre-seeded
    folder and clash on the collision check. The forge-spec pre-seed
    branch detects the seeded `routing` block and skips steps 1–4 of
    its own lifecycle; the forge-refine pre-seed branch does the same
    for full-tier seeds; the forge-research entry consumes the seeded
    `routing` block when `current_phase="research"`.

## Failure modes

- **Constitution bootstrap declined / cancel chosen.** Skill aborts
  without seeding. No state mutation.
- **Health preflight surfaces findings.** Default is to halt and ask
  the user. If the user opts to abort, no folder is created. `--force`
  is not exposed today.
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
  `feature_id`, `tier`, `current_phase` (= `spec` for focused / standard
  without `--research`, `research` for standard with `--research`,
  `refine` for full), `phases.<current_phase>.status: "in_progress"`,
  `phases.<current_phase>.started_at: <utc>`. The research deferral
  entry in `skipped[]` is seeded only on tiers whose effective phase
  list does NOT include `research` (focused, standard without
  `--research`); on full and standard-with-research the marker is
  suppressed so it cannot contradict the live lifecycle.
- `state.json.routing` — written by `tools.state.record_routing_decision`
  (called inside the routing helper). Contains `idea`, `final_tier`,
  `proposed_tier`, `rationale`, `constitution_present`, `decided_at`,
  and (for full and standard-with-`--research`) `phase_list` carrying
  the explicit lifecycle. Focused and standard-without-flag omit
  `phase_list`; consumers reach for the per-tier static table via
  `tools.state.get_phase_list`'s lazy-derive branch.
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
- `/forge:spec --feature <feature_id>` — next phase for focused and
  standard-without-`--research`; pre-seed branch consumes the seeded
  routing block and skips its own steps 1–4.
- `/forge:refine --feature <feature_id>` — next phase for full tier;
  pre-seed branch consumes the seeded routing block and enters the
  Socratic loop directly.
- `/forge:research --feature <feature_id>` — next phase for standard
  with `--research`; consumes the seeded routing block + the
  `routing.phase_list` carrying `research` at index 0.
- `/forge:change` — capability-collision delta route.
