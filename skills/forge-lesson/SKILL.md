---
name: forge-lesson
description: Sequential interactive authoring for one cross-feature trap lesson. Use when /forge:lesson fires.
model: sonnet
disable-model-invocation: true
---

# FORGE Lesson

> **Bounded Python I/O, skill-owned authoring.** This skill owns the
> sequential question turns, the tag-vocabulary draft, the final-review
> turn, and any in-session edit pass. Python
> (`tools.intel.lessons`) owns the parser, the id allocator, and the
> atomic-pair append. No Python module calls an LLM — drafting is a step
> in this skill's own turns.

## When this skill applies

Triggered by `/forge:lesson`. Single entry path. The skill never fires
on its own — `disable-model-invocation: true` keeps it slash-only so the
sequential `AskUserQuestion` contract holds.

## Inputs

- `repo_root` — absolute path to the repository root.
- No feature folder — lessons are cross-feature; the manual path runs at
  the repo root and never reads or mutates `.forge/features/<id>/`.

## Steps

1. **Refuse if the lessons file is malformed.** Call
   `tools.intel.lessons.parse(repo_root / ".forge/intel/lessons.md")`
   when the file exists. If it raises `LessonError`, surface the message
   verbatim and abort with no further prompts. The user fixes the file
   first; the allocator (step 2) would otherwise refuse to skip over a
   broken file.

2. **Resolve the next id.** Call
   `tools.intel.lessons.next_id(repo_root)` → bound as `lesson_id`. The
   skill reserves this id for the new entry. Missing file returns
   `L001`; otherwise `L<max+1:03d>`.

3. **Trap question.** Present **one** `AskUserQuestion`:

   > Describe the trap. What went wrong? Include enough detail that
   > future subagents recognize the same shape.

   Free-text response, bound as `trap`. Cap at 1000 chars before
   re-prompt — if the answer exceeds the cap, surface the byte count and
   ask the user to tighten the body, then re-prompt with the same
   question.

4. **Avoidance question.** Present **one** `AskUserQuestion`:

   > Describe the avoidance. What should future subagents do instead?

   Free-text response, bound as `avoidance`. Same 1000-char cap as
   step 3; same re-prompt behavior on overflow.

5. **Severity selector.** Present **one** `AskUserQuestion` with four
   options (single-select, no `[default]` — force a deliberate pick):

   - `CRITICAL`
   - `HIGH`
   - `MEDIUM`
   - `LOW`

   Bound as `severity`. The lesson parser refuses anything outside this
   set, so accepting a free-form severity here would only push the
   rejection out to `append` (step 9).

6. **Captured-from question.** Present **one** `AskUserQuestion`:

   > What feature surfaced this trap? Use the feature id (e.g.
   > `m8-p0-substrate`) or `manual` if not tied to a feature.

   Free-text response, bound as `captured_from`. The parser accepts
   free-form text on this field; `manual` is the conventional
   placeholder when the trap surfaced outside a feature folder.

7. **Tag draft from the controlled vocabulary.** Match `trap` +
   `avoidance` text against the vocabulary
   `tools.intel.lessons._TAG_VOCAB`
   (`imports`, `fixtures`, `state-mutation`, `async`, `secrets`,
   `validation`, `dispatch`, `review-tagging`, `ship-gate`, `cross-ai`,
   `bdd`, `frontmatter`). Surface the draft via **one**
   `AskUserQuestion`:

   > Proposed tags: <comma-separated list>. Edit?

   Options (single-select):

   - `[k]eep` — accept the drafted tag list.
   - `[e]dit` — open the tag list in `$EDITOR` for hand edits. Re-validate
     the edited list against `_TAG_VOCAB` after readback. Any token
     outside the vocabulary fails the check; surface the offending
     token(s) and offer one re-edit round, then fall through to
     `[d]rop-all`.
   - `[d]rop-all` — require the user to pick at least one tag from the
     vocabulary list (the parser refuses an empty Tags row). Re-prompt
     with the full vocabulary as suggestions until the user names ≥1
     valid token.

   Bound as `tags`. Free-form tags are never accepted — only the
   controlled vocabulary clears.

8. **Final review.** Render the lesson markdown block following the
   shape locked in `templates/intel/lessons.md`:

   ```
   ## L<NNN> — <one-line trap title>
   **Captured:** <today> from feature <captured_from>
   **Resolved by:** manual
   **Trap:** <trap>
   **Avoidance:** <avoidance>
   **Tags:** <comma-separated tags>
   **Severity:** <CRITICAL|HIGH|MEDIUM|LOW>
   **Status:** active
   ```

   The `<one-line trap title>` is derived from the first line of `trap`
   (matches the title-derivation rule in
   `tools.intel.lessons._serialize_lesson`). `<today>` is today's date
   in `YYYY-MM-DD` form. Print the rendered block verbatim so the user
   sees exactly what will land on disk, then present **one**
   `AskUserQuestion`:

   > Append this lesson?

   Options (single-select):

   - `[a]ccept` — call `tools.intel.lessons.append(repo_root, lesson)`
     (see step 9).
   - `[e]dit-in-editor` — open the rendered markdown in `$EDITOR` for
     hand edits. Re-parse the edited body via
     `tools.intel.lessons.parse_text`. On success, treat the returned
     `Lesson` as the new draft and proceed directly to step 9
     (`[a]ccept` semantics). On `LessonError`, surface the helper's
     message and offer one repair round in the editor; on a second
     failure, fall through to `[c]ancel`.
   - `[c]ancel` — abort cleanly. No disk mutation, no state record.

9. **On accept.** Build the `Lesson` dataclass from the captured fields
   and call `tools.intel.lessons.append(repo_root, lesson)`. The helper
   refuses if `lesson.id != tools.intel.lessons.next_id(repo_root)`
   (the allocator forbids skipping ids), atomically writes
   `.forge/intel/lessons.md` via
   `tools.constitution_amend.atomic_replace`, and round-trip-validates
   the merged body. On `LessonError`, surface the message and offer one
   re-edit round via `[e]dit-in-editor`; on a second failure, fall
   through to `[c]ancel`.

   On success, surface the new lesson id and the absolute path to
   `.forge/intel/lessons.md`, then return control to the caller.

## Sequential-question contract (locked)

This skill follows the **one-question-per-turn** pattern that already
governs `skills/forge-refine/SKILL.md` step 6a,
`skills/forge-crucible/SKILL.md` Adversarial Q&A,
`skills/forge-bootstrap-constitution/SKILL.md` steps 2 / 5 / 6, and
`skills/forge-resync-agents/SKILL.md`.

Six sequential question points exist in this skill, each its own
`AskUserQuestion` turn:

1. **Trap** (step 3) — free-text body.
2. **Avoidance** (step 4) — free-text body.
3. **Severity** (step 5) — four-option single-select, no default.
4. **Captured-from** (step 6) — free-text feature id or `manual`.
5. **Tag draft** (step 7) — three-option `[k]eep / [e]dit / [d]rop-all`
   selector with re-validation against `_TAG_VOCAB`.
6. **Final accept** (step 8) — three-option `[a]ccept /
   [e]dit-in-editor / [c]ancel` selector.

Batched multi-question prompts are forbidden in this skill. Forcing the
user to answer a numbered list at once raises cognitive load and skews
answers toward "say yes to all to move on."

## Failure modes

- **Malformed `.forge/intel/lessons.md`.** `parse` raises `LessonError`
  in step 1; abort with the helper's message. No prompts, no further
  state change. The user must repair the file first; the allocator
  refuses to skip a broken file.
- **`append` raises `LessonError` on `[a]ccept`.** Surface the helper's
  message; offer one re-edit round via `[e]dit-in-editor`. If the
  edited body still raises on re-parse or re-`append`, fall through to
  `[c]ancel` with no disk mutation.
- **Tag-vocabulary mismatch in `[e]dit`.** Surface the offending
  token(s); offer one re-edit round in `$EDITOR`. On a second failure,
  fall through to `[d]rop-all` and re-prompt with the full vocabulary
  list.
- **User cancels at any step.** Abort cleanly. No disk mutation, no
  partial record.

## State writes

- `.forge/intel/lessons.md` — one new entry appended (atomic-replace
  via `tools.constitution_amend.atomic_replace` inside
  `tools.intel.lessons.append`). The append helper round-trip-parses
  the merged body before persisting, so a malformed serialization can
  never reach disk.
- **No `decisions.md` row.** Lessons are not governance amendments;
  they are trap-memory entries consumed by the dispatch budget. The
  bootstrap / amend Constitution flow is the only path that writes an
  ADR row, and it lives in `tools.constitution_amend`, not here.
- **No `state.json` mutation.** The manual lesson path operates at the
  repo root and never opens a feature folder.

## See also

- `commands/lesson.md` — slash command that dispatches this skill.
- `skills/forge-review/SKILL.md` — auto-harvest path (Step 7 sub-step):
  during convergence, when a finding flips to `resolved` with a SHA
  reference and severity HIGH+, the reviewer offers to harvest the
  trap into `.forge/intel/lessons.md` without leaving the review flow.
- `tools.intel.lessons.append` — atomic append; refuses on id-skip,
  round-trip-parses the merged body before commit.
- `tools.intel.lessons.next_id` — id allocator; refuses to advance over
  a malformed file.
- `tools.intel.lessons.parse` /
  `tools.intel.lessons.parse_text` — file + in-memory parsers; raise
  `LessonError` with field-named messages on rejection.
- `tools.intel.lessons._TAG_VOCAB` — frozen controlled tag vocabulary.
- `tools.intel.lessons.Lesson` — dataclass shape for one entry.
- `templates/intel/lessons.md` — entry shape the rendered block must
  match.
- `tools.validate.lessons` — structural validator
  (`python -m tools.validate --target lessons`).
