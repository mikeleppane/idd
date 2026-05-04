"""Tiny CSV import path used by the bug-fix-focused fixture."""
from __future__ import annotations


def import_row(row: list[str]) -> list[str]:
    """Trim leading/trailing whitespace from each cell; preserve internal whitespace."""
    return [cell.strip() for cell in row]
