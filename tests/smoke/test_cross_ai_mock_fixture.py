"""Smoke tests for the cross-AI mock CLI fixture.

The fixture under ``tests/fixtures/_cross_ai/`` ships a mock dispatcher
plus three relative symlinks (``codex``, ``claude``, ``gemini``) so that
prepending the directory to ``$PATH`` makes :func:`shutil.which` resolve
each reviewer name to the same script. Behavior is selected at run time
through the ``MOCK_CLI_RESPONSE`` environment variable.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FIXTURE_DIR = _REPO_ROOT / "tests" / "fixtures" / "_cross_ai"
_DISPATCH = _FIXTURE_DIR / "mock_dispatch.sh"
_REVIEWER_NAMES: tuple[str, ...] = ("codex", "claude", "gemini")


def test_mock_dispatch_exists_and_is_executable() -> None:
    assert _DISPATCH.is_file(), f"missing dispatcher: {_DISPATCH}"
    assert os.access(_DISPATCH, os.X_OK), f"not executable: {_DISPATCH}"


@pytest.mark.parametrize("reviewer", _REVIEWER_NAMES)
def test_reviewer_symlinks_resolve_to_dispatch(reviewer: str) -> None:
    link = _FIXTURE_DIR / reviewer
    assert link.is_symlink(), f"expected symlink, got: {link}"
    assert os.path.realpath(link) == os.path.realpath(_DISPATCH)


@pytest.mark.parametrize("reviewer", _REVIEWER_NAMES)
def test_path_lookup_finds_reviewer_via_fixture(
    reviewer: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    existing_path = os.environ.get("PATH", "")
    monkeypatch.setenv("PATH", f"{_FIXTURE_DIR}{os.pathsep}{existing_path}")
    resolved = shutil.which(reviewer)
    assert resolved is not None, f"shutil.which could not find {reviewer!r}"
    assert os.path.realpath(resolved) == os.path.realpath(_DISPATCH)


def test_clean_response_invocation_emits_no_findings_marker() -> None:
    result = subprocess.run(
        [str(_DISPATCH)],
        env={**os.environ, "MOCK_CLI_RESPONSE": "clean"},
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, f"stderr: {result.stderr!r}"
    assert "No findings." in result.stdout
