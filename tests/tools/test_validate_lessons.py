"""Tests for validate_lessons (.forge/intel/lessons.md parser wrapper)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import validate as validate_pkg
from tools.validate import validate_lessons

_WELL_FORMED_BODY = """---
version: 0.1.0
created: "2026-05-11"
---

# FORGE Lessons

## L001 — Example lesson
**Captured:** 2026-05-11 from feature m0-example
**Resolved by:** manual
**Trap:** Example trap text describing what went wrong with sufficient detail to be searchable but tight enough to fit a dispatch budget.
**Avoidance:** Example avoidance describing what future subagents should do instead.
**Tags:** dispatch, validation
**Severity:** LOW
**Status:** retired
"""

_MALFORMED_MISSING_TRAP = """---
version: 0.1.0
created: "2026-05-11"
---

# FORGE Lessons

## L001 — Example lesson
**Captured:** 2026-05-11 from feature m0-example
**Resolved by:** manual
**Avoidance:** Example avoidance describing what future subagents should do instead.
**Tags:** dispatch, validation
**Severity:** LOW
**Status:** retired
"""

_MALFORMED_BAD_TAG = """---
version: 0.1.0
created: "2026-05-11"
---

# FORGE Lessons

## L001 — Example lesson
**Captured:** 2026-05-11 from feature m0-example
**Resolved by:** manual
**Trap:** Example trap text describing what went wrong.
**Avoidance:** Example avoidance describing what future subagents should do instead.
**Tags:** imports, made-up
**Severity:** LOW
**Status:** retired
"""


def _write_lessons(repo_root: Path, body: str) -> Path:
    intel = repo_root / ".forge" / "intel"
    intel.mkdir(parents=True, exist_ok=True)
    path = intel / "lessons.md"
    path.write_text(body, encoding="utf-8")
    return path


# --- Direct API -------------------------------------------------------------


def test_missing_lessons_file_returns_empty_findings(tmp_path: Path) -> None:
    assert validate_lessons(tmp_path) == []


def test_absent_intel_directory_returns_empty_findings(tmp_path: Path) -> None:
    """Repo with no .forge/intel/ at all is treated as a clean pass — a fresh
    repository has no lessons yet, and the absence of the directory must not
    surface as a finding."""
    assert validate_lessons(tmp_path) == []


def test_well_formed_lessons_file_returns_empty_findings(tmp_path: Path) -> None:
    _write_lessons(tmp_path, _WELL_FORMED_BODY)
    assert validate_lessons(tmp_path) == []


def test_malformed_missing_trap_field_blocks(tmp_path: Path) -> None:
    _write_lessons(tmp_path, _MALFORMED_MISSING_TRAP)
    findings = validate_lessons(tmp_path)
    assert len(findings) == 1
    finding = findings[0]
    assert finding.severity == "BLOCK"
    assert finding.target == "lessons"
    assert "trap" in finding.message.lower()


def test_malformed_bad_tag_blocks(tmp_path: Path) -> None:
    _write_lessons(tmp_path, _MALFORMED_BAD_TAG)
    findings = validate_lessons(tmp_path)
    assert len(findings) == 1
    finding = findings[0]
    assert finding.severity == "BLOCK"
    assert finding.target == "lessons"
    assert "made-up" in finding.message


def test_block_finding_carries_full_lessons_path(tmp_path: Path) -> None:
    expected_path = _write_lessons(tmp_path, _MALFORMED_MISSING_TRAP)
    findings = validate_lessons(tmp_path)
    assert len(findings) == 1
    assert findings[0].file == expected_path


def test_block_finding_target_is_lessons(tmp_path: Path) -> None:
    _write_lessons(tmp_path, _MALFORMED_MISSING_TRAP)
    findings = validate_lessons(tmp_path)
    assert findings[0].target == "lessons"


def test_malformed_header_blocks(tmp_path: Path) -> None:
    """A header that does not match the L<NNN> shape raises LessonError;
    the validator translates that to a BLOCK finding rather than crashing."""
    bad_header = """---
version: 0.1.0
created: "2026-05-11"
---

# FORGE Lessons

## Lxx — Bad header
**Captured:** 2026-05-11 from feature m0-example
**Resolved by:** manual
**Trap:** Example trap text.
**Avoidance:** Example avoidance text.
**Tags:** dispatch
**Severity:** LOW
**Status:** active
"""
    _write_lessons(tmp_path, bad_header)
    findings = validate_lessons(tmp_path)
    assert len(findings) == 1
    assert findings[0].severity == "BLOCK"


# --- CLI integration --------------------------------------------------------


def test_cli_target_lessons_zero_when_file_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = validate_pkg.main(["--target", "lessons", "--repo-root", str(tmp_path)])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == 0
    assert payload == {"target": "lessons", "findings": []}


def test_cli_target_lessons_blocks_on_malformed_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_lessons(tmp_path, _MALFORMED_MISSING_TRAP)
    rc = validate_pkg.main(["--target", "lessons", "--repo-root", str(tmp_path)])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == 1
    assert payload["target"] == "lessons"
    assert any(f["severity"] == "BLOCK" for f in payload["findings"])


def test_cli_target_lessons_zero_on_well_formed_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_lessons(tmp_path, _WELL_FORMED_BODY)
    rc = validate_pkg.main(["--target", "lessons", "--repo-root", str(tmp_path)])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == 0, payload
    assert payload["findings"] == []
