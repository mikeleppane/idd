---
name: idd-status
description: Print a one-line status summary for the active IDD feature. Use when the user asks "where am I?" or wants a quick view of phase, tier, and last commit without opening state.json.
disable-model-invocation: true
---

# IDD Status

## Goal

Print one line summarizing the active feature's lifecycle position. Read-only.

## Inputs

- Active feature folder (resolved via `tools.state.find_active_feature(repo_root, feature_id)`).
- `--feature <id>` flag: override active-feature resolution.
- `--verbose` flag: print phase history table after the summary.

## Steps

1. Resolve `repo_root` (cwd of invocation).
2. Call `tools.state.find_active_feature(repo_root, feature_id=<flag value or None>)`.
3. Read the resolved `state.json` via `tools.state.read_state`.
4. Build summary line:
   - `<feature_id> [<tier>] — phase: <current_phase> (<status>) — last commit: <sha-short>`
   - If `current_phase == "review"`: render phase as `review (target=<current_target>, done=[<comma-separated>])`.
   - `<sha-short>` = first 7 chars of `state.json.commits[-1].sha`, or `none` when commits is empty.
5. Print summary.
6. If `--verbose`: print a phase history table with columns `Phase | Status | Started | Completed`. Skip phases not present in `phases`. Reflect `targets_done` for the review row.

## Done

User sees a single status line (plus history table when `--verbose`); state.json is unchanged.
