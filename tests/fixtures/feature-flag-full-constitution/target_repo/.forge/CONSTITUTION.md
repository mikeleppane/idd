---
version: 0.1.0
created: "2026-05-01"
---

# Project Constitution

Sample 5-article constitution for loader tests.

## Article 1 — Secrets via vault only [CRITICAL]

**Rule:** Secrets, API keys, and credentials SHALL be retrieved through the project's vault loader. Hard-coded secrets and inline credentials are forbidden.
**Reference:** OWASP A02:2021, CWE-798
**Rationale:** Hard-coded credentials are the most common cause of public credential leaks.
**Exception:** None.

## Article 2 — Test coverage floor [SHOULD]

**Rule:** New modules SHOULD ship with unit tests covering the documented public surface.
**Reference:** Team consensus 2026-01.
**Rationale:** Untested modules accumulate bugs faster than they accumulate features.
**Exception:** Throwaway scripts under `scripts/` may skip tests with a `decisions.md` entry.

## Article 3 — Repository pattern for data access [CRITICAL]

**Rule:** ORM session calls MUST be confined to the `repository/` layer. Service-layer code calls repository functions, never the session directly.
**Reference:** ADR-2025-03-data-access-layer
**Rationale:** Direct session access in services couples business logic to schema.
**Exception:** None.

## Article 4 — Forbidden deps [SHOULD]

**Rule:** Pinned-vulnerable packages MUST NOT be added to `pyproject.toml` or `package.json`. Verified-Deps section in PLAN.md cites a clean source.
**Reference:** GitHub Advisory Database
**Rationale:** Supply-chain.
**Exception:** Pin with explicit decisions.md entry naming the CVE and mitigation.

## Article 5 — Documentation in commit body [MAY]

**Rule:** Non-trivial commits MAY include a body that explains the why beyond what the diff shows.
**Reference:** Conventional Commits 1.0.
**Rationale:** Helps reviewers without forcing prose for every line.
**Exception:** None.
