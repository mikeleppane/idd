---
version: 0.1.0
created: "2026-01-01"
---

# Project Constitution

Project-wide guidance authored by the team. Articles below are surfaced to spec/plan/execute/review subagents as advisory context (M3) and to the reviewer subagent as severity hints.

## Article 1 — Secrets via vault only [CRITICAL]

**Rule:** Secrets, API keys, and credentials SHALL be retrieved through the project's vault loader. Hard-coded secrets, `.env` reads outside the loader, and inline credentials are forbidden.
**Reference:** OWASP A02:2021, CWE-798
**Rationale:** Hard-coded credentials are the most common cause of public credential leaks.
**Exception:** None.

## Article 2 — Test coverage floor [SHOULD]

**Rule:** New modules SHOULD ship with unit tests covering at least the documented public surface.
**Reference:** Team consensus 2026-01.
**Rationale:** Untested modules accumulate bugs faster than they accumulate features.
**Exception:** Throwaway scripts under `scripts/` may skip tests with a `decisions.md` entry.
