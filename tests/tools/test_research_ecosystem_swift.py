"""Tests for the Swift ecosystem plugin."""

from tools.research.ecosystems import swift as swift_plugin

from .research_plugin_helpers import EMPTY_REPO_ROOT, fixture_path


def test_match_happy_returns_record() -> None:
    record = swift_plugin.plugin.match(fixture_path("swift", "happy"))
    assert record is not None
    assert record.name == "swift"
    assert record.manifest_paths == ("Package.swift",)


def test_declared_deps_happy_includes_named_and_url_packages() -> None:
    deps = swift_plugin.plugin.declared_deps(fixture_path("swift", "happy"))
    assert "alamofire" in deps
    # URL-form package: tail is "swift-nio.git" -> stripped + normalised.
    assert "swift_nio" in deps


def test_scan_imports_happy_finds_modules() -> None:
    imports = swift_plugin.plugin.scan_imports(fixture_path("swift", "happy"))
    assert "alamofire" in imports
    assert "foundation" in imports


def test_match_boundary_returns_record_empty_deps() -> None:
    record = swift_plugin.plugin.match(fixture_path("swift", "boundary"))
    assert record is not None
    assert record.declared_deps == ()


def test_match_failure_returns_record() -> None:
    record = swift_plugin.plugin.match(fixture_path("swift", "failure"))
    assert record is not None


def test_match_empty_repo_returns_none() -> None:
    assert swift_plugin.plugin.match(EMPTY_REPO_ROOT) is None


def test_standard_dirs() -> None:
    dirs = swift_plugin.plugin.standard_dirs()
    assert dirs["test"] == ("Tests/",)
    assert dirs["source"] == ("Sources/",)
