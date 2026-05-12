"""WS3 lesson-side hardening tests: per-entry cap, tag casefold, today semantics,
strict header padding, template polish.

These tests pin behaviour for direct dataclass construction (the "back door"
into the writer that bypasses the parser's per-field cap) and for the
parser-level surface changes: case-insensitive tag matching, strict 3-digit
header id, and the ``today`` parameter on :func:`tools.intel.lessons.append`.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from tools.intel import lessons
from tools.validate import validate_lessons

TEMPLATE_PATH = Path(__file__).resolve().parents[2] / "templates" / "intel" / "lessons.md"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_HEADER = '---\nversion: 0.1.0\ncreated: "2026-05-11"\n---\n\n# FORGE Lessons\n\n'


def _entry(
    *,
    nid: str = "L001",
    title: str = "Example trap",
    captured: str = "2026-05-11",
    feature: str = "m0-example",
    resolved_by: str = "manual",
    trap: str = "Subagent did the wrong thing.",
    avoidance: str = "Subagent should do the right thing.",
    tags: str = "dispatch, validation",
    severity: str = "LOW",
    status: str = "active",
) -> str:
    return (
        f"## {nid} — {title}\n"
        f"**Captured:** {captured} from feature {feature}\n"
        f"**Resolved by:** {resolved_by}\n"
        f"**Trap:** {trap}\n"
        f"**Avoidance:** {avoidance}\n"
        f"**Tags:** {tags}\n"
        f"**Severity:** {severity}\n"
        f"**Status:** {status}\n"
    )


def _file(*entries: str) -> str:
    return _HEADER + "\n".join(entries)


def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _make_lesson(**overrides: object) -> lessons.Lesson:
    defaults: dict[str, object] = {
        "id": "L001",
        "captured": date(2026, 5, 11),
        "captured_from": "m0-example",
        "resolved_by": "manual",
        "trap": "Subagent did the wrong thing.",
        "avoidance": "Subagent should do the right thing.",
        "tags": ("dispatch", "validation"),
        "severity": "LOW",
        "status": "active",
        "body_words": 0,
    }
    defaults.update(overrides)
    return lessons.Lesson(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Per-entry length cap via __post_init__
# ---------------------------------------------------------------------------


def test_lesson_post_init_rejects_oversize_trap_via_direct_construction() -> None:
    """Constructing a Lesson directly with a 2001-char trap raises LessonError.

    The parser caps at 1000 chars; the dataclass defends a looser back-door
    cap of 2000 against callers (tests, future CLIs, library users) bypassing
    the parser path.
    """
    long_trap = "a" * 2001
    with pytest.raises(lessons.LessonError, match=r"trap exceeds 2000 chars"):
        _make_lesson(trap=long_trap)


def test_lesson_post_init_rejects_oversize_avoidance_via_direct_construction() -> None:
    long_avoidance = "b" * 2001
    with pytest.raises(lessons.LessonError, match=r"avoidance exceeds 2000 chars"):
        _make_lesson(avoidance=long_avoidance)


def test_lesson_post_init_rejects_combined_words_over_max() -> None:
    """Combined trap + avoidance word count above MAX_LESSON_WORDS is refused.

    The dataclass enforces the same 600-word cap that the dispatch-budget
    filter applies later; a single CRITICAL lesson bloated past 600 words
    would otherwise crash the load-and-filter relevance pass for callers
    who never see the parser at all.
    """
    trap_words = "word " * 300  # 300 words
    avoidance_words = "word " * 301  # 301 words => 601 combined
    with pytest.raises(lessons.LessonError, match=r"trap\+avoidance exceeds 600 words"):
        _make_lesson(trap=trap_words.strip(), avoidance=avoidance_words.strip())


def test_lesson_post_init_accepts_exactly_max_words_combined() -> None:
    """The combined-word cap is inclusive — exactly 600 words is allowed."""
    trap_words = ("word " * 300).strip()  # 300 words
    avoidance_words = ("word " * 300).strip()  # 300 words => 600 combined
    lesson = _make_lesson(trap=trap_words, avoidance=avoidance_words)
    assert lesson.trap.count("word") == 300


def test_lesson_post_init_accepts_template_seed_entry() -> None:
    """The shipped template parses without tripping __post_init__."""
    assert TEMPLATE_PATH.exists()
    parsed = lessons.parse(TEMPLATE_PATH)
    assert len(parsed) == 1


# ---------------------------------------------------------------------------
# today param semantics on append
# ---------------------------------------------------------------------------


def test_append_today_stamps_header_only_on_file_creation(tmp_path: Path) -> None:
    """First call stamps ``today`` into the auto-generated frontmatter header."""
    draft = _make_lesson(id="L001", captured=date(2026, 5, 11))
    lessons.append(tmp_path, draft, today=date(2026, 1, 15))
    text = (tmp_path / ".forge" / "intel" / "lessons.md").read_text(encoding="utf-8")
    assert 'created: "2026-01-15"' in text


def test_append_today_ignored_on_subsequent_appends(tmp_path: Path) -> None:
    """Second-call ``today`` is silently ignored — frontmatter is immutable."""
    first = _make_lesson(id="L001", captured=date(2026, 5, 11))
    lessons.append(tmp_path, first, today=date(2026, 1, 15))
    second = _make_lesson(id="L002", captured=date(2026, 5, 12))
    lessons.append(tmp_path, second, today=date(2099, 12, 31))
    text = (tmp_path / ".forge" / "intel" / "lessons.md").read_text(encoding="utf-8")
    assert 'created: "2026-01-15"' in text
    assert "2099-12-31" not in text


def test_append_entry_captured_reflects_draft_not_today(tmp_path: Path) -> None:
    """The per-entry ``Captured:`` line always reflects ``draft.captured``."""
    draft = _make_lesson(id="L001", captured=date(2026, 3, 7))
    lessons.append(tmp_path, draft, today=date(2026, 1, 15))
    text = (tmp_path / ".forge" / "intel" / "lessons.md").read_text(encoding="utf-8")
    assert "**Captured:** 2026-03-07 from feature" in text


# ---------------------------------------------------------------------------
# Tag vocabulary casefold tolerance
# ---------------------------------------------------------------------------


def test_parse_accepts_capitalized_tags(tmp_path: Path) -> None:
    """Tags are matched case-insensitively; storage is canonical lowercase."""
    body = _file(_entry(tags="Dispatch, Fixtures"))
    path = _write(tmp_path / "lessons.md", body)
    parsed = lessons.parse(path)
    assert parsed[0].tags == ("dispatch", "fixtures")


def test_parse_accepts_uppercase_tag(tmp_path: Path) -> None:
    body = _file(_entry(tags="DISPATCH"))
    path = _write(tmp_path / "lessons.md", body)
    parsed = lessons.parse(path)
    assert parsed[0].tags == ("dispatch",)


def test_parse_rejects_unknown_tag_after_casefold(tmp_path: Path) -> None:
    """Tags outside the vocabulary stay rejected; case-folding does not
    accidentally promote a typo to a hit.
    """
    body = _file(_entry(tags="Mixed-Case"))
    path = _write(tmp_path / "lessons.md", body)
    with pytest.raises(lessons.LessonError, match=r"Mixed-Case"):
        lessons.parse(path)


def test_parse_error_preserves_original_tag_spelling(tmp_path: Path) -> None:
    """The error message preserves the source spelling so the author can
    grep for the offending row, while the matcher uses casefolded form."""
    body = _file(_entry(tags="dispatch, MadeUp"))
    path = _write(tmp_path / "lessons.md", body)
    with pytest.raises(lessons.LessonError, match=r"MadeUp") as exc:
        lessons.parse(path)
    assert "madeup" not in str(exc.value).split("not in")[0]


def test_parse_dedupes_tags_after_casefold(tmp_path: Path) -> None:
    """``Dispatch, dispatch`` collapses to a single canonical tag."""
    body = _file(_entry(tags="Dispatch, dispatch"))
    path = _write(tmp_path / "lessons.md", body)
    parsed = lessons.parse(path)
    assert parsed[0].tags == ("dispatch",)


# ---------------------------------------------------------------------------
# Header regex tightened to 3-digit zero-padded ids
# ---------------------------------------------------------------------------


def test_parse_accepts_three_digit_id(tmp_path: Path) -> None:
    body = _file(_entry(nid="L007"))
    path = _write(tmp_path / "lessons.md", body)
    parsed = lessons.parse(path)
    assert parsed[0].id == "L007"


def test_parse_rejects_one_digit_id(tmp_path: Path) -> None:
    """``## L7 — title`` is malformed — header regex requires three digits."""
    bad_entry = (
        "## L7 — Bad id\n"
        "**Captured:** 2026-05-11 from feature x\n"
        "**Resolved by:** manual\n"
        "**Trap:** x\n"
        "**Avoidance:** y\n"
        "**Tags:** dispatch\n"
        "**Severity:** LOW\n"
        "**Status:** active\n"
    )
    path = _write(tmp_path / "lessons.md", _file(bad_entry))
    with pytest.raises(lessons.LessonError, match=r"malformed lesson header"):
        lessons.parse(path)


def test_parse_rejects_four_digit_id(tmp_path: Path) -> None:
    bad_entry = (
        "## L9999 — Bad id\n"
        "**Captured:** 2026-05-11 from feature x\n"
        "**Resolved by:** manual\n"
        "**Trap:** x\n"
        "**Avoidance:** y\n"
        "**Tags:** dispatch\n"
        "**Severity:** LOW\n"
        "**Status:** active\n"
    )
    path = _write(tmp_path / "lessons.md", _file(bad_entry))
    with pytest.raises(lessons.LessonError, match=r"malformed lesson header"):
        lessons.parse(path)


# ---------------------------------------------------------------------------
# Template still validates cleanly
# ---------------------------------------------------------------------------


def test_template_lessons_file_passes_validate_lessons(tmp_path: Path) -> None:
    """Copy the shipped template into a fresh repo root and confirm the
    validator surfaces zero findings."""
    intel = tmp_path / ".forge" / "intel"
    intel.mkdir(parents=True)
    (intel / "lessons.md").write_text(TEMPLATE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    assert validate_lessons(tmp_path) == []
