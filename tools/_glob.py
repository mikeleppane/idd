"""Stdlib-only ``**``-aware glob matcher.

Shared by ``tools.redaction``, ``tools.validate.conventions``, and
``tools.conventions_runtime``. Public entry point so cross-module consumers
do not depend on a single-underscore private name.

This module is import-clean against the standard library only; do not add
third-party dependencies. The dispatch-time hook (``hooks/check_budget.py``)
relies on that contract.
"""

from __future__ import annotations

import re


def globstar_match(path: str, glob: str) -> bool:
    """Return True iff ``path`` matches a ``**``-aware ``glob``.

    Translation rules:
      * ``**/`` (followed by ``/``) → ``(?:.*/)?``  matches zero-or-more
        leading path segments, so ``**/.env`` matches both ``.env`` (root)
        and ``project/.env`` (nested) — parity with
        ``PurePosixPath.full_match`` (Python 3.13+).
      * remaining ``**``  → ``.*``    (cross path separators)
      * single ``*``      → ``[^/]*`` (within one path segment)
      * ``?``             → ``[^/]``
      * everything else is ``re.escape``-protected.

    Stand-in for ``PurePosixPath.full_match`` (Python 3.13+); we run on 3.12
    so we ship our own. Fully anchored via ``re.fullmatch``.
    """
    out: list[str] = []
    i = 0
    while i < len(glob):
        ch = glob[i]
        if ch == "*":
            if i + 1 < len(glob) and glob[i + 1] == "*":
                if i + 2 < len(glob) and glob[i + 2] == "/":
                    out.append("(?:.*/)?")
                    i += 3
                else:
                    out.append(".*")
                    i += 2
            else:
                out.append("[^/]*")
                i += 1
        elif ch == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(ch))
            i += 1
    pattern = "".join(out)
    return re.fullmatch(pattern, path) is not None
