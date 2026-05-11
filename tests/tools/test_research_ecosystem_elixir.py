"""Tests for the Elixir ecosystem plugin."""

from tools.research.ecosystems import elixir as elixir_plugin

from .research_plugin_helpers import EMPTY_REPO_ROOT, fixture_path


def test_match_happy_returns_record() -> None:
    record = elixir_plugin.plugin.match(fixture_path("elixir", "happy"))
    assert record is not None
    assert record.name == "elixir"
    assert record.manifest_paths == ("mix.exs",)


def test_declared_deps_happy_includes_hex_packages() -> None:
    deps = elixir_plugin.plugin.declared_deps(fixture_path("elixir", "happy"))
    assert "phoenix" in deps
    assert "ecto" in deps


def test_scan_imports_happy_finds_aliases() -> None:
    imports = elixir_plugin.plugin.scan_imports(fixture_path("elixir", "happy"))
    assert "phoenix.endpoint" in imports
    assert "ecto.schema" in imports


def test_match_boundary_returns_record_empty_deps() -> None:
    record = elixir_plugin.plugin.match(fixture_path("elixir", "boundary"))
    assert record is not None
    assert record.declared_deps == ()


def test_match_failure_returns_record() -> None:
    record = elixir_plugin.plugin.match(fixture_path("elixir", "failure"))
    assert record is not None


def test_match_empty_repo_returns_none() -> None:
    assert elixir_plugin.plugin.match(EMPTY_REPO_ROOT) is None


def test_standard_dirs() -> None:
    dirs = elixir_plugin.plugin.standard_dirs()
    assert dirs["test"] == ("test/",)
    assert dirs["source"] == ("lib/",)
