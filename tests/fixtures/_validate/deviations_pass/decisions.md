# Decisions Log

> Append-only ADR-lite. One decision per section. Latest at the bottom.

## 2026-05-05 — Pin Postgres driver to 14.7 (phase=execute)

**Context:** Postgres driver pinned to 14 caused planner regression on JSON path queries during execute phase.

**Decision:** Pin to 14.7 and add a regression test covering the JSON path query.

**Consequences:** Builds slow by ~2s; planner stays stable.

**Alternatives considered:** Driver 15 (rejected — major version bump out of scope).
