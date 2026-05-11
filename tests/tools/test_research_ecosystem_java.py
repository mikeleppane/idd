"""Tests for the Java ecosystem plugin."""

from tools.research.ecosystems import java as java_plugin

from .research_plugin_helpers import EMPTY_REPO_ROOT, fixture_path


def test_match_happy_returns_record() -> None:
    record = java_plugin.plugin.match(fixture_path("java", "happy"))
    assert record is not None
    assert record.name == "java"
    assert "pom.xml" in record.manifest_paths


def test_declared_deps_happy_includes_artifacts() -> None:
    deps = java_plugin.plugin.declared_deps(fixture_path("java", "happy"))
    assert "spring_core" in deps
    assert "junit_jupiter" in deps


def test_scan_imports_happy_finds_imports() -> None:
    imports = java_plugin.plugin.scan_imports(fixture_path("java", "happy"))
    assert "org.springframework.context.applicationcontext" in imports


def test_match_boundary_returns_record_empty_deps() -> None:
    record = java_plugin.plugin.match(fixture_path("java", "boundary"))
    assert record is not None
    assert record.declared_deps == ()


def test_match_failure_returns_record_empty_deps() -> None:
    record = java_plugin.plugin.match(fixture_path("java", "failure"))
    assert record is not None
    assert record.declared_deps == ()


def test_match_empty_repo_returns_none() -> None:
    assert java_plugin.plugin.match(EMPTY_REPO_ROOT) is None


def test_standard_dirs() -> None:
    dirs = java_plugin.plugin.standard_dirs()
    assert "src/test/java/" in dirs["test"]
    assert "src/main/java/" in dirs["source"]
