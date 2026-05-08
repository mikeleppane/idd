---
name: forge-refine
description: Socratic vague-idea collapse before /forge:spec. Use when /forge:do (full tier) routes to refine, or when the user invokes /forge:refine directly.
model: sonnet
disable-model-invocation: true
---

# FORGE Refine

## When this skill applies

`/forge:do` (full tier) seeded routing and advanced state to
`current_phase == "refine"`, OR the user invoked `/forge:refine [--feature <id>]`
directly against an active feature whose `current_phase` is already `refine`.

## Inputs

- `--feature <id>` — optional. Single-active rule applies: when omitted, resolve
  the only feature whose `state.json.current_phase != "done"`; abort if zero
  or multiple match.
- `[<idea>]` — optional positional. CLI fallback for the idea text when
  `state.json.routing.idea` is absent. Until `/forge:do --full` ships
  (deferred to M3 P6.2), this is the documented direct-invocation path:
  `/forge:refine [--feature <id>] "<idea text>"`.
- Idea source precedence: when `state.json.routing.idea` is present (seeded
  by `/forge:do --full`), use it and **ignore** any CLI `<idea>` (do not
  overwrite — routing is the canonical record). When absent AND CLI `<idea>`
  is given, seed the routing block first via
  `tools.state.record_routing_decision(path, idea=<idea>, final_tier="full",
  proposed_tier="full", rationale="direct /forge:refine invocation")` so
  `routing.refine_attempts` has a parent block to live under. When **both**
  are absent, abort with: `"/forge:refine needs an idea — pass one as an
  argument: /forge:refine \"<idea text>\""`.
- Bootstrap caveat (until M3 P6.2 ships `/forge:do --full`): the feature
  folder + `state.json` at `current_phase == "refine"` must already exist.
  `/forge:refine` does NOT create the feature folder — that's `/forge:spec`'s
  job. To bootstrap manually, ask the user to first create the folder via
  the `/forge:spec` template path then flip `current_phase` to `refine`, or
  wait for P6.2.

## Steps

1. **Resolve feature.** Read `--feature <id>` or apply the single-active rule.
   Locate `.forge/features/<id>/state.json`.
2. **Guard phase.** Read state.json; require `current_phase == "refine"`.
   Otherwise abort with the `StateError` raised by
   `tools.state.increment_refine_attempts` itself — message format:
   `"cannot increment refine_attempts: current_phase is '<X>', expected 'refine'"`.
   Do not invent a custom abort string; the helper is the single source of
   truth so users see consistent errors across phases.
3. **Guard tier.** After the phase guard, also require `state.json.tier == "full"`.
   The canonical helper `tools.state.increment_refine_attempts` calls
   `tools.state.require_full_tier(payload, phase="refine")` and surfaces
   the verbatim raise:
   `"refine phase is full-tier only; current tier is '<X>'"`. Do not invent
   a custom abort string here either; quote the helper. Refine is full-tier
   only — focused and standard tiers do not enter this phase.
4. **Resolve idea source.** Pull `state.json.routing.idea`. If present, use it
   and ignore any CLI `<idea>` argument (routing is the canonical record;
   do not overwrite). If `routing.idea` is absent AND a CLI `<idea>` was
   passed, seed the routing block first via
   `tools.state.record_routing_decision(path, idea=<idea>, final_tier="full",
   proposed_tier="full", rationale="direct /forge:refine invocation")` —
   this satisfies the schema requirement that `routing` carry `idea`,
   `final_tier`, and `decided_at`, and gives `increment_refine_attempts` a
   block to mutate. If **both** are absent, abort with:
   `"/forge:refine needs an idea — pass one as an argument: /forge:refine \"<idea text>\""`.
5. **Detect ambiguity.** Scan the idea for vague verbs ("improve", "better",
   "fix"), compound goals (multiple "and"-joined outcomes), and missing
   acceptance signal (no measurable change implied).
6. **Socratic loop, max 5 rounds.** While ambiguity remains and round count is
   below 5:
   a. Ask one clarifying question targeting the highest-impact ambiguity.
   b. After the user replies, call `tools.state.increment_refine_attempts(path)`
      to bump `routing.refine_attempts` by 1. The helper enforces the
      5-round cap machine-side: a sixth call raises
      `"refine_attempts already at cap (5); record_refined_idea +
      complete_phase or surface a deviation"`. The schema mirrors the cap
      via `routing.refine_attempts.maximum = 5` so a tampered state.json
      is rejected on read/write too.
   c. Synthesize a candidate refined-idea paragraph and ask the user to
      confirm or continue.
7. **Persist refined idea.** On user confirm OR round-cap reached, call
   `tools.state.record_refined_idea(path, refined=...)` to write
   `state.json.refined_idea`. The refined idea must be ≤4000 chars; the
   helper raises `StateError` on overflow rather than truncating silently —
   trim before calling `record_refined_idea`. The schema mirrors the cap
   via `refined_idea.maxLength = 4000`.
8. **Round-cap deviation.** When 5 rounds exhausted without convergence:
   - **Interactive mode:** halt; prompt the user to re-state the idea.
   - **Auto mode:** append a `decisions.md` entry **and** append a structured
     object to `state.json.deviations` matching the schema shape
     `{phase, cause, resolution}` (per `schemas/state.schema.json` —
     `deviations[]` items are objects, never bare strings). Use:
     `{"phase": "refine", "cause": "round cap reached", "resolution": "proceeding with best-effort refinement"}`.
     Then proceed with the best-effort paragraph.
9. **Self-review.** Confirm:
   - Single feature scope (no compound goals).
   - Measurable outcome implied.
   - No multi-feature spillover. If the user cannot collapse to one feature,
     suggest re-running `/forge:do --full` and abort.
10. **Phase transition.** Call `tools.state.complete_phase(path, "refine")` then
    `tools.state.start_phase(path, "spec")`. Print `Next: /forge:spec`.

## Failure modes

- **Multi-feature idea.** Self-review detects two or more independent features
  inside the refined paragraph. Suggest re-running `/forge:do --full` per
  feature and abort without transitioning phase.
- **5 rounds exhausted, no convergence.** In interactive mode, halt and let
  the user re-state. In auto mode, log a deviation (`decisions.md` entry +
  `state.json.deviations` append — object shape `{phase, cause, resolution}`)
  and proceed with the best-effort refinement.
- **`routing` block absent.** `tools.state.increment_refine_attempts` raises
  `StateError`. Surface the message: `/forge:do --full <idea>` must run first.

## State writes

- `routing.refine_attempts` — incremented once per round via
  `tools.state.increment_refine_attempts`.
- `refined_idea` — single-paragraph string, set on convergence or round-cap via
  `tools.state.record_refined_idea`.
- `current_phase` — transitions `refine -> spec` via `complete_phase` +
  `start_phase`.
- `deviations[]` — appended only on round-cap auto-mode degradation.

## See also

- `tools.state.increment_refine_attempts` — atomic round counter.
- `tools.state.record_refined_idea` — persists the converged paragraph.
- `commands/refine.md` — slash spec.
- `/forge:spec` — next phase; consumes `refined_idea` as Intent draft.
