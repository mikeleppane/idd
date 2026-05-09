---
name: domain
description: Author DOMAIN.md (glossary, bounded contexts, aggregates, invariants, open questions) for full-tier features and update SPEC.md `# Domain` to a pointer. Use when /forge:do (full tier) advances to the domain phase, or when the user invokes /forge:domain directly.
argument-hint: "[--feature <id>]"
model: sonnet
---

# /forge:domain

Full-tier-only phase that authors the canonical `DOMAIN.md` for a feature
and replaces `SPEC.md`'s `# Domain` body with a one-line pointer to that
file. Runs after `/forge:spec` and before `/forge:scenarios`; transitions
`current_phase` from `domain` to `scenarios`.

## Args

- `--feature <id>` — target feature folder under `.forge/features/<id>/`.
  Optional; when omitted, the single-active rule resolves the unique
  unshipped feature.

## Behavior

1. Resolves the active feature and guards `current_phase == "domain"` plus
   `tier == "full"`.
2. Reads `SPEC.md` Intent + Scenarios plus `pyproject.toml`/`package.json`
   for ecosystem hints; extracts 4–8 domain-load-bearing terms.
3. Writes `.forge/features/<id>/DOMAIN.md` from
   `templates/feature/DOMAIN.md` — frontmatter (`status: draft`, `version:
   0.1.0`), glossary, bounded-contexts placeholder, aggregates, invariants,
   open questions.
4. Updates `SPEC.md` `# Domain` in place: keeps the heading, replaces the
   body with a one-line pointer to `DOMAIN.md`.
5. Self-reviews term coverage; on unresolvable terms in auto mode, logs a
   deviation to `decisions.md` and `state.json.deviations`.
6. Transitions phase to `scenarios` via `complete_phase` + `start_phase`,
   then prints `next: /forge:scenarios`.

## See also

- `skills/forge-domain/SKILL.md` — full lifecycle.
- `templates/feature/DOMAIN.md` — artifact contract.
- `/forge:spec` — prior phase; ships the `# Domain` placeholder.
- `/forge:scenarios` — next phase; consumes DOMAIN.md.
