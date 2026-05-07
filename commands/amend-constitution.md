---
name: amend-constitution
description: Open `.idd/CONSTITUTION.md` in $EDITOR for atomic edit; bumps version per the scope of the change and appends an ADR entry to decisions.md. Pass `--bootstrap` to seed a starter Constitution from project signals (proposes 5 articles, max 9, opt-in).
---

# /idd:amend-constitution

## Behavior

1. Without `--bootstrap`: invoke `idd-amend-constitution` skill in regular-amend mode.
2. With `--bootstrap`: invoke skill in bootstrap mode (refuses if `.idd/CONSTITUTION.md` exists).

## Failure modes

- Constitution missing without `--bootstrap` → abort with "run with `--bootstrap` to seed".
- Constitution invalid post-edit → roll back, abort with the validator's error message.
- No diff detected → abort with "no changes; nothing to amend".
- $EDITOR crashes → tmpfile cleanup; original Constitution untouched.
