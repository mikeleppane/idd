"""Smoke: ``project_scan.scan`` returns a stable polyglot summary.

The polyglot fixture stages a single repo with both ``package.json`` and
``pyproject.toml`` so the detector emits two ecosystem records. This
test pins the public-contract surface of :func:`tools.research.project_scan.scan`:
ecosystems sort alphabetically, declared deps populate per ecosystem,
the unioned library list matches a deduped view of all declared deps,
and the layout snapshot is non-empty.
"""

from __future__ import annotations

from pathlib import Path

from tools.research import library_extract, project_scan

_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "_research" / "polyglot_node_python"


def test_polyglot_scan_surfaces_both_ecosystems() -> None:
    result = project_scan.scan(_FIXTURE)

    assert result.ecosystems == ("node", "python")

    assert set(result.declared_deps) == {"node", "python"}
    assert result.declared_deps["node"], "node deps should not be empty"
    assert result.declared_deps["python"], "python deps should not be empty"

    expected_union = tuple(
        library_extract.dedup(
            list(result.declared_deps["node"]) + list(result.declared_deps["python"]),
        ),
    )
    assert sorted(result.unioned_libraries) == sorted(expected_union)

    assert result.layout, "layout should surface at least the src/ directory"
