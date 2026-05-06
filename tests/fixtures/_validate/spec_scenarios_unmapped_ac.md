---
id: 2026-05-06-scenarios-unmapped-ac
status: draft
tier: standard
created: 2026-05-06
capability: scenarios-unmapped-ac
---

# Intent

An acceptance criterion has no scenario referencing it.

# Scenarios (BDD)

Scenario: Only crit-1 is covered (criterion: 1)
  Given a request
  When it succeeds
  Then a success response returns

# Acceptance Criteria

1. The happy path returns a success response.
2. The retry path recovers from a transient error.

# Negative Requirements

- MUST NOT swallow transient errors silently.
