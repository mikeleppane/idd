---
id: 2026-05-06-scenarios-weasel-body
status: draft
tier: standard
created: 2026-05-06
capability: scenarios-weasel-body
---

# Intent

A scenario whose Given/When/Then body contains a weasel word.

# Scenarios (BDD)

Scenario: Validate input (criterion: 1)
  Given a request
  When it arrives
  Then the system might reject it with TBD details

# Acceptance Criteria

1. Inputs are validated before persistence.

# Negative Requirements

- MUST NOT persist unvalidated inputs.
