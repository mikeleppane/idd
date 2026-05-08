"""Tests for tools.validate.Finding dataclass and module skeleton."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools import validate


def test_finding_is_frozen() -> None:
    finding = validate.Finding(
        severity="BLOCK",
        target="constitution",
        file=Path(".forge/CONSTITUTION.md"),
        message="missing frontmatter",
    )
    with pytest.raises(AttributeError):
        finding.severity = "WARN"  # type: ignore[misc]


def test_finding_fields_round_trip() -> None:
    finding = validate.Finding(
        severity="WARN",
        target="constitution",
        file=Path(".forge/CONSTITUTION.md"),
        message="article count near cap",
    )
    assert finding.severity == "WARN"
    assert finding.target == "constitution"
    assert finding.file == Path(".forge/CONSTITUTION.md")
    assert finding.message == "article count near cap"


def test_validate_error_is_runtime_error() -> None:
    err = validate.ValidationError("boom")
    assert isinstance(err, RuntimeError)
