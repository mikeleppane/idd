---
spec: 2026-05-11-sample
status: done
tier: full
parallel_used: false
research_grounding: degraded
---

# Codebase findings

- Top-level layout: src/, tests/.
- Single entry point: src/main.py.

# External docs

_Context7 not available — research ran in **degraded** mode._
_External library APIs were not verified against authoritative docs._

To enable full grounding, install Context7 MCP server:
  https://github.com/upstash/context7

Or pre-stage docs locally (BYOD): create `.forge/external-docs/<library>.md`
files for each external library and re-run `/forge:research`.

Or enable WebSearch fallback in `.forge/config.json`:
  {"research": {"websearch_fallback": true}}

# Domain notes

- AsyncClient discussion deferred until external docs available.

# Risks surfaced

- External API claims unverified at research time. Plan-phase Verified Deps registry dry-run remains the slopsquatting backstop.
