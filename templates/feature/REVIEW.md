---
spec: 0000-00-00-template-placeholder
target: plan
status: open
cycles: 1
---

# Findings

| ID | Severity | Status | Location | Problem | Recommended Fix | Source |
|----|----------|--------|----------|---------|-----------------|--------|
| F-1 | BLOCK | open | path/file.py:42 | <problem> | <fix> | self |
| F-2 | HIGH  | open | PLAN.md slice 2 wave 1 | <problem> | <fix> | heavy-subagent |

`Status` values:
- `open` — finding stands as written.
- `resolved` — fix landed in code or spec; reviewer reverified.
- `accepted-risk` — exception logged in `decisions.md` referencing the finding id.

The §5.3.9 ship gate (`tools.ship_gate.parse_review_findings`) only acts on `Status: open` rows; `resolved` and `accepted-risk` are history.

# Convergence Log

| Cycle | Findings opened | Findings resolved | HIGH+ remaining |
|-------|-----------------|-------------------|-----------------|
| 1 | 2 | 0 | 1 |

# Decision

<resolved | escalated to user | accepted with risk noted in decisions.md>
