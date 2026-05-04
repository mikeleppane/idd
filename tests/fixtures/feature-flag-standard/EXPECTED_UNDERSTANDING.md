---
spec: 2026-05-04-feature-flag-killswitch
ritual: assumptions → adversarial → pre-mortem
generated: 2026-05-04
---

# Confirmed Assumptions

1. Agent assumed: source of truth is a JSON file on disk. User confirmed: yes for v0; DB-backed in M3.
2. Agent assumed: only one flag (`enable_payments`) ships in v0. User confirmed: yes.

# Adversarial Q&A

**Q:** What happens if the flag file is deleted at runtime?
**A:** Treat as flag=off (fail-closed) so payments halt.
**Resolution:** Added Negative Requirement: MUST NOT default to on when source of truth is missing.

# Pre-Mortem (Top Failure Modes)

1. **Mode:** Race between read and write on the JSON file under load.
   **Mitigation:** File-locking via stdlib `fcntl`; documented in slice 1 wave 1.
2. **Mode:** Operator forgets to authenticate flag-toggle endpoint.
   **Mitigation:** Negative Requirement c-neg-2 enforces auth; review subagent verifies in code-review pass.

# Shared Model Statement

We are building a single-flag kill-switch (`enable_payments`) to give operators a safe runtime brake on payment endpoints, knowing the source of truth is a JSON file in v0, explicitly excluding per-tenant scoping and percentage rollouts.
