"""Tests for validate_frontmatter wrapper across artifact types."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools import validate


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def test_valid_spec_returns_no_findings(tmp_path: Path) -> None:
    spec = tmp_path / "SPEC.md"
    _write(
        spec,
        "---\nid: 2026-05-04-demo\nstatus: draft\ntier: focused\n"
        "created: 2026-05-04\ncapability: demo\n---\n# Intent\n",
    )

    findings = validate.validate_frontmatter(spec, kind="spec")

    assert findings == []


def test_invalid_spec_blocks(tmp_path: Path) -> None:
    spec = tmp_path / "SPEC.md"
    _write(
        spec,
        "---\nid: BAD ID\nstatus: draft\ntier: focused\n"
        "created: 2026-05-04\ncapability: demo\n---\n# Intent\n",
    )

    findings = validate.validate_frontmatter(spec, kind="spec")

    assert any(f.severity == "BLOCK" and "id" in f.message for f in findings)


def test_unknown_kind_raises_validation_error(tmp_path: Path) -> None:
    spec = tmp_path / "x.md"
    _write(spec, "---\n---\n")

    with pytest.raises(validate.ValidationError, match="unknown kind"):
        validate.validate_frontmatter(spec, kind="bogus")


def test_missing_file_returns_block_finding(tmp_path: Path) -> None:
    findings = validate.validate_frontmatter(tmp_path / "absent.md", kind="spec")
    assert any(f.severity == "BLOCK" and "not found" in f.message.lower() for f in findings)
