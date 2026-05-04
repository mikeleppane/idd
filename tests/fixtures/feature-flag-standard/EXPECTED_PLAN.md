---
spec: 2026-05-04-feature-flag-killswitch
slices: 2
status: ready
---

# Verified Dependencies

_None._

# Slice 1: Flag store + read API
**Goal:** Operators can read flag state via `is_enabled("enable_payments")`.
**Spec sections:** Intent, c-1, c-3
**Files in scope:** src/feature_flag.py, src/flag_store.py, tests/test_feature_flag.py

**Wave 1 (parallel):**
- [ ] Task 1.1.1 — Implement `flag_store.FileBackedStore` with `get(name)` / `set(name, bool)`.
- [ ] Task 1.1.2 — Implement `feature_flag.is_enabled(name)` reading the store.

**Wave 2 (sequential after Wave 1):**
- [ ] Task 1.2.1 — Wire boot-time restoration test for c-3.

**Acceptance:** c-1 unit-tested, c-3 unit-tested.

# Slice 2: HTTP 503 / 200 behavior
**Goal:** Payment endpoints honor the flag.
**Spec sections:** Scenarios, c-1, c-2
**Files in scope:** src/payments.py, tests/test_payments.py, tests/step_defs/test_feature_flag_steps.py, tests/features/feature_flag.feature

**Wave 1 (parallel):**
- [ ] Task 2.1.1 — Add `payments.charge()` returning 503 when flag off, 200 when on.

**Wave 2 (sequential after Wave 1):**
- [ ] Task 2.2.1 — Wire pytest-bdd step definitions in tests/step_defs/test_feature_flag_steps.py against the handler from 2.1.1.

**Acceptance:** Scenarios pass; c-1 and c-2 EVIDENCED via scenario-exec.
