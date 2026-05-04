"""Shared pytest fixtures for IDD tooling tests."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def repo_root() -> Path:
    """Absolute path to the IDD repo root."""
    return REPO_ROOT


@pytest.fixture()
def schemas_dir(repo_root: Path) -> Path:
    return repo_root / "schemas"


@pytest.fixture()
def templates_dir(repo_root: Path) -> Path:
    return repo_root / "templates"
