---
name: forge-refine
description: Socratic vague-idea collapse before /forge:spec. Use when /forge:do (full tier) routes to refine, or when the user invokes /forge:refine directly.
model: sonnet
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
- Idea text — read from `state.json.routing.idea` (seeded by `/forge:do --full`).
  Refine does not accept an idea on the CLI; if `routing.idea` is absent the
  user must run `/forge:do --full <idea>` first.

## Steps

1. **Resolve feature.** Read `--feature <id>` or apply the single-active rule.
   Locate `.forge/features/<id>/state.json`.
2. **Guard phase.** Read state.json; require `current_phase == "refine"`.
   Otherwise abort with `StateError("refine phase already complete or skipped")`.
3. **Read seeded idea.** Pull `state.json.routing.idea`. Abort if missing —
   instruct the user to run `/forge:do --full <idea>` first so routing is seeded.
4. **Detect ambiguity.** Scan the idea for vague verbs ("improve", "better",
   "fix"), compound goals (multiple "and"-joined outcomes), and missing
   acceptance signal (no measurable change implied).
5. **Socratic loop, max 5 rounds.** While ambiguity remains and round count is
   below 5:
   a. Ask one clarifying question targeting the highest-impact ambiguity.
   b. After the user replies, call `tools.state.increment_refine_attempts(path)`
      to bump `routing.refine_attempts` by 1.
   c. Synthesize a candidate refined-idea paragraph and ask the user to
      confirm or continue.
6. **Persist refined idea.** On user confirm OR round-cap reached, call
   `tools.state.record_refined_idea(path, refined=...)` to write
   `state.json.refined_idea`.
7. **Round-cap deviation.** When 5 rounds exhausted without convergence:
   - **Interactive mode:** halt; prompt the user to re-state the idea.
   - **Auto mode:** append a `decisions.md` entry **and** append a structured
     object to `state.json.deviations` matching the schema shape
     `{phase, cause, resolution}` (per `schemas/state.schema.json` —
     `deviations[]` items are objects, never bare strings). Use:
     `{"phase": "refine", "cause": "round cap reached", "resolution": "proceeding with best-effort refinement"}`.
     Then proceed with the best-effort paragraph.
8. **Self-review.** Confirm:
   - Single feature scope (no compound goals).
   - Measurable outcome implied.
   - No multi-feature spillover. If the user cannot collapse to one feature,
     suggest re-running `/forge:do --full` and abort.
9. **Phase transition.** Call `tools.state.complete_phase(path, "refine")` then
   `tools.state.start_phase(path, "spec")`. Print `next: /forge:spec`.

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
