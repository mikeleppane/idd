"""Reproducers for the lessons parser hardening contract.

Pins the canonical-normalized round-trip contract:

  * Multi-line trap / avoidance bodies preserve LF newlines.
  * CRLF normalises to LF on write (canonical form).
  * Trailing whitespace lost (canonical form) — documented.
  * Body lines whose stripped form re-parses as a ``**Marker:**`` field
    are escaped on write with a 4-space prefix and unescaped on read so
    no injection occurs.
  * Nested triple-backtick fences obey the CommonMark close rule (the
    inner shorter fence stays as body content; only a matching-or-longer
    bare fence closes the outer block).
"""

from __future__ import annotations

from datetime import date

import pytest

from tools.intel import lessons
from tools.intel.lessons import LessonSeverity, LessonStatus


def _make_lesson(
    *,
    lesson_id: str = "L001",
    trap: str = "trap body",
    avoidance: str = "avoidance body",
    tags: tuple[str, ...] = ("dispatch",),
    severity: LessonSeverity = "HIGH",
    status: LessonStatus = "active",
) -> lessons.Lesson:
    return lessons.Lesson(
        id=lesson_id,
        captured=date(2026, 5, 12),
        captured_from="feature-x",
        resolved_by="manual",
        trap=trap,
        avoidance=avoidance,
        tags=tags,
        severity=severity,
        status=status,
        body_words=len((trap + " " + avoidance).split()),
    )


def _serialize_and_parse(draft: lessons.Lesson) -> lessons.Lesson:
    """Run a single lesson through the serializer and parser."""
    body = (
        '---\nversion: 0.1.0\ncreated: "2026-05-12"\n---\n\n# FORGE Lessons\n\n'
        + lessons._serialize_lesson(draft)
    )
    parsed = lessons.parse_text(body)
    assert len(parsed) == 1
    return parsed[0]


def test_multi_line_lf_trap_round_trips_preserved() -> None:
    """Newlines in trap body survive serialize -> parse."""
    draft = _make_lesson(trap="Real bug.\nLine two of trap.\nLine three.")
    parsed = _serialize_and_parse(draft)
    assert parsed.trap == "Real bug.\nLine two of trap.\nLine three."


def test_crlf_trap_normalises_to_lf() -> None:
    """CRLF input writes as LF (canonical form). Round-trip yields LF."""
    draft = _make_lesson(trap="Real bug.\r\nLine two.\r\nLine three.")
    parsed = _serialize_and_parse(draft)
    assert parsed.trap == "Real bug.\nLine two.\nLine three."


def test_body_line_matching_field_marker_does_not_inject() -> None:
    """A trap body whose continuation line looks like ``**Trap:** decoy``.

    Pre-hardening this would have switched the active field mid-body and
    truncated the real trap. After escaping on write, the continuation
    survives byte-stable.
    """
    draft = _make_lesson(trap="Real bug.\n**Trap:** decoy line\nmore text")
    parsed = _serialize_and_parse(draft)
    assert parsed.trap == "Real bug.\n**Trap:** decoy line\nmore text"


def test_body_line_matching_avoidance_marker_does_not_inject() -> None:
    draft = _make_lesson(
        trap="Outer trap.",
        avoidance="Real avoidance.\n**Captured:** 2026-05-12 from feature x\nstill avoidance",
    )
    parsed = _serialize_and_parse(draft)
    assert (
        parsed.avoidance
        == "Real avoidance.\n**Captured:** 2026-05-12 from feature x\nstill avoidance"
    )


def test_nested_triple_backtick_fence_survives_round_trip() -> None:
    """A trap containing a ```` ``` ```` fence inside its body must parse cleanly."""
    trap = "Outer prose.\n```py\nos.system('echo')\n```\nAfter fence."
    draft = _make_lesson(trap=trap)
    parsed = _serialize_and_parse(draft)
    assert parsed.trap == trap


def test_inner_shorter_fence_stays_as_body_content() -> None:
    """A fence with 4 backticks must not close on a stray 3-backtick line.

    CommonMark close rule: the closing run length must be >= opening run
    length AND no info string. A nested 3-backtick fence inside an
    outer 4-backtick fence stays as body content.
    """
    trap = "Outer fence example:\n````md\n```py\nfoo = 1\n```\n````\nAfter."
    draft = _make_lesson(trap=trap)
    parsed = _serialize_and_parse(draft)
    assert parsed.trap == trap


def test_field_marker_at_non_column_zero_is_body_content() -> None:
    """``**Trap:** ...`` indented past column 0 must be treated as body content."""
    draft = _make_lesson(trap="Plain prose.\n  **Trap:** still body, not a new field.")
    parsed = _serialize_and_parse(draft)
    assert parsed.trap == "Plain prose.\n  **Trap:** still body, not a new field."


def test_trailing_whitespace_lost_canonical_contract() -> None:
    """Trailing whitespace on body lines is lost on round-trip (canonical form).

    Documented in the canonical-normalized contract. A future caller that
    needs byte-exact preservation should reach for a different round-trip
    primitive — the canonical writer always emits the trimmed form.
    """
    draft = _make_lesson(trap="line one    \nline two\t")
    parsed = _serialize_and_parse(draft)
    assert parsed.trap == "line one\nline two"


@pytest.mark.parametrize("marker", ["Captured", "Resolved by", "Trap", "Avoidance"])
def test_every_field_marker_shape_escapes_on_continuation(marker: str) -> None:
    """Every marker name in :data:`_FIELD_KEYS` must escape on continuation."""
    body = f"opening prose\n**{marker}:** decoy\ntail"
    draft = _make_lesson(trap=body)
    parsed = _serialize_and_parse(draft)
    assert parsed.trap == body
