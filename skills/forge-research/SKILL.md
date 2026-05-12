---
name: forge-research
description: Codebase + external library discovery before spec; emits RESEARCH.md with mode-aware citations. Use when /forge:do (full tier) routes here after /forge:refine, or when a standard-tier feature was seeded with --research.
allowed-tools: Bash, Read, Write, Edit, Task, mcp__context7__resolve-library-id, mcp__context7__query-docs, WebSearch
disable-model-invocation: true
---

# FORGE Research

## When this skill applies

Active feature `state.json` has `current_phase == "research"` and
`phases.research.status == "in_progress"`. Two entry paths:

- **Full tier.** `/forge:refine` completed and seeded `current_phase` to
  `research` (the routing phase list ordered by `/forge:do --full`).
- **Standard tier with `--research`.** `/forge:do --standard --research
  "<idea>"` seeded `routing.phase_list` with research at index 0.

Focused tier never reaches this skill — `/forge:research` refuses with
the literal hint
`Research escalates to standard tier. Use /forge:do --standard --research "<idea>".`

## Inputs

- `--feature <id>` — required. Resolves the feature folder under
  `.forge/features/<id>/`.
- `--skip "<reason>"` — optional. Records a skip entry per master §11.2
  and proceeds directly to `/forge:spec` without dispatching the
  research subagent.
- Refined idea source precedence:
  1. `state.json.refined_idea` — set by `/forge:refine` on full-tier
     features. Use verbatim.
  2. `state.json.routing.idea` — fallback on standard tier (no refine
     phase ran).
  3. Neither present → abort with the hint to re-run `/forge:refine` or
     `/forge:do`.

## Allowed-tools rationale

The `mcp__context7__*` and `WebSearch` entries are listed because the
**research subagent dispatched in step 4** invokes them directly per
spec §5.3.10. The Python helper modules under `tools.research` do NOT
call MCP or WebSearch — that boundary is enforced by
`tests/regression/test_no_mcp_imports.py`.

## Steps

### 1. Validate state.

Read `state.json`. Abort if `current_phase != "research"` or
`phases.research.status != "in_progress"`. Resolve the refined-idea
text via the precedence chain documented under "Inputs". On the skip
path (`--skip "<reason>"`), branch to step 9 immediately after logging
the skip entry.

### 2. Constitution preflight.

Call `tools.constitution.load_and_filter(repo_root,
idea_text=<refined>, files_in_scope=[])`. When `articles[]` is
non-empty, include the serialized list in the research subagent's
dispatch budget under the `articles` field so the subagent can flag
Constitution-relevant risks in step 5e. A preflight failure aborts the
skill with the validator output (matches existing spec/plan preflight
behavior).

### 3. Initialize RESEARCH.md.

Copy `templates/feature/RESEARCH.md` to
`.forge/features/<id>/RESEARCH.md` if absent. Frontmatter:

- `spec: <feature-id>`
- `status: in_progress`
- `tier: <full|standard>` (read from `state.json.tier`)
- `parallel_used: false`
- `research_grounding: <pending — set in step 7>`

### 4. Dispatch ONE research subagent.

Use `forge-subagent-dispatch` with the budget block below. RESEARCH.md
is the **sole writable file**.

**Required prompt prefix.** The dispatch prompt MUST start with a
top-level `context_budget:` block at column 0 (outside any fenced
code block). The PreToolUse hook (`hooks/check_budget.py`) parses
the block with `json.loads` and refuses dispatches that omit the
marker, lack `files_in_scope`, or leave `forbidden` empty. Canonical
shape — copy verbatim and substitute the bracketed values:

```text
context_budget:
{
  "spec_sections": [],
  "files_in_scope": [
    ".forge/features/<id>/RESEARCH.md"
  ],
  "read_only_files": [
    "<each ecosystem manifest resolved by tools.research.ecosystem.detect()>",
    ".forge/CONSTITUTION.md",
    ".forge/external-docs/**"
  ],
  "scan_roots": ["."],
  "intel_files": [],
  "refined_idea": "<verbatim refined-idea text>",
  "forbidden": [
    "do not edit code",
    "do not write outside .forge/features/<id>/RESEARCH.md",
    "do not dispatch additional subagents"
  ],
  "articles": [ <output of Article.to_budget_dict() for each filtered article, or [] when CONSTITUTION.md is absent> ],
  "mcp_tools_allowed": [
    "mcp__context7__resolve-library-id",
    "mcp__context7__query-docs",
    "WebSearch"
  ],
  "return_format": {
    "sections": ["codebase_findings", "external_docs", "domain_notes", "risks"],
    "max_words": 800,
    "extra": {
      "grounding_probe": {
        "context7_callable": "bool",
        "websearch_present": "bool"
      },
      "lookup_results": "list[{library: str, source: enum[context7, websearch, byod, none], snippet_refs: list[str]}]",
      "libraries_extracted": "list[str]"
    }
  }
}

[task prose follows here, starting with a blank line]
```

The `read_only_files` placeholder
`<each ecosystem manifest resolved by tools.research.ecosystem.detect()>`
is deliberate — manifests are a runtime detection result, never a
hand-rolled inventory in skill prose. The block is JSON (not YAML)
because the hook parses with stdlib `json.loads`; the YAML-shaped
predecessor was unparseable to the hook and would be refused.

### 5. Subagent runs the four-section research.

5a. **Codebase findings.** Subagent invokes
`tools.research.project_scan.scan(repo_root)` via Bash; the helper
delegates to `tools.research.ecosystem.detect()` and the matched
per-ecosystem plugin under `tools.research.ecosystems`. Returns a
structured summary `{ecosystems, entrypoints, top_modules,
declared_deps}`. Subagent narrates layout + relevance to the refined
idea atop that summary; polyglot repos surface as separate sections.

5b. **Library-name extraction.** Subagent emits a deduped, normalized
candidate-library list via three signals: (1) regex over the
refined-idea text for capitalized identifiers and back-tick-fenced
names, (2) cross-check against `declared_deps` from 5a, (3) the
matched plugin's `scan_imports(repo_root) -> [str]` method. Normalize
via `tools.research.library_extract.normalize` (lower-case,
hyphen↔underscore unified). Generic ecosystem (no plugin matched)
skips the import scan and auto-injects an `unknown_ecosystem` WARN
into the Risks section.

5c. **External docs.** For each library in `libraries_extracted`, the
subagent invokes the agent-side MCP tools directly:
`mcp__context7__resolve-library-id` then `mcp__context7__query-docs`.
Snippet refs `[context7:<library_id>:<snippet_id>]` recorded inline
in the External docs section. Hard cap: ≤5 distinct lookups per
research run. **Python tools never call MCP** — the boundary is
locked by `tests/regression/test_no_mcp_imports.py`.

5d. **Domain notes.** Light glossary candidates surfaced from the
refined idea + project tree. Consumed downstream by
`/forge:domain` via direct read of RESEARCH.md.

5e. **Risks.** Surface contradictions with Constitution articles,
deprecated dependencies, blast-radius hints. **Conditional auto-
injection:** when the External docs section is non-empty AND
`grounding_probe.context7_callable == false`, the subagent appends
the row "external API claims unverified at research time".

### 6. Subagent writes RESEARCH.md atomically.

The subagent's atomic write is the canonical artifact; the main
thread does NOT persist a duplicate summary file. Phase exit is
recorded in `state.json.phases.research.completed_at` by step 9.

### 7. Resolve grounding mode.

Main thread calls
`tools.research.grounding.resolve_mode(probe=<from payload>,
config=<.forge/config.json>, libraries_extracted=<from payload>,
byod_dir=<.forge/external-docs/ if present else None>)`. The function
returns one of `full | degraded | websearch | byod | byod-partial`.
Write the resolved value into the RESEARCH.md frontmatter via an
atomic file rewrite (replace the `<pending — …>` placeholder).

### 8. Self-review.

Run `/forge:validate --target research
.forge/features/<id>/RESEARCH.md`. The validator
(`tools.validate._research_shape.validate_research`) enforces the
frontmatter schema, the four required H1 sections when
`status == "done"`, and the mode-aware citation rule via
`tools.research.citations`. `BLOCK` or `HIGH` findings block phase
exit; `MEDIUM`/`LOW` are advisory.

### 9. Transition phase.

Run the forge-state Bash CLI (do NOT translate to a Python heredoc):

```bash
forge-state complete-phase --feature <id> --phase research
forge-state start-phase    --feature <id> --phase spec
```

Module fallback: `PYTHONPATH=$CLAUDE_PLUGIN_ROOT python3 -m tools.state_cli ...`. Print
`Next: /forge:spec --feature <id>`.

### 10. Surface to user.

Print:

- Path to `RESEARCH.md`.
- Finding counts (codebase / external / domain / risks).
- Resolved `research_grounding`.
- Context7 lookup count consumed (from `lookup_results`).
- Next phase command (`/forge:spec --feature <id>`).

## Failure modes

- **Context7 absent (never installed).** `grounding_probe.context7_callable
  == false`; `tools.research.grounding.resolve_mode` returns
  `degraded` (or `byod` / `byod-partial` / `websearch` per the
  fallback chain). External docs section carries the
  `_Context7 not available_` marker line; risks gain "external API
  claims unverified at research time".
- **Context7 installed but unresponsive.** Per-lookup
  `[context7:UNAVAILABLE]` annotations recorded inline; section still
  written with `Source unavailable` per-entry markers; risks gain
  "partial Context7 grounding — N/M lookups failed".
- **Refined idea empty.** Skill refuses with the hint to re-run
  `/forge:refine` or `/forge:do`.
- **Constitution preflight failure.** Skill aborts with the validator
  output and does NOT dispatch the subagent.

## State writes

- `phases.research.status` — transitions `in_progress -> done` via
  `complete_phase`.
- `phases.spec.status` — transitions `pending -> in_progress` via
  `start_phase`.
- `phases.research.parallel_used` — lazily defaults to `false` on read
  (`tools.state` default for the optional field).

## See also

- `templates/feature/RESEARCH.md` — frontmatter + four-section shape.
- `commands/research.md` — slash spec + grounding-mode summary table.
- `tools.research.ecosystem.detect` — runtime manifest resolver.
- `tools.research.project_scan.scan` — codebase summary helper.
- `tools.research.library_extract.normalize` — canonical library names.
- `tools.research.grounding.resolve_mode` — pure mode resolver.
- `tools.research.citations.validate` — mode-aware citation rule.
- `tools.validate._research_shape.validate_research` — full validator.
- `/forge:spec` — next phase; consumes RESEARCH.md as Context excerpt.
