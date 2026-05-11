"""Tests for the Ruby ecosystem plugin."""

from tools.research.ecosystems import ruby as ruby_plugin

from .research_plugin_helpers import EMPTY_REPO_ROOT, fixture_path


def test_match_happy_returns_record() -> None:
    record = ruby_plugin.plugin.match(fixture_path("ruby", "happy"))
    assert record is not None
    assert record.name == "ruby"
    assert "Gemfile" in record.manifest_paths


def test_declared_deps_happy_includes_gems() -> None:
    deps = ruby_plugin.plugin.declared_deps(fixture_path("ruby", "happy"))
    assert "rails" in deps
    assert "puma" in deps
    assert "rspec" in deps


def test_scan_imports_happy_finds_requires() -> None:
    imports = ruby_plugin.plugin.scan_imports(fixture_path("ruby", "happy"))
    assert "rails" in imports
    assert "puma" in imports


def test_match_boundary_returns_record() -> None:
    record = ruby_plugin.plugin.match(fixture_path("ruby", "boundary"))
    assert record is not None
    assert record.declared_deps == ()


def test_match_failure_returns_record_empty_deps() -> None:
    record = ruby_plugin.plugin.match(fixture_path("ruby", "failure"))
    assert record is not None


def test_match_empty_repo_returns_none() -> None:
    assert ruby_plugin.plugin.match(EMPTY_REPO_ROOT) is None


def test_standard_dirs() -> None:
    dirs = ruby_plugin.plugin.standard_dirs()
    assert "spec/" in dirs["test"]
    assert "lib/" in dirs["source"]
