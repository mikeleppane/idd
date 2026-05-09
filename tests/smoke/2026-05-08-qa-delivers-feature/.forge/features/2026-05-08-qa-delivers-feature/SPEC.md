---
id: 2026-05-08-qa-delivers-feature
status: draft
tier: standard
created: 2026-05-08
capability: qa-delivers-feature
---

# Intent

Smoke fixture exercising `tools.validate.qa_shape` against a feature whose
post-ship QA record is internally consistent: every section status agrees
with the declared verdict and confidence. Validator must return zero
findings.

# Scope

## In scope

- A clean four-section QA.md aligned with the template shape.
- A state.json that marks the qa phase done with flow_version 3.

## Out of scope (Non-goals)

- Running real acceptance, adversarial, or NR-regrep modules against the
  fixture; the QA.md is hand-authored to a known-good shape.

# Scenarios (BDD)

Scenario: Validator accepts a clean QA record (criterion-1)
  Given a QA.md whose frontmatter agrees with all four section statuses
  When validate_qa_shape runs against this fixture
  Then no finding is emitted

# Acceptance Criteria

1. validate_qa_shape returns zero findings on this fixture.
2. state.json schema-validates and marks the qa phase done.

# Negative Requirements

- MUST NOT log secrets to stdout.
