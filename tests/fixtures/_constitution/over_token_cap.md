---
version: 0.1.0
created: "2026-05-01"
---

# Over-Cap Constitution

Fixture sized so cumulative body word count exceeds `MAX_INJECTED_WORDS`
(~1153) before any drop, forcing the hard-cap step in `filter_articles`.
Eight articles, four SHOULD and four MAY, no CRITICAL — so no article is
exempt from the cap. Every title contains "loader" so the relevance filter
scores them all > 0 against `scope_keywords={"loader"}` and the percentile
gate keeps them; only the cap step prunes the surplus.

## Article 1 — Loader rule one [SHOULD]

**Rule:** loader determinism matters because the loader path drives module discovery and the loader contract enforces deterministic behavior; the loader avoids hidden globals; the loader rule constrains import order; the loader binding ensures consistent behavior; loader semantics align with module isolation; loader determinism reduces test flake; loader scope must not exceed module scope; the loader handles configuration explicitly; loader contracts include explicit return shapes; loader idempotency is required; loader observability requires structured logs; loader timeouts must be bounded; loader retries follow exponential backoff; loader caching uses LRU; loader caching uses TTL; loader caching invalidates on config change; loader caching invalidates on schema change; loader caching invalidates on env change; loader caching invalidates on file change; loader caching invalidates on signal; loader caching invalidates on error; loader telemetry counts cache hits; loader telemetry counts cache misses; loader telemetry counts evictions; loader telemetry counts errors and warnings; loader logs cache statistics on shutdown.
**Reference:** team consensus 2026-01 article one.
**Rationale:** loader determinism aids tests and reduces flake.
**Exception:** None.

## Article 2 — Loader rule two [MAY]

**Rule:** loader configuration matters because the loader caching invalidates on config change; loader caching invalidates on schema change; loader caching uses LRU; loader caching uses TTL; loader retries follow exponential backoff; loader timeouts must be bounded; loader observability requires structured logs; loader idempotency is required; loader contracts include explicit return shapes; the loader handles configuration explicitly; loader scope must not exceed module scope; loader determinism reduces test flake; loader semantics align with module isolation; the loader binding ensures consistent behavior; the loader rule constrains import order; the loader avoids hidden globals; the loader contract enforces deterministic behavior; the loader path drives module discovery; loader caching invalidates on env change; loader caching invalidates on file change; loader caching invalidates on signal; loader caching invalidates on error; loader telemetry counts cache hits; loader telemetry counts cache misses; loader telemetry counts evictions; loader telemetry counts errors and warnings; loader logs cache statistics on shutdown.
**Reference:** team consensus 2026-01 article two.
**Rationale:** loader determinism aids tests and reduces flake.
**Exception:** None.

## Article 3 — Loader rule three [SHOULD]

**Rule:** loader retries matter because loader retries follow exponential backoff; loader timeouts must be bounded; loader caching uses TTL; loader caching uses LRU; loader caching invalidates on schema change; loader caching invalidates on config change; the loader handles configuration explicitly; loader contracts include explicit return shapes; loader idempotency is required; loader observability requires structured logs; loader telemetry counts cache hits; loader telemetry counts cache misses; loader telemetry counts evictions; loader telemetry counts errors and warnings; loader logs cache statistics on shutdown; loader semantics align with module isolation; loader determinism reduces test flake; loader scope must not exceed module scope; the loader binding ensures consistent behavior; the loader rule constrains import order; the loader avoids hidden globals; the loader contract enforces deterministic behavior; the loader path drives module discovery; loader caching invalidates on env change; loader caching invalidates on file change; loader caching invalidates on signal; loader caching invalidates on error.
**Reference:** team consensus 2026-01 article three.
**Rationale:** loader retries protect downstream consumers.
**Exception:** None.

## Article 4 — Loader rule four [MAY]

**Rule:** loader caching matters because loader caching uses LRU; loader caching uses TTL; loader caching invalidates on config change; loader caching invalidates on schema change; loader caching invalidates on env change; loader caching invalidates on file change; loader caching invalidates on signal; loader caching invalidates on error; loader telemetry counts cache hits; loader telemetry counts cache misses; loader telemetry counts evictions; loader telemetry counts errors and warnings; loader logs cache statistics on shutdown; loader retries follow exponential backoff; loader timeouts must be bounded; loader observability requires structured logs; loader idempotency is required; loader contracts include explicit return shapes; the loader handles configuration explicitly; loader scope must not exceed module scope; loader determinism reduces test flake; loader semantics align with module isolation; the loader binding ensures consistent behavior; the loader rule constrains import order; the loader avoids hidden globals; the loader contract enforces deterministic behavior; the loader path drives module discovery.
**Reference:** team consensus 2026-01 article four.
**Rationale:** loader caching keeps the hot path cheap.
**Exception:** None.

## Article 5 — Loader rule five [SHOULD]

**Rule:** loader observability matters because loader observability requires structured logs; loader telemetry counts cache hits; loader telemetry counts cache misses; loader telemetry counts evictions; loader telemetry counts errors and warnings; loader logs cache statistics on shutdown; loader retries follow exponential backoff; loader timeouts must be bounded; loader caching uses LRU; loader caching uses TTL; loader caching invalidates on config change; loader caching invalidates on schema change; loader caching invalidates on env change; loader caching invalidates on file change; loader caching invalidates on signal; loader caching invalidates on error; loader idempotency is required; loader contracts include explicit return shapes; the loader handles configuration explicitly; loader scope must not exceed module scope; loader determinism reduces test flake; loader semantics align with module isolation; the loader binding ensures consistent behavior; the loader rule constrains import order; the loader avoids hidden globals; the loader contract enforces deterministic behavior; the loader path drives module discovery.
**Reference:** team consensus 2026-01 article five.
**Rationale:** loader observability aids triage.
**Exception:** None.

## Article 6 — Loader rule six [MAY]

**Rule:** loader idempotency matters because loader idempotency is required; loader contracts include explicit return shapes; the loader handles configuration explicitly; loader scope must not exceed module scope; loader determinism reduces test flake; loader semantics align with module isolation; the loader binding ensures consistent behavior; the loader rule constrains import order; the loader avoids hidden globals; the loader contract enforces deterministic behavior; the loader path drives module discovery; loader caching uses LRU; loader caching uses TTL; loader caching invalidates on config change; loader caching invalidates on schema change; loader caching invalidates on env change; loader caching invalidates on file change; loader caching invalidates on signal; loader caching invalidates on error; loader retries follow exponential backoff; loader timeouts must be bounded; loader observability requires structured logs; loader telemetry counts cache hits; loader telemetry counts cache misses; loader telemetry counts evictions; loader telemetry counts errors and warnings; loader logs cache statistics on shutdown.
**Reference:** team consensus 2026-01 article six.
**Rationale:** loader idempotency makes retries safe.
**Exception:** None.

## Article 7 — Loader rule seven [SHOULD]

**Rule:** loader timeouts matter because loader timeouts must be bounded; loader retries follow exponential backoff; loader caching uses LRU; loader caching uses TTL; loader caching invalidates on config change; loader caching invalidates on schema change; loader caching invalidates on env change; loader caching invalidates on file change; loader caching invalidates on signal; loader caching invalidates on error; loader observability requires structured logs; loader telemetry counts cache hits; loader telemetry counts cache misses; loader telemetry counts evictions; loader telemetry counts errors and warnings; loader logs cache statistics on shutdown; loader idempotency is required; loader contracts include explicit return shapes; the loader handles configuration explicitly; loader scope must not exceed module scope; loader determinism reduces test flake; loader semantics align with module isolation; the loader binding ensures consistent behavior; the loader rule constrains import order; the loader avoids hidden globals; the loader contract enforces deterministic behavior; the loader path drives module discovery.
**Reference:** team consensus 2026-01 article seven.
**Rationale:** loader timeouts contain blast radius.
**Exception:** None.

## Article 8 — Loader rule eight [MAY]

**Rule:** loader scope matters because loader scope must not exceed module scope; loader determinism reduces test flake; loader semantics align with module isolation; the loader binding ensures consistent behavior; the loader rule constrains import order; the loader avoids hidden globals; the loader contract enforces deterministic behavior; the loader path drives module discovery; loader caching uses LRU; loader caching uses TTL; loader caching invalidates on config change; loader caching invalidates on schema change; loader caching invalidates on env change; loader caching invalidates on file change; loader caching invalidates on signal; loader caching invalidates on error; loader retries follow exponential backoff; loader timeouts must be bounded; loader observability requires structured logs; loader telemetry counts cache hits; loader telemetry counts cache misses; loader telemetry counts evictions; loader telemetry counts errors and warnings; loader logs cache statistics on shutdown; loader idempotency is required; loader contracts include explicit return shapes; the loader handles configuration explicitly.
**Reference:** team consensus 2026-01 article eight.
**Rationale:** loader scope keeps modules isolated.
**Exception:** None.
