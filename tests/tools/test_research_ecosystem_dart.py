"""Tests for the Dart ecosystem plugin."""

from tools.research.ecosystems import dart as dart_plugin

from .research_plugin_helpers import EMPTY_REPO_ROOT, fixture_path


def test_match_happy_returns_record() -> None:
    record = dart_plugin.plugin.match(fixture_path("dart", "happy"))
    assert record is not None
    assert record.name == "dart"
    assert record.manifest_paths == ("pubspec.yaml",)


def test_declared_deps_happy_includes_packages() -> None:
    deps = dart_plugin.plugin.declared_deps(fixture_path("dart", "happy"))
    assert "http" in deps
    assert "provider" in deps
    assert "test" in deps


def test_scan_imports_happy_finds_package_imports() -> None:
    imports = dart_plugin.plugin.scan_imports(fixture_path("dart", "happy"))
    assert "http" in imports
    assert "provider" in imports


def test_match_boundary_returns_record_empty_deps() -> None:
    record = dart_plugin.plugin.match(fixture_path("dart", "boundary"))
    assert record is not None
    assert record.declared_deps == ()


def test_match_failure_returns_record_empty_deps() -> None:
    record = dart_plugin.plugin.match(fixture_path("dart", "failure"))
    assert record is not None
    assert record.declared_deps == ()


def test_match_empty_repo_returns_none() -> None:
    assert dart_plugin.plugin.match(EMPTY_REPO_ROOT) is None


def test_standard_dirs() -> None:
    dirs = dart_plugin.plugin.standard_dirs()
    assert dirs["test"] == ("test/",)
    assert dirs["source"] == ("lib/",)
