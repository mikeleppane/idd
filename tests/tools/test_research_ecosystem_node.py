"""Tests for the Node ecosystem plugin."""

from tools.research.ecosystems import node as node_plugin

from .research_plugin_helpers import EMPTY_REPO_ROOT, fixture_path


def test_match_happy_returns_record() -> None:
    record = node_plugin.plugin.match(fixture_path("node", "happy"))
    assert record is not None
    assert record.name == "node"
    assert "package.json" in record.manifest_paths


def test_declared_deps_happy_includes_runtime_and_dev() -> None:
    deps = node_plugin.plugin.declared_deps(fixture_path("node", "happy"))
    assert "react" in deps
    assert "lodash" in deps
    assert "jest" in deps


def test_scan_imports_happy_finds_packages() -> None:
    imports = node_plugin.plugin.scan_imports(fixture_path("node", "happy"))
    assert "express" in imports
    # Relative imports (./foo) should be filtered out.
    assert not any(name.startswith(".") for name in imports)


def test_match_boundary_returns_record() -> None:
    record = node_plugin.plugin.match(fixture_path("node", "boundary"))
    assert record is not None
    assert record.declared_deps == ()


def test_match_failure_returns_record_with_empty_deps() -> None:
    record = node_plugin.plugin.match(fixture_path("node", "failure"))
    assert record is not None
    assert record.declared_deps == ()


def test_match_empty_repo_returns_none() -> None:
    assert node_plugin.plugin.match(EMPTY_REPO_ROOT) is None


def test_standard_dirs_lists_test_and_src() -> None:
    dirs = node_plugin.plugin.standard_dirs()
    assert "test/" in dirs["test"]
    assert "src/" in dirs["source"]
