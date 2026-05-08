---
id: <YYYY-MM-DD-slug>
status: draft
tier: <focused|standard|full>
created: <YYYY-MM-DD>
capability: <stable-capability-handle>
---

# Intent

> One paragraph. WHY this exists. The problem being solved. The user/business outcome pursued.
> Replace this blockquote with the real intent statement before exiting the spec phase.

# Context

> Background, constraints, prior art. Link to RESEARCH.md if it exists for this feature.

# Domain

> Ubiquitous-language glossary. Add or remove rows as needed. Keep examples concrete.

| Term | Definition | Example |
|---|---|---|
| <Term> | <Definition> | <Example sentence using the term> |

> Optional: Mermaid sketch of key concepts. Add only if it clarifies. Delete this line if unused.

# Codebase Anchors

> Concrete pointers into the existing codebase: modules, files, types, or symbols this feature
> touches or depends on. Lets a subagent find context without reading the whole repo.

- `path/to/module.py:Symbol` — <one-line role>
- `path/to/another.py` — <one-line role>

# Scope

## In scope

- <behavior-level bullet>
- <behavior-level bullet>

## Out of scope (Non-goals)

- <explicit exclusion>
- <explicit exclusion>

# Scenarios (BDD)

```gherkin
Scenario: <name>
  Given <precondition>
  When <action>
  Then <observable outcome>
```

# Test Strategy

> What kinds of tests will prove acceptance? Map criteria to test type (unit / integration / scenario / UAT).

| Criterion | Test type | Where it will live |
|---|---|---|
| crit-1 | <unit \| integration \| scenario \| UAT> | <path/to/test_or_uat_log> |

# Acceptance Criteria

1. <falsifiable criterion mapping to Scenario 1 or to a measurable outcome>
2. <criterion>
3. <criterion>

# Negative Requirements

> Explicit MUST-NOT statements. These guard against scope creep, regression, and silent feature
> additions during execute. Each negative requirement is verified by `/forge:verify` like a regular criterion.

- MUST NOT <behavior the feature explicitly excludes>
- MUST NOT <behavior the feature explicitly excludes>

# Open Questions

> Numbered list. Each question must be resolved (or accepted as out-of-scope) before exiting the spec phase.

1. <open question>

# Decisions

See `decisions.md` (append-only).
