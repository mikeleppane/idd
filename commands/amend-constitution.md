---
name: amend-constitution
description: Open `.forge/CONSTITUTION.md` in $EDITOR for atomic edit, bump version per the scope of the change, and append an ADR entry to decisions.md. Use when the user asks to amend, extend, or bootstrap the project Constitution. Pass `--bootstrap` to seed a starter Constitution from project signals (proposes up to 5 articles, opt-in).
---

# /forge:amend-constitution

## Behavior

1. Without `--bootstrap`: invoke `forge-amend-constitution` skill in regular-amend mode.
2. With `--bootstrap`: invoke skill in bootstrap mode (refuses if `.forge/CONSTITUTION.md` exists).

## Failure modes

- Constitution missing without `--bootstrap` → abort with "run with `--bootstrap` to seed".
- Constitution invalid post-edit → roll back, abort with the validator's error message.
- No diff detected → abort with "no changes; nothing to amend".
- $EDITOR crashes → tmpfile cleanup; original Constitution untouched.
