---
id: 2026-05-07-plan-tasks-template-phrase
status: draft
tier: focused
created: 2026-05-07
capability: plan-tasks-template-phrase
---

# Intent

A SPEC paired with a PLAN that uses the shipped template's
`<Scenario 1 passes + criterion-1 met>` Acceptance phrase. The `_AC_TOKEN`
regex matches both `Scenario 1` and `criterion-1` against AC index 1; without
per-slice deduping the validator reports a false multi-slice HIGH.

# Acceptance Criteria

1. Single AC referenced twice in the same slice still resolves to one slice.
