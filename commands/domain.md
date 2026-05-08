---
name: domain
description: Glossary table + optional Mermaid sketch for full-tier feature SPEC.md. Use when /forge:do (full tier) advances to the domain phase, or when the user invokes /forge:domain directly.
argument-hint: "[--feature <id>]"
model: sonnet
---

# /forge:domain

Full-tier-only phase that fills the `# Domain` section of `SPEC.md` with a
4–8-entry glossary table (and an optional Mermaid sketch) drawn from the
spec's Intent and Scenarios. Runs after `/forge:spec` and before
`/forge:scenarios`; transitions `current_phase` from `domain` to `scenarios`.

## Args

- `--feature <id>` — target feature folder under `.forge/features/<id>/`.
  Optional; when omitted, the single-active rule resolves the unique
  unshipped feature.

## Behavior

1. Resolves the active feature and guards `current_phase == "domain"`.
2. Reads `SPEC.md` Intent + Scenarios plus `pyproject.toml`/`package.json`
   for ecosystem hints; extracts 4–8 domain-load-bearing terms.
3. Rewrites `SPEC.md` `# Domain` in-place with a `Term | Definition | Example`
   table; offers an optional Mermaid sketch.
4. Self-reviews term coverage; on unresolvable terms in auto mode, logs a
   deviation to `decisions.md` and `state.json.deviations`.
5. Transitions phase to `scenarios` via `complete_phase` + `start_phase`,
   then prints `next: /forge:scenarios`.

## See also

- `skills/forge-domain/SKILL.md` — full lifecycle.
- `/forge:spec` — prior phase; ships the `# Domain` placeholder.
- `/forge:scenarios` — next phase; consumes the populated `# Domain`.
