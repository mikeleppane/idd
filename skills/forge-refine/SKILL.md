---
name: forge-refine
description: Socratic vague-idea collapse before /forge:spec. Use when /forge:do (full tier) routes to refine, or when the user invokes /forge:refine directly.
model: sonnet
disable-model-invocation: true
---

# FORGE Refine

> **`state.json` is hook-protected.** Mutate it only through the
> `tools.state.*` helpers — `complete_phase`, `start_phase`,
> `record_routing_decision`, `record_refined_idea`, `record_commit`,
> `append_deviation`, `set_execute_current_slice`. The PreToolUse hook
> at `hooks/check_state_writer.py` refuses direct `Write` / `Edit` /
> `MultiEdit` on `.forge/features/<id>/state.json` and surfaces a
> permission-deny with guidance toward the correct helper.

## When this skill applies

`/forge:do` (full tier) seeded routing and advanced state to
`current_phase == "refine"`, OR the user invoked `/forge:refine [--feature <id>]`
directly against an active feature whose `current_phase` is already `refine`.

## Inputs

- `--feature <id>` — optional. Single-active rule applies: when omitted, resolve
  the only feature whose `state.json.current_phase != "done"`; abort if zero
  or multiple match.
- `[<idea>]` — optional positional. CLI fallback for the idea text when
  `state.json.routing.idea` is absent. Used on the direct-invocation path
  `/forge:refine [--feature <id>] "<idea text>"` (still supported for
  re-running refine on an existing feature; the canonical entry is
  `/forge:do --full`).
- Idea source precedence: when `state.json.routing.idea` is present (seeded
  by `/forge:do --full`), use it and **ignore** any CLI `<idea>` (do not
  overwrite — routing is the canonical record). When absent AND CLI `<idea>`
  is given, seed the routing block first via
  `tools.state.record_routing_decision(path, idea=<idea>, final_tier="full",
  proposed_tier="full", rationale="direct /forge:refine invocation")` so
  `routing.refine_attempts` has a parent block to live under. When **both**
  are absent, abort with: `"/forge:refine needs an idea — pass one as an
  argument: /forge:refine \"<idea text>\""`.

## Adaptive routing entry

`/forge:do --full` is the canonical entry path: it seeds the feature folder,
writes the `routing` block to `state.json`, and advances `current_phase` to
`refine` with `phases.refine.status == "in_progress"` before printing the
dispatch literal `Next: /forge:refine --feature <feature_id>`. Direct
invocation (`/forge:refine --feature <id> "<idea>"`) is still supported for
re-running refine on an existing feature whose `current_phase` is already
`refine`; the direct-invocation fallback path below covers that case.

## Mode resolution

Two entry paths converge on this skill: the **`/forge:do --full` pre-seed**
branch (the routing entry-point seeded the feature folder +
`state.json.routing` block + advanced `current_phase` to `refine` before
dispatching here) and the **direct-invocation fallback**
(`/forge:refine --feature <id> "<idea>"` invoked standalone against an
existing feature folder, no upstream `/forge:do` seed).

**Pre-seed predicate (locked, four-conjunct AND).** The pre-seed branch
fires only when **all four** of the following hold; otherwise the
direct-invocation fallback path runs:

1. `--feature <id>` resolved (the user invoked `/forge:refine --feature <id>`
   — typically because `/forge:do --full` printed
   `Next: /forge:refine --feature <feature_id>`).
2. `state.json` parses successfully — i.e., the file exists and `state.json parses` cleanly as valid JSON.
3. `state.json.routing` block is present (`idea`, `final_tier`, `decided_at`
   all populated by `/forge:do --full` via
   `tools.state.record_routing_decision`).
4. `state.json.current_phase == "refine"` AND
   `state.json.phases.refine.status == "in_progress"`. Both must hold;
   either failing routes to the direct-invocation fallback. (The single
   conjunct combining both phase fields is treated as one conjunct of the
   four.)

All four conjuncts must hold for the pre-seed branch to fire. If any
conjunct fails — including the routing block being absent — the
**direct-invocation fallback** branch runs: seed the routing block from
CLI `<idea>` if `routing.idea` is absent; abort if both are absent.

**Pre-seed branch behavior.** When the predicate holds, `/forge:refine`
ENTERS the Socratic loop directly using `state.json.routing.idea` as the
seed text. It does **NOT** call `tools.state.record_routing_decision` —
the routing block is already populated by `/forge:do --full`, and
re-calling `record_routing_decision` here would clobber the seed
`decided_at` timestamp (the same clobber-prevention rationale as the
forge-spec pre-seed `start_phase` guard). The phase guard via
`tools.state.increment_refine_attempts` still runs on every round — both
pre-seed and direct-invocation paths must satisfy `current_phase ==
"refine"` before each round increments.

**Direct-invocation fallback behavior.** When ANY conjunct fails (e.g.,
`routing` block absent because the user invoked `/forge:refine`
standalone), seed the routing block via
`record_routing_decision(path, idea=<idea>, final_tier="full",
proposed_tier="full", rationale="direct /forge:refine invocation")` from
the CLI `<idea>` if `routing.idea` is absent; if both `routing.idea` AND
CLI `<idea>` were absent, abort with the same error message documented
under "Inputs" above.

## Steps

1. **Resolve feature.** Read `--feature <id>` or apply the single-active rule.
   Locate `.forge/features/<id>/state.json`.
2. **Guard tier + phase BEFORE any mutation.** Call
   `tools.state.guard_refine_entry(state_path)` which reads state.json once
   and refuses if `current_phase != "refine"` (message format:
   `"cannot enter refine: current_phase is '<X>', expected 'refine'"`) OR
   if `tier != "full"` (message format:
   `"refine phase is full-tier only; current tier is '<X>'"`). The helper
   returns the parsed payload so the rest of the skill does not need a
   second read. Refine is full-tier only — focused and standard tiers do
   not enter this phase. Do not invent a custom abort string; quote the
   helper. This guard MUST run BEFORE any call to
   `record_routing_decision` so a wrong-tier feature cannot have its
   routing block clobbered with `final_tier="full"`.
3. **(Per-round phase guard reminder.)** `tools.state.increment_refine_attempts`
   ALSO checks `current_phase == "refine"` and `tier == "full"` on every
   round (defense in depth) and raises with the same wording — message
   format: `"cannot increment refine_attempts: current_phase is '<X>',
   expected 'refine'"` and
   `"refine phase is full-tier only; current tier is '<X>'"`.
4. **Resolve idea source.** Branch on the pre-seed predicate (see "Mode
   resolution" above):
   - **Pre-seed branch** (all four conjuncts hold): `routing.idea` is
     already present in `state.json` (seeded by `/forge:do --full`). Use
     it as the seed text and ignore any CLI `<idea>` argument — routing
     is the canonical record. Do **NOT** call `record_routing_decision`
     again; doing so would clobber the seed `decided_at` timestamp.
   - **Direct-invocation fallback** (any conjunct fails). If
     `routing.idea` is present, use it and ignore any CLI
     `<idea>`. If `routing.idea` is absent AND a CLI `<idea>` was passed,
     seed the routing block first via
     `tools.state.record_routing_decision(path, idea=<idea>, final_tier="full",
     proposed_tier="full", rationale="direct /forge:refine invocation")` —
     this satisfies the schema requirement that `routing` carry `idea`,
     `final_tier`, and `decided_at`, and gives `increment_refine_attempts`
     a block to mutate. If **both** are absent, abort with:
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
7. **Persist refined idea.** On user confirm OR round-cap reached, run the forge-state Bash CLI (do NOT translate to a Python heredoc — `refined` is keyword-only and agents consistently mis-call it positionally):

   ```bash
   forge-state refine --feature <id> --refined "<paragraph>"
   ```

   The refined idea must be ≤4000 chars; the CLI surfaces the helper's `StateError` on overflow rather than truncating silently — trim before invoking. The schema mirrors the cap via `refined_idea.maxLength = 4000`. Module fallback: `PYTHONPATH=$CLAUDE_PLUGIN_ROOT python3 -m tools.state_cli refine ...`.
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
10. **Phase transition.** Run the forge-state Bash CLI:

    ```bash
    forge-state complete-phase --feature <id> --phase refine
    forge-state start-phase    --feature <id> --phase spec
    ```

    Print `Next: /forge:spec`. Module fallback: `PYTHONPATH=$CLAUDE_PLUGIN_ROOT python3 -m tools.state_cli ...`.

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
