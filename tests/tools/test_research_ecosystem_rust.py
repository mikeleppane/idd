"""Tests for the Rust ecosystem plugin."""

from tools.research.ecosystems import rust as rust_plugin

from .research_plugin_helpers import EMPTY_REPO_ROOT, fixture_path


def test_match_happy_returns_record() -> None:
    record = rust_plugin.plugin.match(fixture_path("rust", "happy"))
    assert record is not None
    assert record.name == "rust"
    assert record.manifest_paths == ("Cargo.toml",)


def test_declared_deps_happy_includes_runtime_and_dev() -> None:
    deps = rust_plugin.plugin.declared_deps(fixture_path("rust", "happy"))
    assert "serde" in deps
    assert "tokio" in deps
    assert "proptest" in deps


def test_scan_imports_happy_finds_use_statements() -> None:
    imports = rust_plugin.plugin.scan_imports(fixture_path("rust", "happy"))
    assert "serde" in imports
    assert "tokio" in imports


def test_match_boundary_returns_record_empty_deps() -> None:
    record = rust_plugin.plugin.match(fixture_path("rust", "boundary"))
    assert record is not None
    assert record.declared_deps == ()


def test_match_failure_returns_record_empty_deps() -> None:
    record = rust_plugin.plugin.match(fixture_path("rust", "failure"))
    assert record is not None
    assert record.declared_deps == ()


def test_match_empty_repo_returns_none() -> None:
    assert rust_plugin.plugin.match(EMPTY_REPO_ROOT) is None


def test_standard_dirs() -> None:
    dirs = rust_plugin.plugin.standard_dirs()
    assert dirs["test"] == ("tests/",)
    assert dirs["source"] == ("src/",)
