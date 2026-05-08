---
name: forge-domain
description: Glossary table + optional Mermaid sketch for full-tier feature SPEC.md. Use when /forge:do (full tier) advances to the domain phase, or when the user invokes /forge:domain directly.
model: sonnet
---

# FORGE Domain

## When this skill applies

`/forge:do` (full tier) advanced state through `/forge:spec` and the `_FULL_NEXT`
map flipped `current_phase` to `domain`, OR the user invoked
`/forge:domain [--feature <id>]` directly against an active feature whose
`current_phase` is already `domain`. Full-tier only — focused and standard
tiers fill `# Domain` at spec time and never enter this phase.

## Inputs

- `--feature <id>` — optional. Single-active rule applies: when omitted,
  resolve the only feature whose `state.json.current_phase != "done"`;
  abort if zero or multiple match.
- `SPEC.md` (read-only) — pulls `# Intent` and `# Scenarios` sections as the
  term-extraction corpus. The `# Domain` section is left as the in-place
  rewrite target only; not used as input.
- Project signals — `pyproject.toml` and/or `package.json` at repo root, read
  once for ecosystem hints (Python vs JS/TS, framework names) so the glossary
  picks the right vocabulary register.

## Steps

1. **Resolve feature.** Read `--feature <id>` or apply the single-active rule
   to find `.forge/features/<id>/`. Read `state.json`.
2. **Guard phase and tier.** Require `current_phase == "domain"`. Otherwise
   abort with `StateError("domain phase only valid on full tier after
   /forge:spec completes")`. **Tier guard:** also require
   `state.json.tier == "full"`. Although `start_phase` accepts `domain` for
   any tier, this skill is full-tier only — focused and standard tiers fill
   `# Domain` at spec time. If `tier != "full"`, abort with
   `StateError("/forge:domain is full-tier only; current tier is '<X>' — "
   "fill # Domain at /forge:spec time instead")` and do NOT mutate SPEC.md.
3. **Read SPEC.** Open `SPEC.md`. Extract the `# Intent` and `# Scenarios`
   sections verbatim (read-only). Do not mutate the spec yet.
   **Fence-aware section scan (per P5 T11 H1 lesson):** when locating
   `# Intent`, `# Scenarios`, or `# Domain` headers, mask out fenced code
   blocks (` ``` ` and ` ~~~ ` delimited regions) before matching — fenced
   examples that contain literal `# Header` lines must not shadow real H1
   sections. Mirror the masking helper in `tools.validate.spec_structural`
   (see `_strip_code` / `_mask_fenced_lines`) rather than rolling a naive
   `re.search(r"^# Domain", ...)` regex.
4. **Project signals.** Read `pyproject.toml` (or `package.json`) for
   ecosystem hints — language, framework, declared dependencies. These bias
   which terms count as "generic" vs "domain" (e.g., `request` is generic in
   a web app, domain-load-bearing in a billing system).
5. **Extract candidate terms.** Scan Intent + Scenarios. A term qualifies
   when it appears **2+ times AND** carries domain semantics. Drop generic
   words (`user`, `system`, `request`, `feature` unless ecosystem signal
   promotes one).
6. **Draft glossary table.** Render a Markdown table with columns
   `Term | Definition | Example`. Aim for **4–8 entries**; prune below 8 by
   merging near-synonyms; expand toward 4 by demoting under-used terms.
7. **Optional Mermaid sketch.** Offer the user an optional `mermaid` block
   sketching key concept relationships (one diagram, ≤8 nodes). User opts
   in; if declined, omit the block entirely. Mermaid is optional — never
   force.
8. **Edit SPEC.md `# Domain` in-place.** Replace the placeholder body of the
   `# Domain` section with the glossary table (and optional Mermaid block).
   Preserve surrounding sections byte-for-byte.
9. **Self-review.** Confirm:
   - Every term used in Intent + Scenarios resolves in the glossary OR is
     generic (per the ecosystem signal).
   - No compound term (e.g., `delta proposal validator`) is left undefined
     when its constituents alone are insufficient.
   - On unresolvable terms in **auto mode**: append a `decisions.md` entry
     **and** append a structured object to `state.json.deviations` matching
     the schema shape `{phase, cause, resolution}` (per
     `schemas/state.schema.json` — `deviations[]` items are objects, never
     bare strings). Use:
     `{"phase": "domain", "cause": "unresolved terms", "resolution": "proceeding with best-effort glossary"}`.
     Then advance.
   - On unresolvable terms in **interactive mode**: halt and ask the user
     to disambiguate.
10. **Phase transition.** Call `tools.state.complete_phase(path, "domain")`
    then `tools.state.start_phase(path, "scenarios")`. Print
    `next: /forge:scenarios`.

## Failure modes

- **`# Domain` section missing in SPEC.md.** The spec template ships the
  section; if absent, abort with
  `"SPEC.md is missing # Domain section — re-run /forge:spec"`. Detection
  must use the fence-aware scan from Step 3 — a `# Domain` line inside a
  fenced code block (e.g., a Mermaid diagram caption or a quoted spec
  template excerpt) does NOT count as the real section.
- **Unresolvable terms in interactive mode.** Halt; ask the user which term
  is canonical and what the definition should be.
- **`current_phase != "domain"`.** Surface
  `"domain phase only valid on full tier after /forge:spec completes"`.

## State writes

- `SPEC.md` `# Domain` section — in-place rewrite (glossary table, optional
  Mermaid block).
- `current_phase` — transitions `domain -> scenarios` via
  `tools.state.complete_phase` + `tools.state.start_phase`.
- `deviations[]` — appended only when auto-mode self-review finds
  unresolvable terms.

## Out of scope (M3)

No bounded-context map. No multi-glossary escalation. Single glossary even
when the feature crosses modules. Bounded-context escalation depends on
`intel/modules.md` (deferred to M4 per spec §5.3.4).

## See also

- `tools.state.complete_phase` — closes the `domain` phase.
- `tools.state.start_phase` — opens `scenarios`.
- `commands/domain.md` — slash spec.
- `/forge:scenarios` — next phase; consumes the populated `# Domain`.
- `/forge:spec` — prior phase; ships the `# Domain` placeholder this skill
  rewrites.
