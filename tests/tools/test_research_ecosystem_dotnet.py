"""Tests for the .NET ecosystem plugin."""

from tools.research.ecosystems import dotnet as dotnet_plugin

from .research_plugin_helpers import EMPTY_REPO_ROOT, fixture_path


def test_match_happy_returns_record() -> None:
    record = dotnet_plugin.plugin.match(fixture_path("dotnet", "happy"))
    assert record is not None
    assert record.name == "dotnet"
    assert any(name.endswith(".csproj") for name in record.manifest_paths)


def test_declared_deps_happy_includes_packages() -> None:
    deps = dotnet_plugin.plugin.declared_deps(fixture_path("dotnet", "happy"))
    assert "newtonsoft.json" in deps
    assert "serilog" in deps


def test_scan_imports_happy_finds_using_statements() -> None:
    imports = dotnet_plugin.plugin.scan_imports(fixture_path("dotnet", "happy"))
    assert "system" in imports
    assert "newtonsoft.json" in imports


def test_match_boundary_returns_record_empty_deps() -> None:
    record = dotnet_plugin.plugin.match(fixture_path("dotnet", "boundary"))
    assert record is not None
    assert record.declared_deps == ()


def test_match_failure_returns_record_empty_deps() -> None:
    record = dotnet_plugin.plugin.match(fixture_path("dotnet", "failure"))
    assert record is not None
    assert record.declared_deps == ()


def test_match_empty_repo_returns_none() -> None:
    assert dotnet_plugin.plugin.match(EMPTY_REPO_ROOT) is None


def test_standard_dirs() -> None:
    dirs = dotnet_plugin.plugin.standard_dirs()
    assert "test/" in dirs["test"]
    assert "src/" in dirs["source"]
