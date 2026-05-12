---
version: 0.1.0
created: "2026-05-11"
---

# FORGE Lessons

Cross-feature trap memory. Each entry below is a recorded subagent failure
mode and the avoidance the user wants future dispatches to follow. Entries
are appended via the harvest path in `forge-review` (auto) or
`/forge:lesson` (manual). Status transitions go through
`tools.intel.lessons.amend_status`.

## L001 — Example seed entry (delete on first real append)
**Captured:** 2026-05-11 from feature m0-example
**Resolved by:** manual
**Trap:** Template seed placeholder. This entry exists only so the file
parses cleanly on a fresh repository; replace it with a real lesson on the
first harvest. Retired status keeps it out of the dispatch budget.
**Avoidance:** Delete this entry as soon as a real lesson is appended;
keeping retired template seeds in place is harmless but noisy. Real
entries should describe a concrete trap and the avoidance future
subagents must follow.
**Tags:** dispatch, validation
**Severity:** LOW
**Status:** retired
