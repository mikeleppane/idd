"""Shared helpers for per-plugin ecosystem tests.

The eleven ecosystem plugin test modules share the same happy/boundary/
failure fixture layout under ``tests/fixtures/_research/ecosystems/``.
Centralising the fixture-path resolver keeps each test file focused on
plugin-specific assertions instead of re-deriving paths.
"""

from pathlib import Path

FIXTURES_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "_research" / "ecosystems"
EMPTY_REPO_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "_research" / "empty_repo"


def fixture_path(plugin_name: str, kind: str) -> Path:
    """Return the absolute path to ``<plugin>/<kind>`` fixture (happy/boundary/failure)."""
    return FIXTURES_ROOT / plugin_name / kind
