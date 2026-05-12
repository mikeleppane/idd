---
name: forge-domain
description: Author a per-feature DOMAIN.md (glossary, bounded contexts, aggregates, invariants, open questions) for full-tier features and replace SPEC.md `# Domain` with a one-line pointer. Use when /forge:do (full tier) advances to the domain phase, or when the user invokes /forge:domain directly.
model: sonnet
disable-model-invocation: true
---

# FORGE Domain

> **`state.json` is hook-protected.** Mutate it only through the
> `tools.state.*` helpers — `complete_phase`, `start_phase`,
> `record_routing_decision`, `record_refined_idea`, `record_commit`,
> `append_deviation`, `set_execute_current_slice`. The PreToolUse hook
> at `hooks/check_state_writer.py` refuses direct `Write` / `Edit` /
> `MultiEdit` on `.forge/features/<id>/state.json` and surfaces a
> permission-deny with guidance toward the correct helper.

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
- `templates/feature/DOMAIN.md` — the artifact contract. Frontmatter +
  five sections (Glossary, Bounded Contexts, Aggregates, Invariants, Open
  Questions) define the shape of the file this skill writes.
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
   `# Domain` at spec time. Call `tools.state.require_full_tier(payload,
   phase="domain")` and surface the canonical raise verbatim:
   `"domain phase is full-tier only; current tier is '<X>'"`. Do NOT invent
   a custom abort string; the helper is the single source of truth and is
   shared with `/forge:refine` so users see consistent error wording across
   the two full-tier-only phases. Do NOT mutate SPEC.md or write DOMAIN.md
   when the tier guard trips.
3. **Read SPEC.** Open `SPEC.md`. Extract the `# Intent` and `# Scenarios`
   sections verbatim (read-only). Do not mutate the spec yet.
   **Fence-aware section scan (per P5 T11 H1 lesson):** when locating
   `# Intent`, `# Scenarios`, or `# Domain` headers, mask out fenced code
   blocks (` ``` ` and ` ~~~ ` delimited regions) before matching — fenced
   examples that contain literal `# Header` lines must not shadow real H1
   sections. Use the canonical helper `tools.validate._frontmatter._strip_code`
   (its real home — `tools.validate.spec_structural` only re-imports it,
   it does NOT define its own copy; `tools.validate.constitution` likewise
   imports the same symbol). The helper preserves byte offsets by replacing
   fenced + inline code regions with same-length whitespace. Do not invent
   a new section parser; reuse `_strip_code` exactly as `spec_structural`
   does. Note: `_mask_fenced_lines` lives elsewhere (`tools.delta_merge`)
   and serves a different purpose — do NOT reach for it here.
4. **Project signals.** Read `pyproject.toml` (or `package.json`) for
   ecosystem hints — language, framework, declared dependencies. These bias
   which terms count as "generic" vs "domain" (e.g., `request` is generic in
   a web app, domain-load-bearing in a billing system).
5. **Extract candidate terms.** Scan Intent + Scenarios. A term qualifies
   when it appears **2+ times AND** carries domain semantics. Drop generic
   words (`user`, `system`, `request`, `feature` unless ecosystem signal
   promotes one).
6. **Author DOMAIN.md from the template.** Open `templates/feature/DOMAIN.md`
   and use it as the structural contract for the new file at
   `.forge/features/<id>/DOMAIN.md`.
   - **Frontmatter.** Set `id` from `state.json.id`, `status: draft`,
     `version: 0.1.0`, `depends_on: []`.
   - **Glossary.** Populate the table with **4–8 rows** drawn from the
     extracted candidate terms. Each row needs a non-empty Definition. When
     a term spans bounded contexts, annotate it inline as
     `[term](context: <ctx-id>)` so downstream tooling can wire edges.
   - **Bounded Contexts.** Do not hand-author the bounded-context map.
     After the Glossary section is populated, call
     `tools.domain.render_mermaid.render_from_domain_md(domain_md_text)`
     and splice the returned block into the `# Bounded Contexts` section
     of the in-memory DOMAIN.md text, replacing the placeholder Mermaid
     block byte-for-byte. The renderer is deterministic and idempotent —
     re-running the domain phase against an unchanged glossary returns a
     byte-identical block, so manual edits inside the fenced block are
     overwritten on the next pass.
   - **Aggregates.** One H2 subsection per aggregate; list value-objects as
     bullets and aggregate-local invariants beneath them.
   - **Invariants.** Cross-aggregate rules only. Aggregate-local rules
     belong under their aggregate above.
   - **Open Questions.** Any unresolvable terms or relationships the domain
     phase could not close.
7. **Write the file.** Write `.forge/features/<id>/DOMAIN.md`. UTF-8, LF
   line endings, no trailing whitespace.
8. **Update SPEC.md `# Domain` body in place.** Locate the `# Domain`
   section using the same fence-aware approach as Step 3. Replace whatever
   lives between `# Domain` and the next H1 with a single blockquote pointer:

   ```markdown
   > See [DOMAIN.md](./DOMAIN.md) for the glossary, bounded contexts, aggregates, and invariants.
   ```

   **Keep the `# Domain` heading** — only the body shrinks. Preserve all
   surrounding sections byte-for-byte.
9. **Self-review.** Confirm:
   - Every domain-flavoured term used in Intent + Scenarios resolves in
     DOMAIN.md `# Glossary` OR is generic per the ecosystem signal.
   - Every glossary row has a non-empty Definition.
   - No compound term (e.g., `delta proposal validator`) is left undefined
     when its constituents alone are insufficient.
   - On unresolvable terms in **auto mode**: append a `decisions.md` entry
     **and** run the forge-state Bash CLI (do NOT translate to a Python
     heredoc — `phase` / `cause` / `resolution` are keyword-only and
     agents consistently mis-call them positionally):

     ```bash
     forge-state deviation --feature <id> --phase domain \
       --cause "unresolved terms" \
       --resolution "proceeding with best-effort glossary"
     ```

     The CLI writes the schema-validated entry through the hook-protected
     path; direct `Write`/`Edit`/`MultiEdit` on `state.json.deviations`
     is refused by the PreToolUse hook. Then advance.
   - On unresolvable terms in **interactive mode**: halt and ask the user
     to disambiguate.
10. **Phase transition.** Run the forge-state Bash CLI:

    ```bash
    forge-state complete-phase --feature <id> --phase domain
    forge-state start-phase    --feature <id> --phase scenarios
    ```

    Print `Next: /forge:scenarios`. Module fallback: `PYTHONPATH=$CLAUDE_PLUGIN_ROOT python3 -m tools.state_cli ...`.

## Failure modes

- **`# Domain` section missing in SPEC.md.** The spec template ships the
  section; if absent, abort with
  `"SPEC.md is missing # Domain section — re-run /forge:spec"`. Detection
  must use the fence-aware scan from Step 3 — a `# Domain` line inside a
  fenced code block (e.g., a Mermaid diagram caption or a quoted spec
  template excerpt) does NOT count as the real section.
- **`DOMAIN.md` already exists.** Abort with
  `"DOMAIN.md exists; re-running domain phase requires removing the file"`.
  A `--force` flag is a follow-up task; today the user removes the file
  manually before re-running.
- **Unresolvable terms in interactive mode.** Halt; ask the user which term
  is canonical and what the definition should be.
- **`current_phase != "domain"`.** Surface
  `"domain phase only valid on full tier after /forge:spec completes"`.

## State writes

- `.forge/features/<id>/DOMAIN.md` — newly created from
  `templates/feature/DOMAIN.md`. Source of truth for the feature's
  glossary, bounded contexts, aggregates, invariants, and open questions.
- `SPEC.md` `# Domain` section — body replaced in place with a one-line
  pointer to `DOMAIN.md`. Heading retained.
- `current_phase` — transitions `domain -> scenarios` via
  `tools.state.complete_phase` + `tools.state.start_phase`.
- `deviations[]` — appended only when auto-mode self-review finds
  unresolvable terms.

## Out of scope

No multi-glossary escalation. A single DOMAIN.md per feature even when the
feature crosses modules. When the glossary genuinely needs to split, that is
a follow-up task — not a per-run decision this skill makes.

## See also

- `templates/feature/DOMAIN.md` — the artifact contract this skill produces.
- `tools.domain.render_mermaid` — the deterministic bounded-context Mermaid
  renderer this skill calls in Step 6 to populate `# Bounded Contexts`.
- `tools.state.complete_phase` — closes the `domain` phase.
- `tools.state.start_phase` — opens `scenarios`.
- `commands/domain.md` — slash spec.
- `/forge:scenarios` — next phase; consumes the populated DOMAIN.md.
- `/forge:spec` — prior phase; ships the `# Domain` placeholder this skill
  rewrites into a pointer.
