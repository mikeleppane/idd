---
id: 2026-05-08-tdd-missing-feature
status: draft
tier: focused
created: 2026-05-08
capability: tdd-missing-feature
---

# Intent

Smoke fixture exercising `tools.validate.tdd_evidence` against a feature
where neither acceptance criterion has a paired test commit. AC-2 is
excused by a `## TDD Exception: AC-2` heading in `decisions.md`, so the
validator must emit exactly one BLOCK finding scoped to AC-1.

# Scenarios (BDD)

Scenario: First criterion is missing its test commit (AC-1)
  Given an impl commit lands without any preceding test commit
  When validate_tdd_evidence runs against this fixture
  Then a BLOCK finding is emitted for AC-1

Scenario: Second criterion is excused by ADR (AC-2)
  Given an impl commit lands without any preceding test commit
  And decisions.md records a TDD Exception for the criterion
  When validate_tdd_evidence runs against this fixture
  Then no finding is emitted for AC-2

# Acceptance Criteria

1. AC-1 lacks a paired test commit and surfaces a BLOCK finding.
2. AC-2 lacks a paired test commit but is excused via ADR and stays silent.

# Negative Requirements

- MUST NOT introduce real git commits — fixture SHAs are deterministic stubs.
