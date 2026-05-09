---
id: 2026-05-08-tdd-paired-feature
status: draft
tier: focused
created: 2026-05-08
capability: tdd-paired-feature
---

# Intent

Smoke fixture exercising `tools.validate.tdd_evidence` against a feature
where every acceptance criterion has a paired test->impl commit recorded
in chronological order. Validator must return zero findings.

# Scenarios (BDD)

Scenario: First criterion lands paired (AC-1)
  Given a test commit precedes its impl commit in state.commits[]
  When validate_tdd_evidence runs against this fixture
  Then no finding is emitted for AC-1

Scenario: Second criterion lands paired (AC-2)
  Given a second test commit precedes its second impl commit
  When validate_tdd_evidence runs against this fixture
  Then no finding is emitted for AC-2

# Acceptance Criteria

1. AC-1 has a paired test->impl commit ordering.
2. AC-2 has a paired test->impl commit ordering.

# Negative Requirements

- MUST NOT introduce real git commits — fixture SHAs are deterministic stubs.
