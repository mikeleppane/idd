"""Tests for tools.validate.validate_anchors (M3 §5.3.6 D-8 anchors module-resolve)."""

from __future__ import annotations

from pathlib import Path

from tools import validate

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "_validate"
REPO = FIX / "anchors_repo"


def test_anchors_pass() -> None:
    findings = validate.validate_anchors(FIX / "spec_anchors_pass.md", repo_root=REPO)
    assert findings == []


def test_anchors_missing_path_high() -> None:
    findings = validate.validate_anchors(FIX / "spec_anchors_missing_path.md", repo_root=REPO)
    assert any(f.severity == "HIGH" and "path" in f.message.lower() for f in findings)


def test_anchors_missing_symbol_medium() -> None:
    findings = validate.validate_anchors(FIX / "spec_anchors_missing_symbol.md", repo_root=REPO)
    assert any(f.severity == "MEDIUM" and "symbol" in f.message.lower() for f in findings)


def test_anchors_no_section_returns_empty() -> None:
    findings = validate.validate_anchors(FIX / "spec_anchors_no_section.md", repo_root=REPO)
    assert findings == []


def test_anchors_absolute_path_blocks() -> None:
    findings = validate.validate_anchors(FIX / "spec_anchors_absolute_path.md", repo_root=REPO)
    assert any(
        f.severity == "BLOCK" and ("absolute" in f.message.lower() or "escape" in f.message.lower())
        for f in findings
    )


def test_anchors_traversal_blocks() -> None:
    findings = validate.validate_anchors(FIX / "spec_anchors_traversal.md", repo_root=REPO)
    assert any(f.severity == "BLOCK" for f in findings)


def test_anchors_missing_file_blocks(tmp_path: Path) -> None:
    findings = validate.validate_anchors(tmp_path / "does_not_exist.md", repo_root=REPO)
    assert any(f.severity == "BLOCK" for f in findings)
