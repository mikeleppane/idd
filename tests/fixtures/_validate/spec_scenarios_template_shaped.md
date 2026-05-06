---
id: 2026-05-06-scenarios-template
status: draft
tier: standard
created: 2026-05-06
capability: scenarios-template
---

# Intent

A SPEC whose scenarios live inside a ```gherkin fence (mirrors templates/feature/SPEC.md).

# Scenarios (BDD)

```gherkin
Scenario: Token resolves criterion-1
  Given a fresh session
  When the user signs in
  Then the dashboard renders
```

# Acceptance Criteria

1. Successful sign-in renders the dashboard.

# Negative Requirements

- MUST NOT cache credentials on disk.
