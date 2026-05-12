"""Tests for the REVIEW.code.md / lessons.md severity cross-check validator.

Covers the in-process validator surface; CLI integration (``--target
review-lesson-tags`` dispatch) lives in
``tests/tools/test_validate_review_lesson_tags_cli.py``.
"""

from __future__ import annotations

from pathlib import Path

from tools.validate.review_lesson_tags import validate_review_lesson_tags

_LESSONS_BODY = """---
version: 0.1.0
created: "2026-05-11"
---

# FORGE Lessons

## L001 — example trap
**Captured:** 2026-05-11 from feature 2026-05-11-demo
**Resolved by:** manual
**Trap:** t
**Avoidance:** a
**Tags:** dispatch
**Severity:** HIGH
**Status:** active
"""

_LESSONS_BODY_RETIRED = """---
version: 0.1.0
created: "2026-05-11"
---

# FORGE Lessons

## L001 — retired trap
**Captured:** 2026-05-11 from feature 2026-05-11-demo
**Resolved by:** manual
**Trap:** t
**Avoidance:** a
**Tags:** dispatch
**Severity:** HIGH
**Status:** retired
"""

_LESSONS_BODY_SUPERSEDED = """---
version: 0.1.0
created: "2026-05-11"
---

# FORGE Lessons

## L001 — older trap
**Captured:** 2026-05-11 from feature 2026-05-11-demo
**Resolved by:** manual
**Trap:** t
**Avoidance:** a
**Tags:** dispatch
**Severity:** HIGH
**Status:** superseded-by:L002

## L002 — replacement trap
**Captured:** 2026-05-11 from feature 2026-05-11-demo
**Resolved by:** manual
**Trap:** t
**Avoidance:** a
**Tags:** dispatch
**Severity:** HIGH
**Status:** active
"""

_LESSONS_BODY_CRITICAL = """---
version: 0.1.0
created: "2026-05-11"
---

# FORGE Lessons

## L007 — critical trap
**Captured:** 2026-05-11 from feature 2026-05-11-demo
**Resolved by:** manual
**Trap:** t
**Avoidance:** a
**Tags:** dispatch
**Severity:** CRITICAL
**Status:** active
"""


def _write_lessons(repo_root: Path, body: str = _LESSONS_BODY) -> Path:
    path = repo_root / ".forge" / "intel" / "lessons.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _write_review(feature: Path, *, rows: list[str]) -> Path:
    feature.mkdir(parents=True, exist_ok=True)
    body = (
        "---\nspec: 2026-05-11-demo\ntarget: code\nstatus: open\ncycles: 1\n---\n\n"
        "# Findings\n\n"
        "| ID | Severity | Status | Resolved by | Location | Problem | Recommended Fix | Source |\n"
        "|----|----------|--------|-------------|----------|---------|-----------------|--------|\n"
        + "\n".join(rows)
        + "\n"
    )
    path = feature / "REVIEW.code.md"
    path.write_text(body, encoding="utf-8")
    return path


def test_missing_review_returns_empty(tmp_path: Path) -> None:
    _write_lessons(tmp_path)
    feature = tmp_path / "feat"
    feature.mkdir()
    assert validate_review_lesson_tags(feature, tmp_path) == []


def test_review_with_no_lesson_tags_returns_empty(tmp_path: Path) -> None:
    _write_lessons(tmp_path)
    feature = tmp_path / "feat"
    _write_review(
        feature,
        rows=["| F-1 | HIGH | open | | src/x.py:1 | plain message | f | self |"],
    )
    assert validate_review_lesson_tags(feature, tmp_path) == []


def test_missing_lessons_with_tagged_row_warns(tmp_path: Path) -> None:
    """REVIEW tags a lesson but lessons.md is absent -> WARN (not BLOCK)."""
    feature = tmp_path / "feat"
    _write_review(
        feature,
        rows=["| F-1 | HIGH | open | | src/x.py:1 | [lesson:L001] m | f | self |"],
    )
    findings = validate_review_lesson_tags(feature, tmp_path)
    assert len(findings) == 1
    assert findings[0].severity == "WARN"
    assert "absent" in findings[0].message


def test_aligned_severity_passes(tmp_path: Path) -> None:
    _write_lessons(tmp_path)
    feature = tmp_path / "feat"
    _write_review(
        feature,
        rows=["| F-1 | HIGH | open | | src/x.py:1 | [lesson:L001] m | f | self |"],
    )
    assert validate_review_lesson_tags(feature, tmp_path) == []


def test_mismatched_severity_blocks(tmp_path: Path) -> None:
    """Row Severity=BLOCK but lesson L001 Severity=HIGH -> BLOCK finding."""
    _write_lessons(tmp_path)
    feature = tmp_path / "feat"
    _write_review(
        feature,
        rows=["| F-1 | BLOCK | open | | src/x.py:1 | [lesson:L001] m | f | self |"],
    )
    findings = validate_review_lesson_tags(feature, tmp_path)
    assert len(findings) == 1
    assert findings[0].severity == "BLOCK"
    assert "disagrees with lesson L001" in findings[0].message


def test_block_matches_critical_via_rename_map(tmp_path: Path) -> None:
    """Row Severity=BLOCK, lesson L007 Severity=CRITICAL -> match (no finding)."""
    _write_lessons(tmp_path, body=_LESSONS_BODY_CRITICAL)
    feature = tmp_path / "feat"
    _write_review(
        feature,
        rows=["| F-1 | BLOCK | open | | src/x.py:1 | [lesson:L007] m | f | self |"],
    )
    assert validate_review_lesson_tags(feature, tmp_path) == []


def test_unknown_lesson_id_blocks(tmp_path: Path) -> None:
    _write_lessons(tmp_path)
    feature = tmp_path / "feat"
    _write_review(
        feature,
        rows=["| F-1 | HIGH | open | | src/x.py:1 | [lesson:L999] m | f | self |"],
    )
    findings = validate_review_lesson_tags(feature, tmp_path)
    assert len(findings) == 1
    assert findings[0].severity == "BLOCK"
    assert "unknown lesson 'L999'" in findings[0].message


def test_batches_multiple_mismatches(tmp_path: Path) -> None:
    _write_lessons(tmp_path)
    feature = tmp_path / "feat"
    _write_review(
        feature,
        rows=[
            "| F-1 | BLOCK | open | | src/a.py:1 | [lesson:L001] a | f | self |",
            "| F-2 | LOW | open | | src/b.py:1 | [lesson:L001] b | f | self |",
        ],
    )
    findings = validate_review_lesson_tags(feature, tmp_path)
    assert len(findings) == 2


def test_article_tag_rows_ignored(tmp_path: Path) -> None:
    """Constitution-tag rows are not in scope for this cross-check."""
    _write_lessons(tmp_path)
    feature = tmp_path / "feat"
    _write_review(
        feature,
        rows=["| F-1 | HIGH | open | | src/x.py:1 | [constitution:A1] m | f | self |"],
    )
    assert validate_review_lesson_tags(feature, tmp_path) == []


def test_mixed_article_and_lesson_tag_row_checks_only_lesson(tmp_path: Path) -> None:
    """Row with both [constitution:A2] and [lesson:L001] -> only lesson tag is cross-checked."""
    _write_lessons(tmp_path)
    feature = tmp_path / "feat"
    _write_review(
        feature,
        rows=[
            "| F-1 | HIGH | open | | src/x.py:1 | [constitution:A2] [lesson:L001] m | f | self |",
        ],
    )
    assert validate_review_lesson_tags(feature, tmp_path) == []


def test_retired_lesson_warns(tmp_path: Path) -> None:
    _write_lessons(tmp_path, body=_LESSONS_BODY_RETIRED)
    feature = tmp_path / "feat"
    _write_review(
        feature,
        rows=["| F-1 | HIGH | open | | src/x.py:1 | [lesson:L001] m | f | self |"],
    )
    findings = validate_review_lesson_tags(feature, tmp_path)
    assert len(findings) == 1
    assert findings[0].severity == "WARN"
    assert "retired" in findings[0].message


def test_superseded_lesson_warns(tmp_path: Path) -> None:
    _write_lessons(tmp_path, body=_LESSONS_BODY_SUPERSEDED)
    feature = tmp_path / "feat"
    _write_review(
        feature,
        rows=["| F-1 | HIGH | open | | src/x.py:1 | [lesson:L001] m | f | self |"],
    )
    findings = validate_review_lesson_tags(feature, tmp_path)
    assert len(findings) == 1
    assert findings[0].severity == "WARN"
    assert "superseded-by:L002" in findings[0].message
