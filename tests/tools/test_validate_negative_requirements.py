"""Tests for validate_negative_requirements placement check."""

from __future__ import annotations

from pathlib import Path

from tools import validate

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "_validate"


def test_pass_returns_no_findings() -> None:
    findings = validate.validate_negative_requirements(FIXTURES / "spec_nr_pass.md")
    assert findings == []


def test_nr_outside_section_blocks() -> None:
    findings = validate.validate_negative_requirements(FIXTURES / "spec_nr_outside_section.md")
    assert any(
        f.severity == "BLOCK" and ("SHALL NOT" in f.message or "MUST NOT" in f.message)
        for f in findings
    )


def test_nr_missing_section_blocks() -> None:
    findings = validate.validate_negative_requirements(FIXTURES / "spec_nr_missing_section.md")
    assert any(f.severity == "BLOCK" and "Negative Requirements" in f.message for f in findings)


def test_nr_phrase_inside_code_fence_does_not_block() -> None:
    """Code-fenced examples that contain `MUST NOT` are illustrative, not
    normative. They MUST NOT trigger placement findings."""
    findings = validate.validate_negative_requirements(FIXTURES / "spec_nr_in_code_fence.md")
    assert findings == []


def test_missing_file_returns_block_finding(tmp_path: Path) -> None:
    findings = validate.validate_negative_requirements(tmp_path / "absent.md")
    assert any(f.severity == "BLOCK" and "not found" in f.message.lower() for f in findings)
