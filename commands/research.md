---
name: research
description: Codebase + external library discovery before /forge:spec. Use when /forge:do (full tier) routes here after /forge:refine, or when the user invokes /forge:research directly on a standard-tier feature seeded with --research.
argument-hint: "--feature <id> [--skip \"<reason>\"]"
model: sonnet
---

# /forge:research

Pre-spec phase that emits `RESEARCH.md` with codebase findings, external
library notes, domain glossary candidates, and risks. External-docs
citations are mode-aware — the resolved `research_grounding` value
(written to the frontmatter at exit) drives which citation form the
validator accepts.

## Args

- `--feature <id>` — required. Target feature folder under
  `.forge/features/<id>/`.
- `--skip "<reason>"` — optional. Records a skip entry per master §11.2
  and proceeds directly to `/forge:spec`. Verify-phase warning surfaces
  at ship.

## Tier rules

- **Full tier.** Auto-runs after `/forge:refine` completes (`/forge:do
  --full` seeds `routing.phase_list` with research included).
- **Standard tier.** Requires `--research` on the routing command:
  `/forge:do --standard --research "<idea>"`. Without the flag the
  standard tier skips straight to `/forge:spec`.
- **Focused tier.** Refused with the literal hint
  `Research escalates to standard tier. Use /forge:do --standard --research "<idea>".`

## Output

On success the skill prints:

- Path to `RESEARCH.md`.
- Resolved grounding mode (one of `full | degraded | websearch | byod |
  byod-partial`).
- Context7 lookup count consumed during the run.
- Next phase command: `/forge:spec --feature <id>`.

## Grounding modes

| Mode | When | Citation format |
|---|---|---|
| `full` | Context7 MCP installed and responsive. | `[context7:<library_id>:<snippet_id>]` |
| `degraded` | Context7 absent or unresponsive; no fallback configured. | _none — body must carry the unavailable marker_ |
| `websearch` | `.forge/config.json` enables `research.websearch_fallback` AND WebSearch tool present. | `[websearch:<url>]` |
| `byod` | All extracted libraries covered by user-staged `.forge/external-docs/<lib>.md`. | `[byod:<lib>:<section-anchor>]` |
| `byod-partial` | Some libraries covered by BYOD docs; others missing. | mixed (byod for covered libs, degraded marker for the rest) |

## See also

- `templates/feature/RESEARCH.md` — frontmatter + section shape.
- `skills/forge-research/SKILL.md` — full lifecycle.
- `tools.research.grounding.resolve_mode` — pure mode resolver.
- `tools.validate._research_shape.validate_research` — frontmatter +
  section + citation gate.
- `/forge:spec` — next phase; consumes RESEARCH.md as Context excerpt.
