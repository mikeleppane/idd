---
name: forge-amend-constitution
description: Atomic edit of `.forge/CONSTITUTION.md` with $EDITOR, semver bump, and a decisions.md ADR entry. Use when the user asks to amend or extend the project Constitution. Use --bootstrap to seed an initial Constitution (defers to forge-bootstrap-constitution). Use --resync-agents to extract prose conventions from AGENTS.md / CLAUDE.md / README.md and route them to the right enforcement mechanism (defers to forge-resync-agents).
disable-model-invocation: true
---

# FORGE Amend Constitution

## When this skill applies

User invoked `/forge:amend-constitution` (with or without `--bootstrap`).

## Inputs

- `.forge/CONSTITUTION.md` (read; required unless `--bootstrap` is on AND the file is absent).
- `decisions.md` (append-only). Default path: `<repo_root>/decisions.md`; project may override.
- `$EDITOR` for interactive edits.

## Steps (regular amend)

1. **Read current Constitution.** Abort if missing and `--bootstrap` not set.
2. **Open in $EDITOR.** Use a tmpfile copy; restore on editor crash.
3. **Detect diff.** No-op → abort with "no changes detected".
4. **Classify change** via `tools.constitution_amend.classify_change` → patch/minor/major.
5. **Bump frontmatter version** via `tools.constitution_amend.bump_version`. Update `updated:` to today.
6. **Prompt for decisions.md ADR body.** Gather the user's reason BEFORE any disk write so an aborted prompt cannot leave a partially-applied amend. Empty body → abort.
7. **Validate.** Run `sys.executable -m tools.validate --target constitution <tmp>`. Abort with no disk mutation on any BLOCK/HIGH.
8. **Auto-create `decisions.md`** with `# Decisions\n\n` header if it does not yet exist (Open Scoping #10).
9. **Atomic write** the new Constitution body via `os.replace` from a sibling tempfile.
10. **Append decisions entry.** On append failure (rare; e.g. read-only filesystem), restore Constitution to `before` via a second `os.replace` so both files end at pre-amend state.
11. **Surface to user:** old version, new version, scope, decisions.md path.

## Steps (`--bootstrap` mode)

1. Refuse if `.forge/CONSTITUTION.md` already exists.
2. Dispatch the `forge-bootstrap-constitution` skill, which owns the
   skill-driven drafting workflow, interactive review loop, and atomic
   write via `tools.constitution_amend.persist_drafted_constitution`.
   Return to this skill's caller on completion.

## Steps (`--resync-agents` mode)

1. Dispatch the `forge-resync-agents` skill, which walks the user
   through extracting prose conventions from AGENTS.md / CLAUDE.md /
   README.md and routing each to the right enforcement mechanism
   (hook / validator / reviewer-tag / advisory). Return to this skill's
   caller on completion.

## Done

`.forge/CONSTITUTION.md` exists, validates clean, decisions.md has a new entry.
