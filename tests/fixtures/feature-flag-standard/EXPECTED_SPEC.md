---
id: 2026-05-04-feature-flag-killswitch
status: draft
tier: standard
created: 2026-05-04
capability: feature-flag
---

# Feature Flag Kill-Switch

## Intent

Operators need to disable cost-sensitive endpoints (payments, refunds) at runtime without redeploying. The kill-switch is the foundation for safe production toggles.

## Context

Demo app currently has no runtime configuration mechanism beyond environment variables (read at boot only). Adding a tiny flag registry unlocks safer launches.

## Domain

| Term | Definition |
|------|------------|
| Flag | Named boolean toggle (`enable_payments`). |
| Operator | A human with admin access who toggles flags. |
| Source of truth | The single store of flag state (file, table, or KV). |

## Codebase Anchors

- `src/feature_flag.py:is_enabled` — main entry point.
- `tests/features/feature_flag.feature` — executable scenarios target.

## Scope

In scope:
- Boolean flag named `enable_payments` with on/off transitions.
- Source of truth survives restart.
- 503 response when flag is off.

Out of scope:
- Per-tenant flag scoping.
- Percentage rollouts.
- Flag UI.

## Scenarios

(emitted by /idd:scenarios — markdown Gherkin here, executable .feature mirror at tests/features/feature_flag.feature)

```gherkin
Scenario: Disabled flag blocks payments  (criterion: c-1)
  Given the flag enable_payments is off
  When a payment is requested
  Then the response is HTTP 503

Scenario: Enabled flag allows payments  (criterion: c-2)
  Given the flag enable_payments is on
  When a payment is requested
  Then the response is HTTP 200
```

## Test Strategy

| Criterion | Method | Target |
|-----------|--------|--------|
| c-1 | scenario | tests/features/feature_flag.feature |
| c-2 | scenario | tests/features/feature_flag.feature |
| c-3 | unit | tests/test_feature_flag.py::test_state_persists |

## Acceptance Criteria

- c-1: When `enable_payments` is off, payment endpoints return 503.
- c-2: When `enable_payments` is on, payment endpoints return 200.
- c-3: Flag state is restored from the source of truth on boot.

## Negative Requirements

- MUST NOT cache flag state in process memory beyond one request lifetime when the source of truth changes.
- MUST NOT allow flag toggles via unauthenticated endpoints.

## Open Questions

(none after spec phase exits)
