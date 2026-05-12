---
name: forge-spec
description: Author or refine a feature SPEC.md following the FORGE §7.1 template. Use when the user asks to start a new FORGE feature, write a spec, or refine an existing SPEC.md. Produces the source-of-truth artifact for everything downstream.
disable-model-invocation: true
---

# FORGE Spec Authoring

> **`state.json` is hook-protected.** Mutate it only through the
> `tools.state.*` helpers — `complete_phase`, `start_phase`,
> `record_routing_decision`, `record_refined_idea`, `record_commit`,
> `append_deviation`, `set_execute_current_slice`. The PreToolUse hook
> at `hooks/check_state_writer.py` refuses direct `Write` / `Edit` /
> `MultiEdit` on `.forge/features/<id>/state.json` and surfaces a
> permission-deny with guidance toward the correct helper.

> **Headless-mode refusal (locked).** Step 6 of this skill fills the
> spec template "one section at a time, asking only when ambiguous"
> — interactive Q&A is part of the contract. When you detect that the
> current session cannot satisfy that contract (no TTY, `claude -p`
> non-interactive, or any runtime signal that user input is
> unavailable) AND you still hold one or more unresolved ambiguities
> after consuming the idea text and any inline pre-answers, you MUST
> exit without mutating `state.json`. Print, verbatim:
>
> `Spec refinement requires interactive Q&A and the current session is headless (claude -p / no TTY). Re-run /forge:spec --feature <id> in an interactive session, or pre-answer gaps inline in the prompt then re-invoke. The skill will exit without mutating state.json.`
>
> Do NOT call `complete_phase("spec")`, `start_phase(<next>)`, or
> guess at the ambiguities. The interactive session (or a richer
> pre-answer prompt) is the only forward path. Leave
> `phases.spec.status` as it was on entry so re-entry is clean.

## Goal

Produce a `.forge/features/<id>/SPEC.md` that obeys the §7.1 template and exits with no Open Questions.

## Inputs

- The user's idea text (free-form).
- `.forge/intel/` if present (read on demand, never preloaded wholesale).
- Existing related specs in `.forge/features/*/SPEC.md` (only when explicitly relevant).

## Mode resolution

Two entry paths converge on this skill: the **direct fallback** (direct
`/forge:spec "<idea>"` invocation, no upstream routing) and the **`/forge:do`
pre-seed** branch (the routing entry-point seeded the feature folder +
`state.json.routing` block before dispatching here).

**Pre-seed predicate (locked, four-conjunct AND).** The pre-seed branch
fires only when **all four** of the following hold; otherwise the M2
fallback path runs:

1. `--feature <id>` resolved (the user invoked `/forge:spec --feature <id>`,
   no `<idea>` text). The bare `/forge:spec --feature <id>` form is the
   handoff signature `/forge:do` prints as `Next: /forge:spec --feature
   <feature_id>`.
2. `state.json` parses successfully — i.e., the file exists and `state.json parses` cleanly as valid JSON.
3. `state.json.routing` block is present (set by `/forge:do` via
   `tools.state.record_routing_decision`).
4. `state.json.current_phase == "spec"` AND `state.json.phases.spec.status
   == "in_progress"`. Both must hold; either failing routes to the M2
   fallback. (The single conjunct combining both phase fields is treated
   as one conjunct of the four.)

All four conjuncts must hold for the pre-seed branch to fire. If any
conjunct fails — including the routing block being absent — the **direct
fallback** branch runs all steps in order as today (forge-spec creates
the folder itself; `routing` block stays absent; `start_phase("spec")`
runs as today's behavior).

**Pre-seed branch behavior.** When the predicate holds, **skip steps 1, 2, 3, and 4** (capability scan, feature-id compute, collision check, folder create) — `/forge:do` already ran the scan, derived the slug, and created the folder via `tools.archive.create_feature_folder`. Entry resumes at **step 5** (Initialize SPEC.md from the existing template files in the pre-seeded folder). The Step 8 phase transition also picks
up a guard described below.

## Steps

1. **Capability scan (new-feature path only, all tiers, runs first).**
   This step runs **only** when the caller invoked `/forge:spec "<idea>"` —
   i.e., the new-feature path with idea text. **Skip this step entirely
   when invoked as `/forge:spec --feature <id>`** (refine-existing); that
   mode has no idea text and operates on a known feature folder.

   On the new-feature path: compute `slug =
   tools.archive.slug_from_idea(idea_text)`. Read `existing =
   tools.archive.scan_existing_capabilities(repo_root)`. If `existing` is
   non-empty AND `slug in existing`, prompt the user: "Capability `<slug>`
   already exists. Route to `/forge:change` for delta proposal? (y/n)".
   - On `y`: exit and dispatch
     `/forge:change --capability <slug> "<change_description>"`. Do NOT
     create `.forge/features/<id>/`.
   - On `n`: ask the user for a disambiguating slug suffix (e.g.,
     `<slug>-v2`, `<slug>-bulk`). Re-run the scan with the new slug. Abort
     with an error if the collision persists.
   - When `existing` is empty (no canonical capabilities exist yet), this
     step is a no-op — skip the prompt and proceed to step 2.
2. **Generate the feature id.** Format: `YYYY-MM-DD-<kebab-slug>`. Slug = 2–5 words, derived from the idea.
3. **Check for collision.** If `.forge/features/<id>/` already exists, abort with: "Feature folder already exists. Re-run with `--feature <id>` to refine, or pick a different slug." Use `tools.state.feature_folder_exists(repo_root, feature_id)`.
4. **Create the feature folder.** `.forge/features/<id>/`. Copy `templates/feature/state.json`, `templates/feature/SPEC.md`, and `templates/feature/decisions.md` into it; set `feature_id`, `tier` (default `focused`), and `current_phase: "spec"`.
4a. **Research-aware prelude (when applicable).** Read `state.json.phases.research`. Behavior is mode-driven:

    - When `phases.research.status == "done"`: read the body of `.forge/features/<id>/RESEARCH.md` and prepend the first ≤6000 chars (≈1500 tokens at 4 chars/token; truncate at the nearest paragraph boundary `\n\n`) to the spec context block under the header `## Research excerpt (mode: <research_grounding>)` (the `research_grounding` value is read from RESEARCH.md frontmatter). Subsequent template-fill steps consume the excerpt as background grounding alongside the user idea.
    - When `phases.research.status == "skipped"`: log the carry-over line `Research skipped: <reason>; spec proceeds without external grounding excerpt.` to skill output and continue without an excerpt; `<reason>` is the matching entry in `state.json.skipped[].reason` (when present) or the literal string `no reason recorded`.
    - When `phases.research` is absent (legacy v1/v2 features authored before the research phase shipped, or focused tier where research never runs): no-op.

    The prelude is read-only; it never mutates `state.json` or RESEARCH.md.
5. **Initialize SPEC.md** from the copied template; `decisions.md` stays empty until the first decision is logged.
5a. **Constitution preflight.** Call `tools.constitution.load_and_filter(repo_root, idea_text=<idea>, files_in_scope=[])`. When `articles[]` is non-empty, include them in the spec-author subagent's dispatch budget under the `articles` field. The author MUST keep CRITICAL articles' rules in view while drafting Intent + Negative Requirements.
5b. **Lessons preflight.** Call
    `tools.intel.lessons.load_and_filter(repo_root, idea_text=<idea>, files_in_scope=[])`.
    When the returned `lessons[]` is non-empty, include them in the
    spec-author subagent's dispatch budget under the `traps` field,
    serialized via `Lesson.to_budget_dict()` (locked JSON shape — `id`,
    `trap`, `avoidance`, `tags`, `severity`, `status`). The author MUST
    keep CRITICAL traps in view while drafting Intent + Negative
    Requirements; a trap that has previously caused a regression should
    not silently recur in the new spec. The PreToolUse hook
    (`hooks/check_budget.py`) is permissive on the `traps` field — no
    schema validation at hook layer; the producing skill owns shape.
    Missing `.forge/intel/lessons.md` is a no-op (`load_and_filter`
    returns `([], [])` on a fresh repo); pass the empty list through to
    the budget unchanged.
6. **Fill the template — one section at a time, asking only when ambiguous.**
   - **Frontmatter.** Set `id`, `status: draft`, `tier`, `created`, `capability` (stable handle).
   - **Intent.** One paragraph. WHY. Drill until the *why* is concrete. **Idea-source precedence (locked, three-level)** — consume sources in order, falling back when the prior source is absent:
     1. `state.json.refined_idea` — set by `/forge:refine` on full-tier features. When non-empty, seed the Intent draft from it: lift the paragraph verbatim, then refine for spec voice. (Empty for focused/standard tiers; no-op on the focused/standard pre-seed path.)
     2. `state.json.routing.idea` — set by `/forge:do` on the focused/standard pre-seed path. When `refined_idea` is absent but `routing.idea` is present, seed the Intent draft from `routing.idea` and refine for spec voice. This is the secondary source — slotted between `refined_idea` (primary) and the CLI `<idea>` argument (tertiary).
     3. CLI `<idea>` argument — the direct-invocation path. When both `refined_idea` AND `routing.idea` are absent, draft Intent directly from the user's idea text passed positionally to `/forge:spec`.
   - **Context.** Background and constraints. Reference RESEARCH.md only if it already exists.
   - **Domain.** Glossary table. Aim for 4–8 terms; more is usually noise. Add a Mermaid sketch only if it clarifies a non-obvious relationship. **Full-tier exception:** when the feature's tier is `full`, leaving `# Domain` as a single-line placeholder `_TBD: filled by /forge:domain_` is acceptable — the dedicated `/forge:domain` phase populates the section after spec exits. For focused and standard tiers, Domain MUST be filled at spec time (existing behavior).
   - **Codebase Anchors.** Concrete `path:Symbol` pointers a subagent can use without reading the whole repo.
   - **Scope.** Behavior-level bullets. Make the Out of Scope list as long as the In Scope list when uncertainty is high — it surfaces tacit assumptions.
   - **Scenarios.** Markdown Gherkin. One scenario per behavior. Strict `.feature` files come later. Cap at 5; if you need more, the slice is too big.
   - **Test Strategy.** Map each criterion to test type (unit / integration / scenario / UAT) and target location.
   - **Acceptance Criteria.** Falsifiable. Each maps to one scenario or one measurable outcome.
   - **Negative Requirements.** Explicit MUST-NOT statements. `/forge:verify` will assert each.
   - **Open Questions.** Numbered. Add liberally as you fill the template; resolve before exit.
7. **Self-review gate:**
   - Run: `python -m tools.validate --target spec-semantic .forge/features/<id>/SPEC.md` (covers Scenarios↔Acceptance mapping, orphan scenarios, weasel words, and Codebase Anchors path/symbol resolution). Any finding with severity `BLOCK` or `HIGH` blocks phase exit. `MEDIUM`/`LOW` are advisory; surface to the user.
     **Division of responsibility:** this skill runs the validator first so the user sees every finding inline with file pointers and recovery hints (better UX than a single raised exception). The mechanical gate lives in `tools.state.complete_phase("spec")`, which re-runs `validate_anchors` + `validate_scenarios` against the same SPEC.md and refuses the transition with `StateError` when any HIGH/BLOCK finding remains. The skill cannot bypass that gate; surfacing findings here is the cooperative path that avoids the helper raising on exit.
   - If `.forge/features/<id>/DOMAIN.md` exists AND its frontmatter `status` is `ready` or `locked` (re-entry after `/forge:domain` closed its Socratic loop, or post-domain spec amendment): run `python -m tools.validate --target domain_glossary .forge/features/<id>` to catch orphan terms introduced by the spec edit. The validator self-gates on the same `status` field (per `docs/plans/2026-05-08-m7-confidence-and-ux-polish.md` P1.1) — `locked` keeps `BLOCK` severities; `ready`/`draft` downgrade orphan/duplicate/malformed findings to `MEDIUM` so first-pass authoring is never gated by an in-flight DOMAIN.md. When DOMAIN.md is absent, or its `status` is `draft` (still inside the Socratic loop), skip this bullet entirely — first-pass full-tier features author DOMAIN.md *after* spec, so requiring its presence here is a chicken-and-egg trap.
   - For ACs that map to a measurable outcome instead of a scenario (per the current rule), append the literal token `(measurable)` to the AC line so the validator skips scenario-mapping for that index. Example:
     `4. p99 latency under 200ms over a 24h window (measurable)`
   - Inline checks (not migrated):
     - No placeholder text remaining.
     - Every Term in Domain appears at least once in Intent / Scope / Scenarios.
     - Every In Scope bullet is covered by ≥1 Scenario.
     - Every Acceptance criterion has a row in Test Strategy.
     - Open Questions count is 0.
     - Ambiguity score (heuristic): count words like "should", "might", "TBD", "etc." in non-list paragraphs. > 3 = block; refine.
     - **Full-tier Domain-placeholder allowance.** When the feature's tier is `full` AND the `# Domain` section body matches the canonical placeholder, the inline check "Every Term in Domain appears at least once in Intent / Scope / Scenarios" is **skipped** — `/forge:domain` will populate Domain in the next phase. All other gates (NR placement, AC-falsifiability, scenarios cap, Open Questions count, weasel-word ambiguity score) still apply. **Comparator (locked):** strip leading and trailing whitespace (including any trailing newline) from the section body, then test against the regex `^_TBD: filled by /forge:domain_$` — the trailing italic underscore is **mandatory** (the prior `_?` form silently accepted the unrendered no-underscore variant, which masked partial fills). No other text, comments, or partial fills are accepted. Backslash-escaped underscores (`\_TBD: ... \_`) do NOT match and are treated as missing Domain content.
   - **When the gate blocks phase exit** (any `BLOCK` or `HIGH` finding from `spec-semantic` or `domain_glossary`, OR any inline check failure):
     1. Surface every unresolved finding with its `file:line` pointer and the validator rule that flagged it. `MEDIUM`/`LOW` items stay advisory but still surface so the user sees the full picture.
     2. Print, verbatim: `Re-run /forge:spec --feature <id> after addressing the findings above to finalize and advance to <next_phase>.` Resolve `<next_phase>` from `state.json.tier` using the same mapping as step 8 (`focused → execute`, `standard → scenarios`, `full → domain`). Do NOT print the downstream phase command — the downstream commands all refuse when `phases.spec.status != "done"`.
     3. Do NOT call `complete_phase("spec")` or `start_phase(<next>)`. Leave `phases.spec.status == "in_progress"` so the user can re-enter the skill against the same partial draft.
8. **Update `state.json`:** call `tools.state.complete_phase(path, "spec")`, then `tools.state.start_phase(path, next_phase)`.

   **Pre-seed `start_phase` guard (locked).** When the pre-seed branch fired (predicate held on entry — `routing` block present AND `current_phase == "spec"` AND `phases.spec.status == "in_progress"`), do **NOT** call `start_phase("spec")` at any point during this skill's execution. `/forge:do` already wrote `phases.spec.status: "in_progress"` (and the seed `started_at` timestamp) via `tools.archive.create_feature_folder`'s seed body, and re-calling `start_phase("spec")` here would clobber that seed `started_at`. Only the trailing `complete_phase("spec")` + `start_phase(<next per tier>)` runs at exit. (Historically `forge-spec` never called `start_phase("spec")` — it created the folder fresh — so the direct-invocation fallback path is unaffected.)

   **Resolve `next_phase` deterministically from `state.json.tier`** (mirrors `tools.state._FOCUSED_NEXT` / `_STANDARD_NEXT` / `_FULL_NEXT`):
   - when the feature's tier is `focused` → `next_phase = "execute"`
   - when the feature's tier is `standard` → `next_phase = "scenarios"`
   - when the feature's tier is `full` → `next_phase = "domain"` (the dedicated `/forge:domain` phase fills the `_TBD: filled by /forge:domain_` placeholder allowed in the Domain bullet above)
   Do NOT pick `next_phase` from a free-form user request — the tier alone determines it. The `# Domain` placeholder is only acceptable when the very next phase is `/forge:domain`; deriving `next_phase` from the tier closes the gap where a full-tier spec could otherwise leak the placeholder past `/forge:domain`. As a cross-check, `tools.state.current_phase_command(payload)` after `start_phase(path, next_phase)` should return `"/forge:domain"` for full / `"/forge:scenarios"` for standard / `"/forge:execute"` for focused. Do NOT call `next_phase_command` here — by this point `current_phase` already advanced to the just-opened phase, so `next_phase_command` returns the phase *after* it and would surface a downstream slash that the next phase's preconditions will refuse.
9. **Surface to the user:** print path to SPEC.md, summarize Intent and Acceptance, list any accepted assumptions logged to `decisions.md` during refinement.

## Done

`.forge/features/<id>/SPEC.md` exists, satisfies the §7.1 template, and self-review passed. `state.json` reflects spec=done.
