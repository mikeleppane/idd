---
schema_version: 1
spec: 0000-00-00-template-placeholder
target: plan
status: open
cycles: 1
---

# Findings

| ID | Severity | Status | Resolved by | Location | Problem | Recommended Fix | Source |
|----|----------|--------|-------------|----------|---------|-----------------|--------|
| F-1 | BLOCK | open  |             | path/file.py:42 | <problem> | <fix> | self |
| F-2 | HIGH  | resolved | 1a2b3c4d5e6f7890abcdef1234567890abcdef12 | PLAN.md slice 2 wave 1 | <problem> | <fix> | heavy-subagent |

`Status` values:
- `open` — finding stands as written.
- `resolved` — fix landed in code or spec; reviewer reverified.
- `accepted-risk` — exception logged in `decisions.md` referencing the finding id.

The §5.3.9 ship gate (`tools.ship_gate.parse_review_findings`) only acts on `Status: open` rows; `resolved` and `accepted-risk` are history.

`Resolved by` values:
- Empty — finding status is `open` or the resolution method was not recorded.
- 40-hex git SHA — fix landed in this commit. Required for harvest to lessons.
- `spec-edit` — finding resolved by editing the spec, not the code.
- `plan-edit` — finding resolved by editing the plan.
- `accepted-risk:<reason>` — exception logged in `decisions.md`; not a code fix.

The §5.3.9 ship gate accepts any of these on `Status: resolved` rows. The
trap-memory harvest path (see `tools.intel.lessons`) only fires on
`Status: resolved` AND `Resolved by` carrying a 40-hex SHA AND severity HIGH+.

# Convergence Log

| Cycle | Findings opened | Findings resolved | HIGH+ remaining |
|-------|-----------------|-------------------|-----------------|
| 1 | 2 | 0 | 1 |

# Decision

<resolved | escalated to user | accepted with risk noted in decisions.md>
