# Decisions Log

> Append-only ADR-lite. One decision per section. Latest at the bottom.

## 2026-05-05 - Skipped optional cache layer for v1

**Context:** Cache adds operational complexity not justified at current scale.

**Decision:** Defer cache layer to v2.

**Consequences:** Hot endpoints still hit DB; acceptable given current QPS.

**Alternatives considered:** Ship cache now (rejected — premature optimization).
