---
name: spec
description: Run the spec phase against the active feature, or start a new feature. Use when the user wants to create, refine, or finalize a feature SPEC.md. Wraps the forge-spec skill, sets up the feature folder, and updates state.json.
---

# /forge:spec

Run the FORGE spec phase. Either:

- **New feature:** `/forge:spec "<idea text>"`
  Generates a feature id, creates `.forge/features/<id>/` with templates, then invokes the `forge-spec` skill to fill SPEC.md.
- **Refine existing:** `/forge:spec --feature <id>`
  Re-enters the spec phase for an existing feature; calls `tools.state.start_phase(path, "spec")` to reset that phase, then invokes the skill against the existing SPEC.md.

## Capability scan (new-feature path only)

When the command is invoked with idea text (the new-feature path), the skill
computes a slug from that idea text via `tools.archive.slug_from_idea` and
checks `.forge/specs/` for an existing canonical capability via
`tools.archive.scan_existing_capabilities`. If a match is found, the skill
offers to route to `/forge:change` for a delta proposal instead of creating a
new feature folder.

The capability scan does NOT run for `/forge:spec --feature <id>` (refine
existing) — that mode operates on the already-selected feature folder and has
no idea text to scan.

## Behavior

1. Parse args. If neither idea text nor `--feature <id>` is provided, error: `usage: /forge:spec "<idea>" | /forge:spec --feature <id>`.
2. **Capability scan (new-feature path only, all tiers, before feature folder creation):** when the command was invoked with idea text, call `tools.archive.slug_from_idea(idea_text)` and `tools.archive.scan_existing_capabilities(repo_root)`. If the slug already exists in `.forge/specs/`, prompt the user to route to `/forge:change` instead. On `y`, dispatch `/forge:change --capability <slug>` and exit — do NOT create `.forge/features/<id>/`. On `n`, continue with a user-provided slug suffix. **Skip this step entirely when `--feature <id>` was given** (no idea text exists; the user is refining a known feature).
3. For new feature: derive id, then call `tools.state.feature_folder_exists(repo_root, feature_id)`. If True, abort with a slug-suffix suggestion. Otherwise create folder, copy `templates/feature/SPEC.md`, `templates/feature/decisions.md`, and `templates/feature/state.json` into it; set `feature_id`, `tier` (default `focused` unless user passes `--standard` or `--full`), `current_phase = "spec"`.
4. For existing feature (`--feature <id>`): read `.forge/features/<id>/state.json`. **Skip `start_phase("spec")` when the feature is already mid-refinement** — i.e., when `current_phase == "spec"` AND `phases.spec.status == "in_progress"`. That state is produced by `/forge:do`'s pre-seed (`tools.archive.create_feature_folder` writes the seed `phases.spec` entry with `started_at`) or by a re-entry after step 7's self-review gate held the phase open; re-issuing `start_phase("spec")` would clobber the existing `started_at` timestamp and reset the phase entry, violating the forge-spec headless-refusal banner's promise to leave `phases.spec.status` as it was on entry. Otherwise (the user is re-entering a `done` or `pending` spec, or any other state that needs to reopen the phase), call `tools.state.start_phase(path, "spec")` to reset the phase entry. The capability scan in step 2 is skipped on this path regardless of the branch taken here.
5. Invoke the `forge-spec` skill (see `skills/forge-spec/SKILL.md`).
6. On completion, print feature id and path to SPEC.md, then branch the next-step prose on `phases.spec.status` so the user is never told to dispatch a phase that will refuse:
   - **When `phases.spec.status == "done"`** (step 7 self-review cleared, step 8 wrote the status flip): resolve the slash for the phase the skill just opened via `tools.state.current_phase_command(payload)` and print it verbatim (e.g. `/forge:execute --feature <id>` for focused, `/forge:scenarios --feature <id>` for standard, `/forge:domain --feature <id>` for full). Do NOT call `next_phase_command` here — by step 8 `current_phase` already moved to the freshly-opened phase, so `next_phase_command` would return the phase *after* it and tell the user to skip ahead.
   - **When `phases.spec.status == "in_progress"`** (a validator `BLOCK`/`HIGH` finding or an inline check held the gate, or the user is still reviewing a draft): instead print `Re-run /forge:spec --feature <id> after addressing the findings above to finalize and advance.` Do NOT print the downstream phase command — the downstream commands all enforce `phases.spec.status == "done"` and would refuse.

## Failure modes

- Feature folder already exists with same id → suggest a slug suffix or `--feature <id>` to refine.
- `templates/feature/SPEC.md` missing in plugin → instruct user to reinstall plugin.
- `tools.state.write_state` raises StateError → surface error, do not write partial files.

## Constitution preflight

When `.forge/CONSTITUTION.md` is present, the skill calls `tools.constitution.load_and_filter` before its primary work and passes filtered `articles[]` into every subagent dispatch budget. No-op when absent.
