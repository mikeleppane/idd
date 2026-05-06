---
id: 2026-05-06-scenarios-bare-digit
status: draft
tier: standard
created: 2026-05-06
capability: scenarios-bare-digit
---

# Intent

A scenario titled `OAuth 2 login` must NOT count as a reference to AC 2 — bare digits never map.

# Scenarios (BDD)

Scenario: OAuth 2 login (criterion: 1)
  Given a registered identity provider
  When the user signs in via OAuth 2
  Then a session is established

# Acceptance Criteria

1. Users can sign in via OAuth 2.
2. Password reset emails arrive within one minute.

# Negative Requirements

- MUST NOT log raw OAuth tokens.
