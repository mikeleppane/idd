---
version: 0.1.0
created: "2026-05-01"
---

# No-Critical Constitution

Fixture used by `test_filter_articles_no_critical_path`. All articles are SHOULD
or MAY so the filter must still return a non-empty kept list when no CRITICAL
articles exist.

## Article 1 — Lint clean before merge [SHOULD]

**Rule:** Pull requests SHOULD pass `make lint` before merge.
**Reference:** Team consensus 2026-01.
**Rationale:** Lint clean diffs reduce review noise.
**Exception:** Hotfix branches may merge with a follow-up cleanup ticket.

## Article 2 — Conventional commit subjects [MAY]

**Rule:** Commit subjects MAY follow Conventional Commits.
**Reference:** Conventional Commits 1.0.
**Rationale:** Helps changelog automation without forcing prose.
**Exception:** None.

## Article 3 — Type-annotate new public functions [SHOULD]

**Rule:** New public functions SHOULD carry full type annotations.
**Reference:** PEP 484.
**Rationale:** Annotations document intent and enable static checks.
**Exception:** Throwaway scripts under `scripts/` are exempt with a decisions entry.
