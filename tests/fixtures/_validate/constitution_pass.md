---
version: 0.1.0
created: 2026-01-01
---

# Project Constitution

## Article 1 — Secrets via vault only [CRITICAL]
**Rule:** Secrets SHALL be retrieved through the vault loader.
**Reference:** OWASP A02:2021
**Rationale:** Hard-coded credentials cause leaks.
**Exception:** None.

## Article 2 — Test coverage floor [SHOULD]
**Rule:** Public functions SHOULD ship with tests.
**Reference:** Team consensus 2026-01.
**Rationale:** Undocumented behavior rots.
**Exception:** Throwaway scripts may skip tests.
