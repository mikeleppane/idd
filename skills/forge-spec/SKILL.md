---
name: forge-spec
description: Author or refine a feature SPEC.md following the FORGE §7.1 template. Use when the user asks to start a new FORGE feature, write a spec, or refine an existing SPEC.md. Produces the source-of-truth artifact for everything downstream.
disable-model-invocation: true
---

# FORGE Spec Authoring

## Goal

Produce a `.forge/features/<id>/SPEC.md` that obeys the §7.1 template and exits with no Open Questions.

## Inputs

- The user's idea text (free-form).
- `.forge/intel/` if present (read on demand, never preloaded wholesale).
- Existing related specs in `.forge/features/*/SPEC.md` (only when explicitly relevant).

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
5. **Initialize SPEC.md** from the copied template; `decisions.md` stays empty until the first decision is logged.
5a. **Constitution preflight.** Call `tools.constitution.load_and_filter(repo_root, idea_text=<idea>, files_in_scope=[])`. When `articles[]` is non-empty, include them in the spec-author subagent's dispatch budget under the `articles` field. The author MUST keep CRITICAL articles' rules in view while drafting Intent + Negative Requirements.
6. **Fill the template — one section at a time, asking only when ambiguous.**
   - **Frontmatter.** Set `id`, `status: draft`, `tier`, `created`, `capability` (stable handle).
   - **Intent.** One paragraph. WHY. Drill until the *why* is concrete.
   - **Context.** Background and constraints. Reference RESEARCH.md only if it already exists.
   - **Domain.** Glossary table. Aim for 4–8 terms; more is usually noise. Add a Mermaid sketch only if it clarifies a non-obvious relationship.
   - **Codebase Anchors.** Concrete `path:Symbol` pointers a subagent can use without reading the whole repo.
   - **Scope.** Behavior-level bullets. Make the Out of Scope list as long as the In Scope list when uncertainty is high — it surfaces tacit assumptions.
   - **Scenarios.** Markdown Gherkin. One scenario per behavior. Strict `.feature` files come later (M2). Cap at 5 in M1; if you need more, the slice is too big.
   - **Test Strategy.** Map each criterion to test type (unit / integration / scenario / UAT) and target location.
   - **Acceptance Criteria.** Falsifiable. Each maps to one scenario or one measurable outcome.
   - **Negative Requirements.** Explicit MUST-NOT statements. `/forge:verify` will assert each.
   - **Open Questions.** Numbered. Add liberally as you fill the template; resolve before exit.
7. **Self-review gate:**
   - Run: `python -m tools.validate --target spec-semantic .forge/features/<id>/SPEC.md` (covers Scenarios↔Acceptance mapping, orphan scenarios, weasel words, and Codebase Anchors path/symbol resolution). Any finding with severity `BLOCK` or `HIGH` blocks phase exit. `MEDIUM`/`LOW` are advisory; surface to the user.
   - For ACs that map to a measurable outcome instead of a scenario (per the current rule), append the literal token `(measurable)` to the AC line so the validator skips scenario-mapping for that index. Example:
     `4. p99 latency under 200ms over a 24h window (measurable)`
   - Inline checks (not migrated):
     - No placeholder text remaining.
     - Every Term in Domain appears at least once in Intent / Scope / Scenarios.
     - Every In Scope bullet is covered by ≥1 Scenario.
     - Every Acceptance criterion has a row in Test Strategy.
     - Open Questions count is 0.
     - Ambiguity score (heuristic): count words like "should", "might", "TBD", "etc." in non-list paragraphs. > 3 = block; refine.
8. **Update `state.json`:** call `tools.state.complete_phase(path, "spec")`, then `tools.state.start_phase(path, next_phase)` where `next_phase` is `execute` for `--focused`, otherwise the first phase the user requested.
9. **Surface to the user:** print path to SPEC.md, summarize Intent and Acceptance, list any accepted assumptions logged to `decisions.md` during refinement.

## Done

`.forge/features/<id>/SPEC.md` exists, satisfies the §7.1 template, and self-review passed. `state.json` reflects spec=done.
