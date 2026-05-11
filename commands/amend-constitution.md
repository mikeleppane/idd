---
name: amend-constitution
description: Open `.forge/CONSTITUTION.md` in $EDITOR for atomic edit, bump version per the scope of the change, and append an ADR entry to decisions.md. Use when the user asks to amend or extend the project Constitution. Pass `--bootstrap` to seed an initial Constitution via the `forge-bootstrap-constitution` skill (bounded signal collection, in-session drafting, sequential `accept / refine / edit-in-editor / skip / cancel` selector).
---

# /forge:amend-constitution

## Behavior

1. **Without `--bootstrap`** — invoke `forge-amend-constitution` skill in
   regular-amend mode. The skill opens `.forge/CONSTITUTION.md` in
   `$EDITOR`, classifies the diff (patch/minor/major), bumps semver,
   prompts for a decisions.md ADR body, validates the result, and
   atomically writes both files via the atomic-pair contract in
   `tools.constitution_amend.amend_constitution`.

2. **With `--bootstrap`** — defer to `forge-bootstrap-constitution`. The
   bootstrap skill:
   - Refuses if `.forge/CONSTITUTION.md` already exists, raising the
     literal AmendError
     `"Constitution already exists at <path>; use plain /forge:amend-constitution"`.
   - Warns once via `AskUserQuestion` if neither `AGENTS.md` nor
     `CLAUDE.md` is present.
   - Calls `tools.constitution_amend.collect_bootstrap_signals(repo_root)`
     to gather a bounded payload (up to 8 files, 16 KiB each, 80 KiB
     total) of manifest + documentation signals; secret-shaped or
     deny-glob-matched paths are filtered before drafting.
   - Drafts the Constitution markdown in-session from the signal
     payload — evidence-based articles only, no universal seed.
   - Presents the user with a five-option selector via `AskUserQuestion`:
     - `[a]ccept` — persist via
       `tools.constitution_amend.persist_drafted_constitution` (atomic-pair
       write of `.forge/CONSTITUTION.md` + bootstrap ADR row in
       decisions.md).
     - `[r]efine` — single clarifying question, re-draft, re-show
       (cap = 5 rounds; after cap the selector degrades to
       `[a]ccept / [e]dit-in-editor / [c]ancel`).
     - `[e]dit-in-editor` — open the draft in `$EDITOR`, re-validate
       via `validate_drafted_markdown`; one repair round on failure.
     - `[s]kip` — no disk mutation; preserves the `/forge:do` skip
       semantics so the caller can continue without a Constitution.
     - `[c]ancel` — abort cleanly, no disk mutation, propagate to the
       caller.

## Failure modes

- Constitution missing without `--bootstrap` → abort with
  `"run with --bootstrap to seed"`.
- Constitution invalid post-edit → roll back, abort with the validator's
  error message.
- No diff detected → abort with `"no changes; nothing to amend"`.
- `$EDITOR` crashes → tmpfile cleanup; original Constitution untouched.
- `--bootstrap` invoked when `.forge/CONSTITUTION.md` exists → bootstrap
  skill refuses with the literal AmendError above.
- `validate_drafted_markdown` rejects the draft on `[a]ccept` → the
  bootstrap skill surfaces the helper message and offers a one-round
  re-draft, then falls through to `[e]dit-in-editor` or `[c]ancel`.
- `decisions.md` append fails after the Constitution is written →
  atomic-pair rollback removes the just-written Constitution and any
  freshly-created decisions.md.

## See also

- `skills/forge-amend-constitution/SKILL.md` — regular-amend lifecycle.
- `skills/forge-bootstrap-constitution/SKILL.md` — bootstrap drafting
  workflow.
- `tools.constitution_amend.collect_bootstrap_signals` — bounded I/O
  helper used by the bootstrap skill.
- `tools.constitution_amend.validate_drafted_markdown` — pre-write
  shape and budget validation.
- `tools.constitution_amend.persist_drafted_constitution` — atomic-pair
  write that backs the `[a]ccept` selector option.
