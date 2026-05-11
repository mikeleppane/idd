"""Lessons (intel) structural validator.

Wraps :func:`tools.intel.lessons.parse` and surfaces parse failures as a
single ``BLOCK`` :class:`Finding`. The parser raises on the first malformed
block; multi-error surfacing is intentionally out of scope here so the
operator fixes one thing at a time. Same shape as
:func:`tools.validate.constitution.validate_constitution`'s
parser-failure path.

The ``tools.intel.lessons`` import is deferred to call time. The intel
module reaches into :mod:`tools.constitution_amend` for its atomic write
helper, and that module in turn imports from :mod:`tools.validate`. Importing
the parser eagerly here would re-enter :mod:`tools.validate` during its own
package load when callers reach for the validator via
:mod:`tools.constitution_amend`'s preflight, creating a circular import.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from ._finding import Finding

_TARGET: Final[str] = "lessons"


def _lessons_path(repo_root: Path) -> Path:
    return repo_root / ".forge" / "intel" / "lessons.md"


def validate_lessons(repo_root: Path) -> list[Finding]:
    """Validate ``.forge/intel/lessons.md`` shape via :func:`tools.intel.lessons.parse`.

    Args:
        repo_root: Repository root containing the ``.forge`` directory.

    Returns:
        List of :class:`Finding` records. Empty list when the file is absent
        (lessons is an optional artifact — a fresh repo has none yet) or
        when every entry parses cleanly. A single ``BLOCK`` finding when
        :class:`LessonError` is raised, carrying the parser's message.
    """
    path = _lessons_path(repo_root)
    if not path.is_file():
        return []
    # Lazy import: see module docstring for the cycle this dodges.
    from tools.intel.lessons import LessonError, parse  # noqa: PLC0415

    try:
        parse(path)
    except LessonError as exc:
        return [Finding("BLOCK", _TARGET, path, str(exc))]
    return []
