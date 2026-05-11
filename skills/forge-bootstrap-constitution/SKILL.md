---
name: forge-bootstrap-constitution
description: Skill-driven first-time Constitution drafting with bounded signal collection and interactive review. Use when /forge:do preflight or /forge:amend-constitution --bootstrap detects no .forge/CONSTITUTION.md.
model: sonnet
disable-model-invocation: true
---

# FORGE Bootstrap Constitution

> **Bounded Python I/O, skill-owned drafting.** This skill owns signal
> surfacing, the user-facing draft turn, and the interactive review loop.
> Python (`tools.constitution_amend`) owns the bounded reads, the schema
> validator, and the atomic-pair write. No Python module calls an LLM —
> drafting is a step in this skill's own turn.

## When this skill applies

Two entry paths converge here:

- **`/forge:do` preflight (step 2)** detected `.forge/CONSTITUTION.md` is
  absent and the user chose `bootstrap` over `skip` / `cancel`.
- **`/forge:amend-constitution --bootstrap`** invoked directly against a
  repo that has no existing Constitution.

Both paths run the same workflow described below; the only difference is
the dispatch literal that precedes this skill.

## Inputs

- `repo_root` — absolute path to the repository root.
- `decisions_path` — default `<repo_root>/decisions.md`; project may
  override.

Refuses if `.forge/CONSTITUTION.md` already exists. The refusal matches the
exact wording raised by
`tools.constitution_amend.persist_drafted_constitution`:
`"Constitution already exists at <path>; use plain /forge:amend-constitution"`.

## Mode resolution

Both entry paths (see "When this skill applies") share the same workflow.
The skill does not branch on entry-point identity — it always:

1. Verifies no Constitution is present.
2. Collects bounded signals via Python.
3. Drafts in-session.
4. Runs the interactive review loop.
5. Either persists, skips, edits, or cancels per user choice.

The entry-point only changes which command surfaced the dispatch literal.

## Steps

1. **Refuse if Constitution exists.** Check
   `<repo_root>/.forge/CONSTITUTION.md`. If present, abort with the literal
   message
   `"Constitution already exists at <path>; use plain /forge:amend-constitution"`
   (matches the exact wording raised by
   `tools.constitution_amend.persist_drafted_constitution`) and propagate
   the abort to the caller. No disk mutation, no further prompts.

2. **AGENTS.md / CLAUDE.md absence warning (locked decision 5).** Before
   signal collection: if NEITHER `<repo_root>/AGENTS.md` NOR
   `<repo_root>/CLAUDE.md` exists, present **one** `AskUserQuestion` with
   exactly the message:

   > No CLAUDE.md / AGENTS.md found. Recommend creating one before bootstrap for better starter Constitution. Proceed anyway?

   Options (single-select):

   - `[Y]es proceed` — continue to step 3 (default suggestion so a brand-new
     repo can still bootstrap).
   - `[N]o cancel` — abort with no disk mutation and no state change; the
     caller continues as if the user chose `skip` / `cancel` at the
     preflight selector.

   This is a single-question turn. Do not batch it with the later selector.

3. **Collect bounded signals.** Call
   `tools.constitution_amend.collect_bootstrap_signals(repo_root)`. The
   helper returns a `BootstrapSignals` dataclass with fields `files: list[SignalFile]`,
   `dropped_for_secrets: list[PurePosixPath]`, `truncated: list[PurePosixPath]`,
   and `total_bytes: int`. Surface to the user in plain prose, in this
   order:

   - **Collected file count** — `len(signals.files)`.
   - **Secret-dropped paths** — every row in `signals.dropped_for_secrets`,
     so the user knows why a candidate was skipped (deny-glob match or
     secret-shaped content).
   - **Truncated paths** — every row in `signals.truncated`, so the user
     knows a file was capped at 16 KiB and the tail was dropped.

   Python performs zero LLM calls and zero network access at this step;
   the helper is pure bounded I/O.

4. **Draft articles from the signals.** This is the in-session drafting
   step: the Claude Code session itself reads the signal payload returned
   by step 3 and emits the Constitution markdown body as part of its own
   turn. Use the drafting prompt verbatim:

   > Read the bounded signals payload. Propose Constitution articles that
   > fit THIS project, evidence-based. Each article needs `title`, `level`
   > (CRITICAL/SHOULD/MAY), `rule`, `reference`, `rationale`, `exception`.
   > Typical Constitution has 3–12 articles — propose what fits, no more,
   > no less. Skip rules that don't apply. **No universal seed; do not add
   > articles for areas the signals don't justify.** Emit markdown
   > matching `templates/constitution/CONSTITUTION.md` shape.

   Emit the full markdown body in this turn:

   - Leading frontmatter block:

     ```
     ---
     version: 0.1.0
     created: "YYYY-MM-DD"
     ---
     ```

     where `YYYY-MM-DD` is today's date.

   - A `# Project Constitution` H1 header followed by the standard
     preamble paragraph from `templates/constitution/CONSTITUTION.md`.

   - One `## Article N — <title> [<LEVEL>]` block per article (N starts
     at 1, monotonic). Each block carries:

     - `**Rule:**` — one or more sentences.
     - `**Reference:**` — citation, standard, or team-consensus marker.
     - `**Rationale:**` — why this rule matters for THIS repo, grounded
       in the signal payload.
     - `**Exception:**` — escape valve, or `None.`

   The body must match the template shape because step 7 / step 8 will
   re-parse it through `tools.constitution_amend.validate_drafted_markdown`.

5. **Show the user the full draft, then present the selector.** Print the
   draft markdown verbatim. Then present **one** `AskUserQuestion` (same
   sequential pattern as `skills/forge-refine/SKILL.md` step 6a and
   `skills/forge-crucible/SKILL.md` Adversarial Q&A — "ask the user one
   at a time"):

   > Constitution draft ready. What now?

   Options (single-select, exactly five):

   - `[a]ccept` — write the draft to disk via
     `persist_drafted_constitution` (see step 8).
   - `[r]efine` — provide feedback for re-draft; enters the refine loop
     (see step 6).
   - `[e]dit-in-editor` — open the draft in `$EDITOR` for hand edits
     (see step 7).
   - `[s]kip` — proceed without a Constitution; preserves the existing
     `/forge:do` skip behavior (see step 9).
   - `[c]ancel` — abort with no state mutation (see step 10).

   This is a single-question turn. Do not batch any clarifier with it.

6. **Refine loop (cap 5 rounds).** On `[r]efine`:

   - Ask **one** clarifying question per turn. Examples: `"Which article
     should be dropped?"`, `"What's the new rule wording for article
     N?"`, `"Loosen which level — Article N from SHOULD to MAY?"`. The
     wording mirrors `forge-refine` step 6a: one question, one answer,
     re-draft, then return to the selector.
   - Capture each answer and append it to an accumulating feedback
     history (skill-local; not persisted).
   - Re-draft the full Constitution markdown body in the next turn,
     consuming the feedback history alongside the original signal
     payload. Re-emit the entire body — do not patch in place.
   - Show the new draft and loop back to step 5's selector.
   - **Per-round cap.** Track round count locally; increment after each
     `[r]efine` round completes (post-redraft, pre-next-selector).
   - **Cap reached.** After 5 rounds without `[a]ccept`, surface the
     literal message
     `"refine cap reached; choose [a]ccept current draft, [e]dit-in-editor, or [c]ancel"`
     and present a degraded selector with **exactly three** options:
     `[a]ccept` / `[e]dit-in-editor` / `[c]ancel`. The `[r]efine` and
     `[s]kip` options drop on cap-reached.

7. **`[e]dit-in-editor`.** Write the current draft body to a tempfile,
   open `$EDITOR` against it, then read back the edited body. Validate
   via `tools.constitution_amend.validate_drafted_markdown(text)`:

   - On success, treat the returned body as the new draft and proceed
     directly to step 8 (`[a]ccept` semantics).
   - On `AmendError`, surface the helper's message to the user and offer
     **one** repair round: re-open the editor with the broken body so
     the user can fix the validation failure. If the second pass still
     raises `AmendError`, fall through to a degraded selector with
     options `[e]dit-in-editor` (one more pass) or `[c]ancel`. The skill
     never writes an unvalidated body to disk.

8. **`[a]ccept`.** Call
   `tools.constitution_amend.persist_drafted_constitution(repo_root=<repo_root>, body=<draft>, decisions_path=<decisions_path>)`.
   The helper:

   - Re-runs `validate_drafted_markdown` defensively.
   - Runs the structural validator against a temp copy.
   - `ensure_decisions_file(decisions_path)` to seed the standard
     `# Decisions` header if `decisions.md` is absent.
   - Atomically writes `.forge/CONSTITUTION.md` via `atomic_replace`.
   - Appends a bootstrap ADR row to `decisions.md`.
   - On append failure, rolls both files back to pre-call state and
     raises `AmendError`.

   Returns the absolute path to the new Constitution. Surface the path
   and article count to the user, then return control to the caller.

9. **`[s]kip`.** No disk mutation. Log a one-line surfaced note that the
   user declined to seed a Constitution this turn. Continue the calling
   flow as if no Constitution exists (this matches today's `/forge:do`
   preflight skip semantics — the feature folder still gets seeded
   downstream with `constitution_present: false`).

10. **`[c]ancel`.** Abort with no disk mutation. If the entry-point was
    `/forge:do` preflight, propagate cancellation upstream so the
    routing helper does NOT seed a feature folder. If the entry-point
    was `/forge:amend-constitution --bootstrap`, simply exit cleanly.

## Sequential-question contract (locked)

This skill follows the **one-question-per-turn** pattern locked under
WS1's *User interaction pattern (locked — from SDD workshop L1)* in
`docs/plans/2026-05-11-conventions-trap-memory-design.md`. The same
pattern already governs `skills/forge-refine/SKILL.md` step 6a and
`skills/forge-crucible/SKILL.md` Adversarial Q&A.

Three sequential question points exist in this skill, each its own
`AskUserQuestion` turn:

1. The **AGENTS.md / CLAUDE.md absence warning** (step 2) — two options.
2. The **accept / refine / edit / skip / cancel selector** (step 5) —
   five options; degrades to three on refine-cap-reached (step 6).
3. Any **refine-branch clarifier** (step 6) — one targeted question per
   round, never a batched numbered list.

Batched multi-question prompts are forbidden in this skill. Forcing the
user to answer a numbered list at once raises cognitive load and skews
answers toward "say yes to all to move on" (workshop L1, slides 62–66).

## Failure modes

- **Constitution already exists.** Refuses with the literal
  `persist_drafted_constitution` message and suggests
  `/forge:amend-constitution`.
- **`validate_drafted_markdown` raises `AmendError` on `[a]ccept`.**
  Surface the helper's message; offer one re-draft round; if the
  redraft also fails, fall through to `[e]dit-in-editor` or `[c]ancel`.
- **`validate_drafted_markdown` raises `AmendError` on
  `[e]dit-in-editor` readback.** Surface the message; offer one repair
  round in the editor; on a second failure, fall through to `[c]ancel`.
- **Structural validator raises (via
  `persist_drafted_constitution`).** Surface the message; offer
  `[e]dit-in-editor` so the user can repair the body and retry
  `[a]ccept`.
- **`decisions.md` append fails (atomic-pair rollback).** Python rolls
  both `.forge/CONSTITUTION.md` and (if it was freshly created)
  `decisions.md` back to pre-call state before raising. Surface the
  `AmendError` message to the user.
- **Refine cap reached.** Surface
  `"refine cap reached; choose [a]ccept current draft, [e]dit-in-editor, or [c]ancel"`
  and present the three-option degraded selector (no further refine
  rounds, no skip).
- **User cancels at the absence-warning prompt.** Abort cleanly, no
  disk mutation, no further prompts.

## State writes

This skill predates a per-feature folder, so it writes no
`state.json` rows directly. The only persistent record is the
`decisions.md` ADR row appended by
`tools.constitution_amend.persist_drafted_constitution` inside the
atomic-pair lifecycle. On `[s]kip` and `[c]ancel`, no record is written.

## See also

- `tools.constitution_amend.collect_bootstrap_signals` — bounded I/O
  helper that produces `BootstrapSignals` + `SignalFile` rows for this
  skill to surface.
- `tools.constitution_amend.validate_drafted_markdown` — pre-write
  shape and budget validation; raises `AmendError` on rejection.
- `tools.constitution_amend.persist_drafted_constitution` — atomic-pair
  write of `.forge/CONSTITUTION.md` and the `decisions.md` ADR row;
  refuses on pre-existing Constitution.
- `tools.constitution_amend.AmendError` — surfaced exception class.
- `skills/forge-amend-constitution` — non-bootstrap amend lifecycle for
  an already-seeded Constitution.
- `skills/forge-do` — preflight step that dispatches here on the
  `bootstrap` choice.
- `templates/constitution/CONSTITUTION.md` — body shape the draft must
  match (frontmatter, preamble, `## Article N — <title> [<LEVEL>]`
  blocks with `Rule` / `Reference` / `Rationale` / `Exception`).
