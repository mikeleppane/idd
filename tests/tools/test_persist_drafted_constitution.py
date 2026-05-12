"""Tests for skill-drafted Constitution validation + atomic-pair persistence."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from tools import constitution_amend as am


def _article(
    *,
    number: int,
    title: str = "Sample article",
    level: str = "SHOULD",
    rule: str = "Sample rule body that is long enough to count.",
    reference: str | None = "Team consensus 2026-05.",
    rationale: str | None = "Sample rationale explaining the trade-off.",
    exception: str | None = "None.",
) -> str:
    """Render one article block in the exact shape the loader expects."""
    parts = [f"## Article {number} — {title} [{level}]", f"**Rule:** {rule}"]
    if reference is not None:
        parts.append(f"**Reference:** {reference}")
    if rationale is not None:
        parts.append(f"**Rationale:** {rationale}")
    if exception is not None:
        parts.append(f"**Exception:** {exception}")
    return "\n".join(parts) + "\n"


def _draft(
    *,
    version: str = "0.1.0",
    created: str = "2026-05-11",
    frontmatter_extra: str = "",
    articles: tuple[str, ...] = (),
    skip_frontmatter: bool = False,
) -> str:
    """Render a full draft body: frontmatter + intro + articles."""
    if skip_frontmatter:
        head = ""
    else:
        head = f'---\nversion: {version}\ncreated: "{created}"\n{frontmatter_extra}---\n\n'
    intro = "# Project Constitution\n\nIntro paragraph.\n\n"
    body = "\n".join(articles)
    return head + intro + body


# ---------------------------------------------------------------------------
# validate_drafted_markdown
# ---------------------------------------------------------------------------


def test_validate_drafted_markdown_happy_path_single_article_returns_one_article() -> None:
    body = _draft(articles=(_article(number=1),))
    result = am.validate_drafted_markdown(body)
    assert len(result) == 1
    assert result[0].id == "A1"
    assert result[0].level == "SHOULD"


def test_validate_drafted_markdown_happy_path_five_mixed_levels_returns_five() -> None:
    body = _draft(
        articles=(
            _article(number=1, level="CRITICAL"),
            _article(number=2, level="SHOULD"),
            _article(number=3, level="MAY"),
            _article(number=4, level="CRITICAL"),
            _article(number=5, level="MAY"),
        )
    )
    result = am.validate_drafted_markdown(body)
    assert [a.id for a in result] == ["A1", "A2", "A3", "A4", "A5"]
    assert [a.level for a in result] == ["CRITICAL", "SHOULD", "MAY", "CRITICAL", "MAY"]


def test_validate_drafted_markdown_missing_frontmatter_raises() -> None:
    body = "# Project Constitution\n\n" + _article(number=1)
    with pytest.raises(am.AmendError, match="parse failed"):
        am.validate_drafted_markdown(body)


def test_validate_drafted_markdown_frontmatter_missing_version_raises() -> None:
    body = '---\ncreated: "2026-05-11"\n---\n\n' + _article(number=1)
    with pytest.raises(am.AmendError, match="version"):
        am.validate_drafted_markdown(body)


def test_validate_drafted_markdown_frontmatter_missing_created_raises() -> None:
    body = "---\nversion: 0.1.0\n---\n\n" + _article(number=1)
    with pytest.raises(am.AmendError, match="created"):
        am.validate_drafted_markdown(body)


def test_validate_drafted_markdown_bad_semver_raises() -> None:
    body = _draft(version="banana", articles=(_article(number=1),))
    with pytest.raises(am.AmendError, match="version"):
        am.validate_drafted_markdown(body)


def test_validate_drafted_markdown_bad_date_raises() -> None:
    body = _draft(created="not-a-date", articles=(_article(number=1),))
    with pytest.raises(am.AmendError, match="created"):
        am.validate_drafted_markdown(body)


def test_validate_drafted_markdown_duplicate_article_numbers_raises() -> None:
    body = _draft(
        articles=(
            _article(number=1, title="First"),
            _article(number=1, title="Second copy"),
        )
    )
    with pytest.raises(am.AmendError, match="duplicate"):
        am.validate_drafted_markdown(body)


def test_validate_drafted_markdown_non_monotonic_articles_raises() -> None:
    # `## Article 3` followed by `## Article 2` previously slipped past draft
    # validation and only failed at the structural validator inside
    # persist_drafted_constitution — two error surfaces for one root cause.
    body = _draft(
        articles=(
            _article(number=1, title="First"),
            _article(number=3, title="Way out front"),
            _article(number=2, title="Backwards"),
        )
    )
    with pytest.raises(am.AmendError, match=r"monotonic|gap"):
        am.validate_drafted_markdown(body)


def test_validate_drafted_markdown_strictly_descending_articles_raises() -> None:
    # Body opens A1, A3 — the gap check fires; then a follow-up A2 would
    # have been the monotonic-trigger if we ever reached it.
    body = _draft(
        articles=(
            _article(number=1, title="First"),
            _article(number=2, title="Second"),
            _article(number=4, title="Skipped three"),
            _article(number=3, title="Backwards"),
        )
    )
    # First failure surfaces — could be gap (A4 instead of A3) or monotonic.
    with pytest.raises(am.AmendError, match=r"gap|monotonic"):
        am.validate_drafted_markdown(body)


def test_validate_drafted_markdown_article_numbering_gap_raises() -> None:
    body = _draft(
        articles=(
            _article(number=1, title="First"),
            _article(number=3, title="Skipped two"),
        )
    )
    with pytest.raises(am.AmendError, match="gap"):
        am.validate_drafted_markdown(body)


def test_validate_drafted_markdown_articles_must_start_at_one() -> None:
    body = _draft(articles=(_article(number=2),))
    with pytest.raises(am.AmendError, match="gap"):
        am.validate_drafted_markdown(body)


def test_validate_drafted_markdown_calendar_invalid_date_raises_month() -> None:
    body = _draft(created="2026-13-99", articles=(_article(number=1),))
    with pytest.raises(am.AmendError, match="created"):
        am.validate_drafted_markdown(body)


def test_validate_drafted_markdown_calendar_invalid_date_raises_feb30() -> None:
    body = _draft(created="2026-02-30", articles=(_article(number=1),))
    with pytest.raises(am.AmendError, match="created"):
        am.validate_drafted_markdown(body)


def test_validate_drafted_markdown_non_leap_feb29_raises() -> None:
    body = _draft(created="2025-02-29", articles=(_article(number=1),))
    with pytest.raises(am.AmendError, match="created"):
        am.validate_drafted_markdown(body)


def test_validate_drafted_markdown_leap_feb29_accepted() -> None:
    # 2024 is a leap year — Feb 29 must round-trip cleanly.
    body = _draft(created="2024-02-29", articles=(_article(number=1),))
    result = am.validate_drafted_markdown(body)
    assert len(result) == 1


def test_validate_drafted_markdown_zero_date_raises() -> None:
    body = _draft(created="0000-00-00", articles=(_article(number=1),))
    with pytest.raises(am.AmendError, match="created"):
        am.validate_drafted_markdown(body)


def test_validate_drafted_markdown_malformed_level_raises() -> None:
    # The header regex rejects unknown level names; the parser surfaces ConstitutionError.
    raw_article = (
        "## Article 1 — Bad level [URGENT]\n"
        "**Rule:** body\n"
        "**Reference:** ref\n"
        "**Rationale:** because\n"
        "**Exception:** None.\n"
    )
    body = _draft(articles=(raw_article,))
    with pytest.raises(am.AmendError, match=r"parse failed|malformed"):
        am.validate_drafted_markdown(body)


def test_validate_drafted_markdown_empty_rule_raises() -> None:
    body = _draft(articles=(_article(number=1, rule=""),))
    with pytest.raises(am.AmendError, match=r"(?i)A1.*rule|rule.*A1"):
        am.validate_drafted_markdown(body)


def test_validate_drafted_markdown_missing_reference_raises() -> None:
    body = _draft(articles=(_article(number=1, reference=None),))
    with pytest.raises(am.AmendError, match=r"(?i)A1.*reference|reference.*A1"):
        am.validate_drafted_markdown(body)


def test_validate_drafted_markdown_missing_rationale_raises() -> None:
    body = _draft(articles=(_article(number=1, rationale=None),))
    with pytest.raises(am.AmendError, match=r"(?i)A1.*rationale|rationale.*A1"):
        am.validate_drafted_markdown(body)


def test_validate_drafted_markdown_article_over_word_cap_raises() -> None:
    huge_rule = "word " * 1200  # 1200 words alone, over 1153 cap
    body = _draft(articles=(_article(number=1, rule=huge_rule.strip()),))
    with pytest.raises(am.AmendError, match=r"A1") as exc:
        am.validate_drafted_markdown(body)
    # Surface the offending word count.
    assert "1153" in str(exc.value) or "120" in str(exc.value)


def test_validate_drafted_markdown_zero_articles_raises() -> None:
    body = (
        '---\nversion: 0.1.0\ncreated: "2026-05-11"\n---\n\n# Project Constitution\n\nIntro only.\n'
    )
    with pytest.raises(am.AmendError, match="zero articles"):
        am.validate_drafted_markdown(body)


# ---------------------------------------------------------------------------
# persist_drafted_constitution
# ---------------------------------------------------------------------------


def _good_body(*, version: str = "0.1.0") -> str:
    return _draft(
        version=version,
        articles=(
            _article(number=1, level="CRITICAL", title="Secrets via vault"),
            _article(number=2, level="SHOULD", title="Test coverage floor"),
        ),
    )


def test_persist_drafted_constitution_happy_path_writes_both_files(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".forge").mkdir(parents=True)
    decisions = repo / "decisions.md"

    body = _good_body()
    result = am.persist_drafted_constitution(
        repo_root=repo,
        body=body,
        decisions_path=decisions,
        today=date(2026, 5, 11),
    )

    assert result == repo / ".forge" / "CONSTITUTION.md"
    written = result.read_text(encoding="utf-8")
    assert written == body  # byte-equal to input
    entry = decisions.read_text(encoding="utf-8")
    assert "Constitution bootstrap: v0.1.0 (skill-drafted)" in entry
    assert "2 article(s)" in entry
    assert "2026-05-11" in entry


def test_persist_drafted_constitution_refuses_when_file_exists(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".forge").mkdir(parents=True)
    constitution = repo / ".forge" / "CONSTITUTION.md"
    constitution.write_text("preexisting\n", encoding="utf-8")
    decisions = repo / "decisions.md"

    with pytest.raises(am.AmendError, match="already exists"):
        am.persist_drafted_constitution(
            repo_root=repo,
            body=_good_body(),
            decisions_path=decisions,
            today=date(2026, 5, 11),
        )
    # Untouched.
    assert constitution.read_text(encoding="utf-8") == "preexisting\n"
    assert not decisions.exists()


def test_persist_drafted_constitution_validate_failure_leaves_disk_clean(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".forge").mkdir(parents=True)
    decisions = repo / "decisions.md"

    bad_body = _draft(
        articles=(_article(number=1, rule=""),),
    )

    with pytest.raises(am.AmendError):
        am.persist_drafted_constitution(
            repo_root=repo,
            body=bad_body,
            decisions_path=decisions,
            today=date(2026, 5, 11),
        )

    assert not (repo / ".forge" / "CONSTITUTION.md").exists()
    assert not decisions.exists()


def test_persist_drafted_constitution_structural_failure_leaves_disk_clean(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    (repo / ".forge").mkdir(parents=True)
    decisions = repo / "decisions.md"

    def _fake_validate(_target: Path) -> None:
        raise am.AmendError("forced structural failure")

    monkeypatch.setattr(am, "_validate_constitution_body", _fake_validate)

    with pytest.raises(am.AmendError, match="structural failure"):
        am.persist_drafted_constitution(
            repo_root=repo,
            body=_good_body(),
            decisions_path=decisions,
            today=date(2026, 5, 11),
        )

    assert not (repo / ".forge" / "CONSTITUTION.md").exists()
    assert not decisions.exists()


def test_persist_drafted_constitution_append_failure_rolls_back_both(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    (repo / ".forge").mkdir(parents=True)
    decisions = repo / "decisions.md"
    assert not decisions.exists()

    def _raise(_path: Path, _entry: str) -> None:
        raise OSError("simulated append failure")

    monkeypatch.setattr(am, "append_decisions_atomic", _raise)

    with pytest.raises(am.AmendError, match=r"decisions\.md append failed"):
        am.persist_drafted_constitution(
            repo_root=repo,
            body=_good_body(),
            decisions_path=decisions,
            today=date(2026, 5, 11),
        )

    assert not (repo / ".forge" / "CONSTITUTION.md").exists()
    # decisions.md was auto-created; rollback must remove it.
    assert not decisions.exists()


def test_persist_drafted_constitution_append_failure_preserves_existing_decisions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    (repo / ".forge").mkdir(parents=True)
    decisions = repo / "decisions.md"
    original_text = "# Decisions\n\n## 2026-01-01 — earlier\n**Context:** keep me.\n"
    decisions.write_text(original_text, encoding="utf-8")

    def _raise(_path: Path, _entry: str) -> None:
        raise OSError("simulated append failure")

    monkeypatch.setattr(am, "append_decisions_atomic", _raise)

    with pytest.raises(am.AmendError, match=r"decisions\.md append failed"):
        am.persist_drafted_constitution(
            repo_root=repo,
            body=_good_body(),
            decisions_path=decisions,
            today=date(2026, 5, 11),
        )

    assert not (repo / ".forge" / "CONSTITUTION.md").exists()
    # Pre-existing decisions.md must NOT be deleted.
    assert decisions.read_text(encoding="utf-8") == original_text


def test_persist_drafted_constitution_validate_failure_preserves_existing_decisions(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    (repo / ".forge").mkdir(parents=True)
    decisions = repo / "decisions.md"
    original_text = "# Decisions\n\n## 2025-01-01 — prior entry\n**Context:** keep me.\n"
    decisions.write_text(original_text, encoding="utf-8")

    # Non-monotonic article numbering trips validate_drafted_markdown before
    # any disk mutation; the pre-existing decisions.md must survive byte-equal.
    bad_body = _draft(
        articles=(
            _article(number=1, title="First"),
            _article(number=3, title="Way out front"),
            _article(number=2, title="Backwards"),
        )
    )

    with pytest.raises(am.AmendError):
        am.persist_drafted_constitution(
            repo_root=repo,
            body=bad_body,
            decisions_path=decisions,
            today=date(2026, 5, 11),
        )

    assert not (repo / ".forge" / "CONSTITUTION.md").exists()
    assert decisions.read_text(encoding="utf-8") == original_text


def test_persist_drafted_constitution_structural_failure_preserves_existing_decisions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    (repo / ".forge").mkdir(parents=True)
    decisions = repo / "decisions.md"
    original_text = "# Decisions\n\n## 2025-01-01 — prior entry\n**Context:** keep me.\n"
    decisions.write_text(original_text, encoding="utf-8")

    def _fake_validate(_target: Path) -> None:
        raise am.AmendError("forced structural failure")

    monkeypatch.setattr(am, "_validate_constitution_body", _fake_validate)

    with pytest.raises(am.AmendError, match="structural failure"):
        am.persist_drafted_constitution(
            repo_root=repo,
            body=_good_body(),
            decisions_path=decisions,
            today=date(2026, 5, 11),
        )

    assert not (repo / ".forge" / "CONSTITUTION.md").exists()
    assert decisions.read_text(encoding="utf-8") == original_text


def test_persist_drafted_constitution_today_overrides_default(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".forge").mkdir(parents=True)
    decisions = repo / "decisions.md"

    am.persist_drafted_constitution(
        repo_root=repo,
        body=_good_body(version="1.2.3"),
        decisions_path=decisions,
        today=date(2023, 7, 4),
    )

    entry = decisions.read_text(encoding="utf-8")
    assert "2023-07-04" in entry
    assert "v1.2.3" in entry


def test_persist_drafted_constitution_writes_body_byte_equal_to_input(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".forge").mkdir(parents=True)
    decisions = repo / "decisions.md"

    body = _good_body()
    result = am.persist_drafted_constitution(
        repo_root=repo,
        body=body,
        decisions_path=decisions,
        today=date(2026, 5, 11),
    )

    assert result.read_text(encoding="utf-8") == body
