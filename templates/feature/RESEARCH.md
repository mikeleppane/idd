---
spec: <YYYY-MM-DD-slug>
status: in_progress
tier: <focused|standard|full>
parallel_used: false
research_grounding: <full|degraded|websearch|byod|byod-partial>
---

# Codebase findings

> ≤8 bullets. Top-level repo layout, modules touched by the refined idea, and
> extension points the implementation will lean on. Narrate atop the structured
> summary returned by `tools.research.project_scan.scan(repo_root)`; do not
> paste the JSON.

- <bullet>

# External docs

> Library-by-library notes. Every paragraph that names an external API symbol
> in backticks MUST carry a `[context7:<library_id>:<snippet_id>]` citation
> (full mode), or the per-mode equivalent (`[byod:<lib>:<section-anchor>]`,
> `[websearch:<url>]`). The `tools.validate --target research` gate enforces
> this rule mode-aware.

- <library>: <one-paragraph summary with citation>

# Domain notes

> Light glossary candidates surfaced from the project tree + refined idea.
> Consumed by `/forge:domain` downstream — keep it terse; the dedicated phase
> populates DOMAIN.md.

- <term>: <one-line gloss>

# Risks surfaced

> Bullets covering blast-radius concerns, deprecated dependencies, and
> Constitution-relevant flags. The subagent auto-injects an "external API
> claims unverified" risk when grounding resolves to a degraded variant AND
> External docs is non-empty.

- <risk>

<!--
Degraded-mode replacement for the External docs section. When the resolved
`research_grounding` is `degraded`, copy this fragment verbatim into the
External docs section above (replacing the per-library notes). The validator
requires the literal `_Context7 not available_` marker line.

# External docs

_Context7 not available — research ran in **degraded** mode._
_External library APIs were not verified against authoritative docs._

To enable full grounding, install Context7 MCP server:
  https://github.com/upstash/context7

Or pre-stage docs locally (BYOD): create `.forge/external-docs/<library>.md`
files for each external library and re-run `/forge:research`.

Or enable WebSearch fallback in `.forge/config.json`:
  {"research": {"websearch_fallback": true}}
-->
