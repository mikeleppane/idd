---
id: 2026-05-06-scenarios-pass
status: draft
tier: standard
created: 2026-05-06
capability: scenarios-pass
---

# Intent

A SPEC whose scenarios cleanly map to acceptance criteria.

# Scenarios (BDD)

Scenario: Apply coupon at checkout (criterion: 1)
  Given a cart with one item
  When the user applies a valid coupon
  Then the discount appears on the receipt

Scenario: Reject expired coupon (criterion: 2)
  Given an expired coupon code
  When the user submits it
  Then checkout shows an error

# Acceptance Criteria

1. Valid coupon discounts the order total.
2. Expired coupon is rejected with a visible error.

# Negative Requirements

- MUST NOT log raw coupon codes.
