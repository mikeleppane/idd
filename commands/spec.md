---
name: spec
description: Run the spec phase against the active feature, or start a new feature. Use when the user wants to create, refine, or finalize a feature SPEC.md. Wraps the idd-spec skill, sets up the feature folder, and updates state.json.
---

# /idd:spec

Run the IDD spec phase. Either:

- **New feature:** `/idd:spec "<idea text>"`
  Generates a feature id, creates `.idd/features/<id>/` with templates, then invokes the `idd-spec` skill to fill SPEC.md.
- **Refine existing:** `/idd:spec --feature <id>`
  Re-enters the spec phase for an existing feature; calls `tools.state.start_phase(path, "spec")` to reset that phase, then invokes the skill against the existing SPEC.md.

## Behavior

1. Parse args. If neither idea text nor `--feature <id>` is provided, error: `usage: /idd:spec "<idea>" | /idd:spec --feature <id>`.
2. For new feature: derive id, then call `tools.state.feature_folder_exists(repo_root, feature_id)`. If True, abort with a slug-suffix suggestion. Otherwise create folder, copy `templates/feature/SPEC.md`, `templates/feature/decisions.md`, and `templates/feature/state.json` into it; set `feature_id`, `tier` (default `focused` unless user passes `--standard` or `--full`), `current_phase = "spec"`.
3. For existing feature: read `.idd/features/<id>/state.json`, call `tools.state.start_phase(path, "spec")`.
4. Invoke the `idd-spec` skill (see `skills/idd-spec/SKILL.md`).
5. On completion, print: feature id, path to SPEC.md, next recommended step (`/idd:execute` for `--focused`).

## Failure modes

- Feature folder already exists with same id → suggest a slug suffix or `--feature <id>` to refine.
- `templates/feature/SPEC.md` missing in plugin → instruct user to reinstall plugin.
- `tools.state.write_state` raises StateError → surface error, do not write partial files.

## Constitution preflight

When `.idd/CONSTITUTION.md` is present, the skill calls `tools.constitution.load_and_filter` before its primary work and passes filtered `articles[]` into every subagent dispatch budget. No-op when absent.
