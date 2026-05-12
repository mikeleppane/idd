"""Cross-check REVIEW.code.md lesson-tag rows against ``.forge/intel/lessons.md``.

The ship gate's :func:`tools.ship_gate.partition_by_lesson_severity` raises
``ShipGateError`` when a REVIEW row's Severity cell disagrees with the
lesson's source-of-truth Severity. Catching that drift only at ship time
forces a fix-then-reship cycle. This validator runs the same cross-check at
validate time so ``forge:validate --target review-lesson-tags`` and the
``--target all`` fan-out surface the mismatch with the rest of the
structural findings instead.

Scope: read-only, file-driven, deferred imports for the same cycle-avoidance
reasons documented in ``tools/validate/lessons.py``. Missing REVIEW.code.md
or a REVIEW.code.md with no lesson tags is a no-op (empty list). A REVIEW
that DOES carry lesson tags but the lessons file is absent surfaces one
WARN finding so a setup gap does not silently disarm the gate.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from ._finding import Finding

_TARGET: Final[str] = "review-lesson-tags"


def validate_review_lesson_tags(feature_folder: Path, repo_root: Path) -> list[Finding]:
    """Cross-check lesson-tagged REVIEW.code.md rows against the lessons file.

    For each row in ``feature_folder/REVIEW.code.md`` whose Problem cell
    carries a ``[lesson:L<NNN>]`` tag, verify:

    * the referenced lesson id exists in ``.forge/intel/lessons.md`` —
      BLOCK when absent (typo or stale tag);
    * the row's Severity cell matches the mapping from the lesson's own
      Severity field (CRITICAL->BLOCK, HIGH->HIGH, MEDIUM->MEDIUM,
      LOW->LOW) — BLOCK on mismatch;
    * the referenced lesson is not ``retired`` or ``superseded-by:*`` —
      WARN so the reviewer can re-tag against the active replacement.

    Returns ``[]`` when ``REVIEW.code.md`` is absent or carries no lesson
    tags. When the REVIEW file does carry lesson tags but the lessons
    file is itself absent, surfaces one WARN finding so the operator
    notices the setup gap instead of the cross-check silently passing.

    Args:
        feature_folder: Path to the feature folder containing
            ``REVIEW.code.md``.
        repo_root: Repository root containing ``.forge/intel/lessons.md``.

    Returns:
        List of :class:`Finding` records. Empty when no cross-check
        applies.
    """
    review = feature_folder / "REVIEW.code.md"
    if not review.is_file():
        return []

    # Deferred imports: ship_gate pulls in tools.intel.lessons transitively,
    # which imports tools.constitution_amend, which re-enters tools.validate
    # during its own __init__ load. Top-level imports would deadlock; the
    # call-time imports break the cycle.
    from tools.intel.lessons import LessonError  # noqa: PLC0415
    from tools.intel.lessons import parse as parse_lessons  # noqa: PLC0415
    from tools.ship_gate import (  # noqa: PLC0415
        ShipGateError,
        _iter_review_rows,
        _lesson_to_ship_severity,
    )

    # Stream the table once via the shared parser (one parser, no drift) and
    # collect every lesson-tagged row alongside its severity cell. Short-
    # circuit when no row carries a lesson tag (the common case) and avoid
    # loading lessons.md for nothing.
    try:
        tagged_rows: list[tuple[str, str, str]] = [
            (row.severity, lesson_id, row.location)
            for row in _iter_review_rows(review)
            for lesson_id in row.lesson_tags
        ]
    except ShipGateError as exc:
        return [Finding("BLOCK", _TARGET, review, f"REVIEW.code.md unparseable: {exc}")]

    if not tagged_rows:
        return []

    lessons_md = repo_root / ".forge" / "intel" / "lessons.md"
    if not lessons_md.is_file():
        # Tags reference a file that does not exist yet — likely setup gap
        # (the lessons file has not been initialised). One WARN keeps the
        # ship path open while flagging the missing source of truth.
        return [
            Finding(
                "WARN",
                _TARGET,
                review,
                (
                    f"REVIEW.code.md carries {len(tagged_rows)} [lesson:L<NNN>] "
                    f"tag(s) but {lessons_md} is absent; the cross-check is "
                    "skipped until the lessons file is created"
                ),
            )
        ]

    try:
        lessons = parse_lessons(lessons_md)
    except LessonError:
        # The repo-wide ``validate_lessons`` already surfaces this; do not
        # double-report.
        return []
    # Empty lessons file with tagged rows still falls through to the per-row
    # "unknown lesson" branch so the operator sees the same BLOCK shape
    # whether the file is empty or just missing the referenced id.
    by_id = {le.id: le for le in lessons}

    out: list[Finding] = []
    for severity, lesson_id, location in tagged_rows:
        lesson = by_id.get(lesson_id)
        if lesson is None:
            out.append(
                Finding(
                    "BLOCK",
                    _TARGET,
                    review,
                    (
                        f"REVIEW row references unknown lesson {lesson_id!r} "
                        f"at {location} — stale tag or retired lesson "
                        f"removed from .forge/intel/lessons.md"
                    ),
                )
            )
            continue
        expected = _lesson_to_ship_severity(lesson.severity)
        if severity != expected:
            out.append(
                Finding(
                    "BLOCK",
                    _TARGET,
                    review,
                    (
                        f"REVIEW row Severity={severity!r} at {location} "
                        f"disagrees with lesson {lesson.id} Severity="
                        f"{lesson.severity!r} (expected row Severity={expected!r})"
                    ),
                )
            )
            continue
        # Severity matches; check retirement / supersession last so a
        # severity mismatch on a retired lesson surfaces the more
        # actionable BLOCK rather than only the WARN.
        if lesson.status == "retired" or lesson.status.startswith("superseded-by:"):
            out.append(
                Finding(
                    "WARN",
                    _TARGET,
                    review,
                    (
                        f"REVIEW row at {location} tags lesson {lesson.id} "
                        f"with status {lesson.status!r}; re-tag against the "
                        "active replacement so the trap-memory routing stays "
                        "current"
                    ),
                )
            )
    return out
