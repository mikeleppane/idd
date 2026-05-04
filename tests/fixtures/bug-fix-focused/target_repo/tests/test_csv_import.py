"""Tests for the dummy CSV import path."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "import"))

from csv import import_row  # type: ignore[import-not-found]


def test_trims_leading_and_trailing() -> None:
    assert import_row(["  alice@example.com  "]) == ["alice@example.com"]


def test_preserves_internal_whitespace() -> None:
    assert import_row(["Alice  Smith"]) == ["Alice  Smith"]
