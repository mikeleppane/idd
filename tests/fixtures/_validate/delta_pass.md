---
id: 2026-05-04-add-stripe-webhook
affects_capability: stripe-integration
status: draft
created: 2026-05-04
---

# Change: Add Stripe webhook for refund events

## Affects

- spec: stripe-integration — sections [Scenarios, Acceptance Criteria]

## Delta

+ ADD: scenario "Refund webhook applied to order"
- REMOVE: criterion 4
~ MODIFY: criterion 2 — was "synchronous capture", now "asynchronous capture via webhook"

## Rationale

Stripe deprecated synchronous capture endpoints; we must migrate before the cutoff.
