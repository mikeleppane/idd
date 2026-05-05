"""Tests for validate_delta structural checks."""

from __future__ import annotations

from pathlib import Path

from tools import validate

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "_validate"


def test_pass_delta_returns_no_findings() -> None:
    findings = validate.validate_delta(FIXTURES / "delta_pass.md")
    assert findings == []


def test_no_frontmatter_blocks() -> None:
    findings = validate.validate_delta(FIXTURES / "delta_no_frontmatter.md")
    assert any(f.severity == "BLOCK" and "frontmatter" in f.message.lower() for f in findings)


def test_bad_status_enum_blocks() -> None:
    findings = validate.validate_delta(FIXTURES / "delta_bad_status_enum.md")
    assert any(f.severity == "BLOCK" and "status" in f.message.lower() for f in findings)


def test_missing_affects_blocks() -> None:
    findings = validate.validate_delta(FIXTURES / "delta_missing_affects.md")
    assert any(f.severity == "BLOCK" and "affects" in f.message.lower() for f in findings)


def test_missing_delta_section_blocks() -> None:
    findings = validate.validate_delta(FIXTURES / "delta_missing_delta_section.md")
    assert any(f.severity == "BLOCK" and "delta" in f.message.lower() for f in findings)


def test_no_op_markers_blocks() -> None:
    findings = validate.validate_delta(FIXTURES / "delta_no_op_markers.md")
    assert any(
        f.severity == "BLOCK"
        and ("ADD" in f.message or "REMOVE" in f.message or "MODIFY" in f.message)
        for f in findings
    )


def test_missing_file_returns_block_finding(tmp_path: Path) -> None:
    findings = validate.validate_delta(tmp_path / "absent.md")
    assert any(f.severity == "BLOCK" and "not found" in f.message.lower() for f in findings)


def test_invalid_yaml_frontmatter_returns_block_not_traceback() -> None:
    """Malformed YAML must surface as a structured BLOCK finding."""
    findings = validate.validate_delta(FIXTURES / "delta_invalid_yaml.md")
    assert any(f.severity == "BLOCK" and "invalid yaml" in f.message.lower() for f in findings), (
        findings
    )
