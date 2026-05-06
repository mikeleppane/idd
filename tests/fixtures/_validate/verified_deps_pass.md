---
spec: 2026-05-06-verified-deps-pass
slices: 1
status: ready
---

# Slice 1: example

**Goal:** Add jose-backed token issuance.
**Files in scope:** pkg/auth.py
**Acceptance:** crit-1

## Verified Dependencies

| Package | Version / range | Registry | Source checked | Key APIs used | Notes |
|---|---|---|---|---|---|
| `jose` | 5.2.x | npm | npmjs.com + jose docs | `SignJWT`, `jwtVerify` | Constitution Art. 3 |
| `bcrypt` | 5.1.1 | npm | npmjs.com + maintainer repo | `hash`, `compare` | cost=12 |
