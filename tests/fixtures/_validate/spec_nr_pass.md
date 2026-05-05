---
id: 2026-05-04-coupon-redemption
status: draft
tier: standard
created: 2026-05-04
capability: coupon-redemption
---

# Intent
Allow checkout to apply coupons.

# Scope
## In scope
- Apply one coupon per order.

## Out of scope (Non-goals)
- Stackable coupons.

# Negative Requirements

- NR-1: The system SHALL NOT log raw coupon codes.
- NR-2: The system MUST NOT call the external registry from the critical path.
