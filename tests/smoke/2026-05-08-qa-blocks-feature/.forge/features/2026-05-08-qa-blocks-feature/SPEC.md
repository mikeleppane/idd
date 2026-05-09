---
id: 2026-05-08-qa-blocks-feature
status: draft
tier: standard
created: 2026-05-08
capability: qa-blocks-feature
---

# Intent

Smoke fixture exercising `tools.validate.qa_shape` against a feature whose
post-ship QA record disagrees with itself: frontmatter declares the
feature `delivers` with `high` confidence while the per-section statuses
report `does-not-deliver` and one `partial`. Validator must return at
least two BLOCK findings — one for the verdict mismatch and one for the
confidence aggregation mismatch.

# Scope

## In scope

- A QA.md whose frontmatter contradicts the per-section statuses.
- A state.json that marks the qa phase done with flow_version 3.

## Out of scope (Non-goals)

- Running real QA modules. The mismatched record is hand-authored.

# Scenarios (BDD)

Scenario: Validator surfaces verdict mismatch (criterion-1)
  Given a QA.md whose frontmatter verdict disagrees with the Acceptance Status
  When validate_qa_shape runs against this fixture
  Then a BLOCK finding with code qa_shape:verdict_mismatch is emitted

# Acceptance Criteria

1. validate_qa_shape emits qa_shape:verdict_mismatch on this fixture.
2. validate_qa_shape emits qa_shape:confidence_aggregation_mismatch on this fixture.

# Negative Requirements

- MUST NOT log secrets to stdout.
