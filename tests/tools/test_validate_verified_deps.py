"""Tests for tools.validate.validate_verified_deps (M3 §5.3.6 D-8 Verified Deps shape)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from tools import validate

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "_validate"


def test_verified_deps_pass_offline() -> None:
    findings = validate.validate_verified_deps(FIX / "verified_deps_pass.md")
    assert findings == []


def test_verified_deps_missing_columns_high() -> None:
    findings = validate.validate_verified_deps(FIX / "verified_deps_missing_columns.md")
    assert any(f.severity == "HIGH" and "missing required columns" in f.message for f in findings)


def test_verified_deps_unknown_ecosystem_high() -> None:
    findings = validate.validate_verified_deps(FIX / "verified_deps_unknown_ecosystem.md")
    assert any(
        f.severity == "HIGH" and "unknown ecosystem" in f.message and "bogusland" in f.message
        for f in findings
    )


def test_verified_deps_no_table_high() -> None:
    findings = validate.validate_verified_deps(FIX / "verified_deps_no_table.md")
    assert any(f.severity == "HIGH" and "no table" in f.message.lower() for f in findings)


def test_verified_deps_no_section_returns_empty() -> None:
    findings = validate.validate_verified_deps(FIX / "verified_deps_no_section.md")
    assert findings == []


def test_verified_deps_blank_notes_column_passes() -> None:
    """Empty interior cells must not shift registry column index."""
    findings = validate.validate_verified_deps(FIX / "verified_deps_blank_notes_column.md")
    # No HIGH findings — registry column reads `npm`, not `useState`.
    assert not any(f.severity == "HIGH" for f in findings)


def test_verified_deps_only_separator_high() -> None:
    findings = validate.validate_verified_deps(FIX / "verified_deps_only_separator.md")
    assert any(
        f.severity == "HIGH" and "no" in f.message.lower() and "row" in f.message.lower()
        for f in findings
    )


def test_verified_deps_missing_file_blocks(tmp_path: Path) -> None:
    findings = validate.validate_verified_deps(tmp_path / "does_not_exist.md")
    assert len(findings) == 1
    assert findings[0].severity == "BLOCK"


def test_verified_deps_check_registries_calls_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Opt-in registry probe shells out per row.

    Patches BOTH `shutil.which` (to claim CLIs are present on PATH) AND
    `subprocess.run` (to capture the invocation). Without the `shutil.which`
    patch the implementation's PATH guard short-circuits before any subprocess
    call on machines that lack `npm`/`pip`.
    """
    calls: list[str] = []

    def fake_run(cmd: list[str], **_kwargs: Any) -> Any:
        calls.append(cmd[0])

        class R:
            returncode = 0
            stdout = "[]"
            stderr = ""

        return R()

    monkeypatch.setattr("tools.validate.plan.shutil.which", lambda _cmd: "/usr/bin/fake")
    monkeypatch.setattr("tools.validate.plan.subprocess.run", fake_run)

    findings = validate.validate_verified_deps(
        FIX / "verified_deps_pass.md",
        check_registries=True,
    )
    assert "npm" in calls or "pip" in calls
    assert not any(f.severity == "HIGH" for f in findings)


def test_verified_deps_check_registries_cli_absent_warns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When `shutil.which` returns None, emit WARN, never call subprocess."""

    def fail_if_called(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("subprocess.run must not be called when CLI is absent")

    monkeypatch.setattr("tools.validate.plan.shutil.which", lambda _cmd: None)
    monkeypatch.setattr("tools.validate.plan.subprocess.run", fail_if_called)

    findings = validate.validate_verified_deps(
        FIX / "verified_deps_pass.md",
        check_registries=True,
    )
    assert any(f.severity == "WARN" and "PATH" in f.message for f in findings)


def test_verified_deps_default_does_not_shell_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default `check_registries=False` must not invoke subprocess.run."""

    def fail_if_called(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("subprocess.run must not be called when check_registries=False")

    monkeypatch.setattr("tools.validate.plan.subprocess.run", fail_if_called)
    findings = validate.validate_verified_deps(FIX / "verified_deps_pass.md")
    assert findings == []


def test_verified_deps_check_registries_timeout_warns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Subprocess timeout surfaces as WARN, not HIGH."""

    def slow_run(cmd: list[str], **_kwargs: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=5)

    monkeypatch.setattr("tools.validate.plan.shutil.which", lambda _cmd: "/usr/bin/fake")
    monkeypatch.setattr("tools.validate.plan.subprocess.run", slow_run)

    findings = validate.validate_verified_deps(
        FIX / "verified_deps_pass.md",
        check_registries=True,
    )
    assert any(f.severity == "WARN" and "timed out" in f.message for f in findings)


def test_verified_deps_check_registries_nonzero_high(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-zero subprocess exit emits HIGH 'package not found in <registry>'."""

    def fake_run(_cmd: list[str], **_kwargs: Any) -> Any:
        class R:
            returncode = 1
            stdout = ""
            stderr = "E404 not found"

        return R()

    monkeypatch.setattr("tools.validate.plan.shutil.which", lambda _cmd: "/usr/bin/fake")
    monkeypatch.setattr("tools.validate.plan.subprocess.run", fake_run)

    findings = validate.validate_verified_deps(
        FIX / "verified_deps_pass.md",
        check_registries=True,
    )
    assert any(f.severity == "HIGH" and "not found in npm" in f.message for f in findings)
