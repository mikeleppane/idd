"""Tests for validate_constitution structural checks."""

from __future__ import annotations

from pathlib import Path

from tools import validate

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "_validate"


def test_pass_constitution_returns_no_findings() -> None:
    findings = validate.validate_constitution(FIXTURES / "constitution_pass.md")
    assert findings == []


def test_no_frontmatter_blocks() -> None:
    findings = validate.validate_constitution(FIXTURES / "constitution_no_frontmatter.md")
    assert any(f.severity == "BLOCK" and "frontmatter" in f.message.lower() for f in findings)


def test_bad_article_header_blocks() -> None:
    findings = validate.validate_constitution(FIXTURES / "constitution_bad_article_header.md")
    assert any(f.severity == "BLOCK" and "article header" in f.message.lower() for f in findings)


def test_non_monotonic_numbering_blocks() -> None:
    findings = validate.validate_constitution(FIXTURES / "constitution_non_monotonic.md")
    assert any(f.severity == "BLOCK" and "monotonic" in f.message.lower() for f in findings)


def test_missing_rule_field_blocks() -> None:
    findings = validate.validate_constitution(FIXTURES / "constitution_missing_rule_field.md")
    assert any(
        f.severity == "BLOCK" and "article 1" in f.message.lower() and "rule" in f.message.lower()
        for f in findings
    )


def test_missing_exception_field_blocks() -> None:
    findings = validate.validate_constitution(FIXTURES / "constitution_missing_exception_field.md")
    assert any(
        f.severity == "BLOCK"
        and "article 1" in f.message.lower()
        and "exception" in f.message.lower()
        for f in findings
    )


def test_per_article_field_check_catches_only_offender() -> None:
    """Article 1 complete; Article 2 missing both fields. We must flag Article 2
    even though Article 1 satisfied document-wide presence."""
    findings = validate.validate_constitution(FIXTURES / "constitution_partial_article_fields.md")
    rule_msgs = [f for f in findings if "rule" in f.message.lower()]
    exc_msgs = [f for f in findings if "exception" in f.message.lower()]
    assert all("article 2" in f.message.lower() for f in rule_msgs), rule_msgs
    assert all("article 2" in f.message.lower() for f in exc_msgs), exc_msgs
    assert rule_msgs and exc_msgs


def test_too_many_articles_blocks() -> None:
    """16 articles trips the hard cap (BLOCK at >=16 per M3 spec §5.3.1)."""
    findings = validate.validate_constitution(FIXTURES / "constitution_too_many_articles.md")
    assert any(f.severity == "BLOCK" and "16" in f.message for f in findings)


def test_missing_file_returns_block_finding(tmp_path: Path) -> None:
    findings = validate.validate_constitution(tmp_path / "absent.md")
    assert any(f.severity == "BLOCK" and "not found" in f.message.lower() for f in findings)
