# Decisions Log

> Append-only ADR-lite. One decision per section. Latest at the bottom.

## 2026-05-05 — Bumped pytest minimum to 8.2 (phase=execute)

**Context:** Coverage plugin needed pytest 8.2.

**Decision:** Bump pytest constraint to 8.2.

**Consequences:** Older Python 3.10 envs no longer supported.

**Alternatives considered:** Pin coverage plugin to older version (rejected — security CVE).

## 2026-05-05 — Switched bundler (phase=research)

**Context:** Research-phase exploration of bundler options.

**Decision:** Adopt esbuild for now.

**Consequences:** Faster builds, smaller toolchain.

**Alternatives considered:** Webpack (rejected — config bloat).
