"""Polyglot detection test for the ecosystem registry walker."""

from pathlib import Path

from tools.research.ecosystem import detect

POLYGLOT_FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "_research" / "polyglot_node_python"
)


def test_polyglot_repo_returns_node_and_python_records_alphabetically() -> None:
    records = detect(POLYGLOT_FIXTURE)
    # Both share priority 10; alphabetical tiebreak gives node before python.
    # Generic is registered in PLUGINS and always matches; it sorts last by
    # priority 99 — strip it for the "real ecosystem" assertion.
    concrete_names = [r.name for r in records if r.name != "generic"]
    assert concrete_names == ["node", "python"]


def test_polyglot_records_carry_their_manifests() -> None:
    records = detect(POLYGLOT_FIXTURE)
    by_name = {r.name: r for r in records}
    assert "package.json" in by_name["node"].manifest_paths
    assert "pyproject.toml" in by_name["python"].manifest_paths


def test_polyglot_declared_deps_populated() -> None:
    records = detect(POLYGLOT_FIXTURE)
    by_name = {r.name: r for r in records}
    assert "react" in by_name["node"].declared_deps
    assert "fastapi" in by_name["python"].declared_deps
