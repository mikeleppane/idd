---
capability: feature-flag
status: shipped
created: "2026-05-04"
last_updated: "2026-05-04"
evidence:
  - 2026-05-04-initial-feature: features/archive/2026-05-04-initial-feature/
bounded_context: null
---

# Feature flag — Capability spec

## Intent

Provide a runtime mechanism to enable or disable features by ID, with
sensible defaults when no override is set.

## Scope

In scope: server-side feature gating by string flag ID. Out of scope:
client-side flag delivery, percent-based rollout (not yet implemented).

## Domain

Feature flags are referenced by lowercase-hyphen ID and resolve to a
boolean. Resolution order: in-process override > env var > default.

## Scenarios

### scenario-1: known flag returns override

- Given a feature flag `foo` with override `true`
- When code reads the flag
- Then resolution returns `true`

### scenario-2: unknown flag returns default

- Given no override for `bar`
- When code reads the flag
- Then resolution returns the configured default

scenario-3: percent rollout
  - Given a feature flag `foo` with percent rollout `50`
  - When 1000 unique users read the flag
  - Then approximately 500 receive `true` (within ±5% tolerance)

## Acceptance Criteria

- criterion-1: flags resolve in O(1) from the override table
- criterion-2: env var overrides are read once at process start

## Negative Requirements

- NR-1: feature flags MUST NOT be persisted to disk by the runtime resolver
- NR-2: flag IDs MUST be lowercase-hyphen — uppercase or whitespace rejected at registration

## Decisions

- D-1: flag IDs are case-sensitive (lowercase canonical) for deterministic lookup
