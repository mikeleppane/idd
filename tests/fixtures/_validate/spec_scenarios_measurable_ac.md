---
id: 2026-05-06-scenarios-measurable
status: draft
tier: standard
created: 2026-05-06
capability: scenarios-measurable
---

# Intent

An AC ending with `(measurable)` is exempt from scenario-mapping per skills/idd-spec/SKILL.md:34.

# Scenarios (BDD)

Scenario: Cold start latency (criterion: 1)
  Given the service has just started
  When the first request arrives
  Then it returns within budget

# Acceptance Criteria

1. Cold start serves the first request without error.
2. p99 latency stays under 200ms (measurable)

# Negative Requirements

- MUST NOT serve stale cached responses past their TTL.
