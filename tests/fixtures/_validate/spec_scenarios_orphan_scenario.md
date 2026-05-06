---
id: 2026-05-06-scenarios-orphan
status: draft
tier: standard
created: 2026-05-06
capability: scenarios-orphan
---

# Intent

An orphan scenario references no acceptance criterion.

# Scenarios (BDD)

Scenario: Fully covered (criterion: 1)
  Given a cart with one item
  When the user checks out
  Then the order is placed

Scenario: Lonely path with no AC link
  Given some unrelated state
  When the user does an unrelated thing
  Then nothing maps back

# Acceptance Criteria

1. The user can complete a checkout.

# Negative Requirements

- MUST NOT silently drop checkout errors.
