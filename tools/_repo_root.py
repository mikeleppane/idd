"""Repo-root autodiscovery via ``.forge/`` walk-up.

A small, focused helper shared between the validator CLI and the state-machine
helpers. Both surfaces need the same answer to the same question — *"given a
SPEC.md / state.json path, which directory is the FORGE repo root?"* — without
requiring every caller to be cwd-aware. The marker is ``.forge/``: every FORGE
repo carries one at the root, and per-feature artifacts live underneath it.

This mirrors the walk-up pattern used by
:func:`tools.state._autodiscover_state_schema`; it is intentionally NOT shared
with that helper because the two answer different questions (schema location
vs. repo root) and converging them would force one to grow knobs it does not
need today.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

# Hard cap on the directory walk. Defensive: legitimate repo trees never need
# more than ~6 levels, and a runaway walk would touch the filesystem far more
# than a path-to-repo lookup should.
_WALK_DEPTH_CAP: Final[int] = 12


def discover_repo_root(start: Path) -> Path | None:
    """Walk up from ``start`` looking for a directory carrying ``.forge/``.

    The walk begins at ``start`` itself (when it is a directory) or at
    ``start.parent`` (when it is a file). At each level the helper checks for
    a ``.forge/`` sub-directory; the first match is returned. The walk stops
    at the filesystem root or when the depth cap is reached.

    Args:
        start: Path inside the repository — typically a SPEC.md or state.json
            path. Existence is not required; ``Path.resolve()`` is applied so
            symlinks are followed before the walk.

    Returns:
        Absolute path to the discovered repo root, or ``None`` when nothing
        matched within the cap.
    """
    try:
        resolved = start.resolve()
    except OSError:
        return None
    current = resolved if resolved.is_dir() else resolved.parent
    for _ in range(_WALK_DEPTH_CAP):
        if (current / ".forge").is_dir():
            return current
        parent = current.parent
        if parent == current:
            return None
        current = parent
    return None
