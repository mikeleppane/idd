---
id: 2026-05-06-scenarios-weasel-title
status: draft
tier: standard
created: 2026-05-06
capability: scenarios-weasel-title
---

# Intent

A scenario whose title contains a weasel word.

# Scenarios (BDD)

Scenario: Should validate the input (criterion: 1)
  Given a request
  When it arrives
  Then the input is validated

# Acceptance Criteria

1. Inputs are validated before persistence.

# Negative Requirements

- MUST NOT persist unvalidated inputs.
