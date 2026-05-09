---
name: forge-status
description: Print a one-line status summary for the active FORGE feature. Use when the user asks "where am I?" or wants a quick view of phase, tier, and last commit without opening state.json.
disable-model-invocation: true
---

# FORGE Status

## Goal

Print one line summarizing the active feature's lifecycle position. Read-only.

## Inputs

- Active feature folder (resolved via `tools.state.find_active_feature(repo_root, feature_id)`).
- `--feature <id>` flag: override active-feature resolution.
- `--verbose` flag: print phase history table after the summary.
- `--report` flag: print a structured markdown report (current phase, last
  ≤5 commits, open BLOCK + HIGH findings scoped to the active feature,
  recommended next command) instead of the one-line summary. Mutually
  exclusive with `--verbose`.

## Steps

1. Resolve `repo_root` (cwd of invocation).
2. Call `tools.state.find_active_feature(repo_root, feature_id=<flag value or None>)`.
3. Read the resolved `state.json` via `tools.state.read_state`.
4. Build summary line:
   - `<feature_id> [<tier>] — phase: <current_phase> (<status>) — last commit: <sha-short>`
   - If `current_phase == "review"`: render phase as `review (target=<current_target>, done=[<comma-separated>])`. **If `current_target` is absent (review just started, no `set_review_target` call yet), render `target=none`.** **If `targets_done` is absent or empty, render `done=[]`.** Same fallbacks the verbose section uses; the summary line MUST NOT print the literal string `None` or `target=` with an empty value.
   - **If `current_phase == "refine"`:** append `(round X/5)` to the phase column, where `X` is `state.json.routing.refine_attempts` (default `0` when the field is absent — first call to `/forge:refine` has not happened yet). The cap (`5`) is the literal Socratic-loop ceiling enforced by `tools.state.increment_refine_attempts`; see `_REFINE_ATTEMPTS_CAP` and the `routing.refine_attempts.maximum` schema mirror. Example: `phase: refine (in_progress) (round 3/5)`.
   - `<sha-short>` = first 7 chars of `state.json.commits[-1].sha`, or `none` when commits is empty.
5. Print summary.
6. If `--verbose`: print a phase history table after the summary line. Pin the rendering as follows:

   - **Row order:** canonical lifecycle order — `refine, research, spec, domain, scenarios, plan, crucible, review, execute, verify, ship`. Skip phases not present in `state.json.phases`.
   - **Columns:** `Phase | Status | Started | Completed`. Render with markdown-pipe separators (single `|` with one space of padding on each side). No alignment padding required — output one terminal-friendly row per phase.
   - **Header row:** the four column labels above, followed by a separator row of dashes (`---`) one cell each. Match the GitHub-Flavored-Markdown table convention.
   - **Status column:** render `state.json.phases.<phase>.status` verbatim (`pending`, `in_progress`, `done`, `skipped`). For the `review` row only, append the target progress in parentheses: `<status> (target=<current_target>, done=[<comma-separated-targets_done>])`. If `current_target` is absent, render as `target=none`. If `targets_done` is absent or empty, render as `done=[]`. Example for in-flight code review: `in_progress (target=code, done=[plan])`. Example for completed review: `done (target=code, done=[plan, code])`.
   - **Timestamps:** render `started_at` and `completed_at` as the literal RFC 3339 strings stored in `state.json` (no reformatting). When a field is absent, render the literal string `none`.
   - **Skipped phases:** entries from `state.json.skipped[]` render as a row with `Status` = `skipped` and the `reason` text appended in parentheses: `skipped (M3 deferred — manual research acceptable)`. Started/Completed columns render `none` for skipped rows.
   - **Empty table:** if no phases match (newly-seeded feature, `phases == {}` and `skipped == []`), print only the header + separator rows; do not print "(no phases yet)" or any other placeholder.
7. **`--report` mode (structured output).** When `--report` is set:
   - Run `python -m tools.status_report --repo-root <repo_root> [--feature <id>]` and print its stdout verbatim. The CLI resolves the active feature, calls per-feature validators in-process scoped to that feature, builds the report, and emits the rendered markdown — exit code `0` on success, `1` when the active feature cannot be resolved (operator passes `--feature <id>` to recover).
   - Skip the one-line summary in this mode (replaced by the structured report).
   - `--verbose` and `--report` are mutually exclusive; if both are passed, abort with the literal error `"--report and --verbose cannot be combined"`.

## Done

User sees a single status line (plus history table when `--verbose`); state.json is unchanged.
