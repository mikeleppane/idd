---
capability: feature-flag
status: shipped
created: 2026-05-04
last_updated: 2026-05-04
evidence:
  - 2026-05-04-feature-flag-killswitch: features/archive/2026-05-04-feature-flag-killswitch/
bounded_context: null
---

# Feature Flag

## Intent

Operators disable cost-sensitive endpoints at runtime without redeploying.

## Scope

In scope: single boolean flag `enable_payments`, file-backed source of truth, 503 / 200 routing.
Out of scope: per-tenant scoping, percentage rollouts, flag UI.

## Domain

| Term | Definition |
|------|------------|
| Flag | Named boolean toggle. |
| Source of truth | The single store of flag state. |

## Scenarios

```gherkin
Scenario: Disabled flag blocks payments
  Given the flag enable_payments is off
  When a payment is requested
  Then the response is HTTP 503

Scenario: Enabled flag allows payments
  Given the flag enable_payments is on
  When a payment is requested
  Then the response is HTTP 200
```

## Acceptance Criteria

- Disabled flag returns 503 on payment endpoints.
- Enabled flag returns 200.
- Flag state restored from source of truth on boot.

## Negative Requirements

- MUST NOT cache flag state across the change boundary.
- MUST NOT allow toggles via unauthenticated endpoints.
- MUST NOT default to on when source of truth is missing.

## Decisions

See `features/archive/2026-05-04-feature-flag-killswitch/decisions.md`.
