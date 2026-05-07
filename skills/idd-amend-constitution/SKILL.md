---
name: idd-amend-constitution
description: Atomic edit of `.idd/CONSTITUTION.md` with $EDITOR, semver bump, and a decisions.md ADR entry. Use when the user asks to amend or extend the project Constitution. Use --bootstrap to seed an initial Constitution from project signals.
disable-model-invocation: true
---

# IDD Amend Constitution

## When this skill applies

User invoked `/idd:amend-constitution` (with or without `--bootstrap`).

## Inputs

- `.idd/CONSTITUTION.md` (read; required unless `--bootstrap` is on AND the file is absent).
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

1. Refuse if `.idd/CONSTITUTION.md` already exists.
2. Read project signals (`pyproject.toml`, `package.json`, `Cargo.toml`, top-level dirs).
3. Generate up to 5 starter article proposals (max 9 if user expands).
4. Article-by-article: print, ask accept / edit / drop.
5. Open final draft in $EDITOR for any last polish.
6. Write `.idd/CONSTITUTION.md` with frontmatter `version: 0.1.0`, `created: today`.
7. Append decisions.md entry naming the bootstrap.

## Done

`.idd/CONSTITUTION.md` exists, validates clean, decisions.md has a new entry.
