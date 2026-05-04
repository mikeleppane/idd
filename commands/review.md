---
name: review
description: Run the review phase against the active feature — layered self-review, heavy on-demand subagent review, and a convergence loop on HIGH+ findings. Targets PLAN.md before execute or code after execute. Use after /idd:crucible (plan target) or /idd:execute (code target). Cross-AI review is M4 territory.
---

# /idd:review

Run the IDD review phase against the active feature.

## Behavior

1. Determine active feature (same rules as /idd:plan).
2. Parse args:
   - `--target plan` (default after crucible) — review PLAN.md. Output: `.idd/features/<id>/REVIEW.plan.md`.
   - `--target code` (default after execute) — review the diff plus PLAN.md mapping. Output: `.idd/features/<id>/REVIEW.code.md`.
   - `--cross-ai` — print "M4 territory; not implemented in M2" and exit non-zero.
3. Read `state.json`. For target=plan: require `phases.crucible.status == "done"`. For target=code: require `phases.execute.status == "done"`.
4. **Enter or resume the review phase.** If `phases.review.status != "in_progress"`, call `tools.state.start_phase(path, "review")`. If review is already `in_progress` (typical for the second pass — `target=code` after the first `target=plan` pass left review open), skip `start_phase` so `phases.review.targets_done` from the first pass survives. `start_phase` itself preserves `targets_done` and `current_target` across review restarts as a safety net.
5. Invoke the `idd-review` skill with the resolved target. The skill writes `REVIEW.<target>.md` (never plain `REVIEW.md`); the dual-pass standard-tier flow keeps two separate audit trails.
6. On completion, print: `REVIEW.<target>.md` path, findings by severity, convergence cycles run, final status (resolved | escalated).

## Failure modes

- `tier == "focused"` → abort: "Review is standard-tier+. Focused tier verifies directly via /idd:verify after /idd:execute."
- `--cross-ai` flag passed → fail with: "Cross-AI review is M4. Use /idd:review without --cross-ai for M2."
- Convergence loop fails to drive HIGH+ findings to zero in 3 cycles → halt, surface remaining findings to user, status=escalated.
