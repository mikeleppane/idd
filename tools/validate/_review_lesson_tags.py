"""Cross-check REVIEW.code.md lesson-tag rows against ``.forge/intel/lessons.md``.

The ship gate's :func:`tools.ship_gate.partition_by_lesson_severity` raises
``ShipGateError`` when a REVIEW row's Severity cell disagrees with the
lesson's source-of-truth Severity. Catching that drift only at ship time
forces a fix-then-reship cycle. This validator runs the same cross-check at
validate time so ``forge:validate --target all`` and per-feature CI surface
the mismatch with the rest of the structural findings instead.

Scope: read-only, file-driven, deferred imports for the same cycle-avoidance
reasons documented in ``tools/validate/lessons.py``. Missing REVIEW.code.md
or missing ``.forge/intel/lessons.md`` is a no-op (empty list).
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from ._finding import Finding

_TARGET: Final[str] = "lessons"


def validate_review_md_lesson_tags(feature_root: Path, repo_root: Path) -> list[Finding]:
    """Cross-check lesson-tagged REVIEW.code.md rows against the lessons file.

    For each row in ``feature_root/REVIEW.code.md`` whose Problem cell carries a
    ``[lesson:L<NNN>]`` tag, verify:

    * the referenced lesson id exists in ``.forge/intel/lessons.md``;
    * the row's Severity cell matches the mapping from the lesson's own
      Severity field (CRITICAL→BLOCK, HIGH→HIGH, MEDIUM→MEDIUM, LOW→LOW).

    Args:
        feature_root: Path to the feature folder containing REVIEW.code.md.
        repo_root: Repository root containing ``.forge/intel/lessons.md``.

    Returns:
        List of :class:`Finding` records. Empty when both files are absent,
        when REVIEW.code.md carries no lesson tags, or when every lesson-tag
        row's severity matches the lesson source of truth.
    """
    review = feature_root / "REVIEW.code.md"
    lessons_md = repo_root / ".forge" / "intel" / "lessons.md"
    if not review.is_file() or not lessons_md.is_file():
        return []

    # Deferred imports: ship_gate pulls in tools.intel.lessons transitively,
    # which imports tools.constitution_amend, which re-enters tools.validate
    # during its own __init__ load. Top-level imports would deadlock; the
    # call-time imports break the cycle.
    from tools.intel.lessons import LessonError  # noqa: PLC0415
    from tools.intel.lessons import parse as parse_lessons  # noqa: PLC0415
    from tools.ship_gate import (  # noqa: PLC0415
        _LESSON_SEVERITY_TO_SHIP,
        ShipGateError,
        parse_review_findings,
    )

    try:
        lessons = parse_lessons(lessons_md)
    except LessonError:
        # The repo-wide ``validate_lessons`` already surfaces this; do not
        # double-report.
        return []
    if not lessons:
        return []
    by_id = {le.id: le for le in lessons}

    try:
        findings = parse_review_findings(review)
    except ShipGateError as exc:
        return [Finding("BLOCK", _TARGET, review, f"REVIEW.code.md unparseable: {exc}")]

    out: list[Finding] = []
    for f in findings:
        if not f.is_lesson or f.lesson_id is None:
            continue
        lesson = by_id.get(f.lesson_id)
        if lesson is None:
            out.append(
                Finding(
                    "BLOCK",
                    _TARGET,
                    review,
                    (
                        f"REVIEW row references unknown lesson {f.lesson_id!r} "
                        f"at {f.location} — stale tag or retired lesson "
                        f"removed from .forge/intel/lessons.md"
                    ),
                )
            )
            continue
        expected = _LESSON_SEVERITY_TO_SHIP[lesson.severity]
        if f.severity != expected:
            out.append(
                Finding(
                    "BLOCK",
                    _TARGET,
                    review,
                    (
                        f"REVIEW row Severity={f.severity!r} at {f.location} "
                        f"disagrees with lesson {lesson.id} Severity="
                        f"{lesson.severity!r} (expected row Severity={expected!r})"
                    ),
                )
            )
    return out
