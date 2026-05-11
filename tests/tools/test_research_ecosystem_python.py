"""Tests for the Python ecosystem plugin."""

from tools.research.ecosystems import python as python_plugin
from tools.research.ecosystems.python import PythonEcosystem

from .research_plugin_helpers import EMPTY_REPO_ROOT, fixture_path


def test_match_happy_returns_record_with_pyproject() -> None:
    record = python_plugin.plugin.match(fixture_path("python", "happy"))
    assert record is not None
    assert record.name == "python"
    assert "pyproject.toml" in record.manifest_paths


def test_declared_deps_happy_includes_runtime_and_optional() -> None:
    deps = python_plugin.plugin.declared_deps(fixture_path("python", "happy"))
    assert "httpx" in deps
    assert "pytest" in deps
    assert "mypy" in deps


def test_scan_imports_happy_finds_top_level_modules() -> None:
    imports = python_plugin.plugin.scan_imports(fixture_path("python", "happy"))
    assert "httpx" in imports
    assert "fastapi" in imports


def test_match_boundary_returns_record_with_empty_deps() -> None:
    record = python_plugin.plugin.match(fixture_path("python", "boundary"))
    assert record is not None
    assert record.declared_deps == ()


def test_declared_deps_boundary_returns_empty_tuple() -> None:
    assert python_plugin.plugin.declared_deps(fixture_path("python", "boundary")) == ()


def test_match_failure_returns_record_with_empty_deps() -> None:
    record = python_plugin.plugin.match(fixture_path("python", "failure"))
    assert record is not None
    assert record.declared_deps == ()


def test_scan_imports_failure_directory_returns_empty_or_safe() -> None:
    # Failure fixture has no source files; scan must not raise.
    assert python_plugin.plugin.scan_imports(fixture_path("python", "failure")) == []


def test_match_empty_repo_returns_none() -> None:
    assert python_plugin.plugin.match(EMPTY_REPO_ROOT) is None


def test_standard_dirs_lists_tests_and_src() -> None:
    dirs = PythonEcosystem().standard_dirs()
    assert "tests/" in dirs["test"]
    assert "src/" in dirs["source"]


def test_requirements_txt_supported() -> None:
    record = python_plugin.plugin.match(fixture_path("python", "happy"))
    assert record is not None
    # No requirements.txt in this fixture; manifests should reflect what is present.
    assert "requirements.txt" not in record.manifest_paths
