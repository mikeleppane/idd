"""Tests for the Go ecosystem plugin."""

from tools.research.ecosystems import go as go_plugin

from .research_plugin_helpers import EMPTY_REPO_ROOT, fixture_path


def test_match_happy_returns_record() -> None:
    record = go_plugin.plugin.match(fixture_path("go", "happy"))
    assert record is not None
    assert record.name == "go"
    assert record.manifest_paths == ("go.mod",)


def test_declared_deps_happy_includes_modules() -> None:
    deps = go_plugin.plugin.declared_deps(fixture_path("go", "happy"))
    # Module paths are normalised — hyphens become underscores.
    assert "github.com/gin_gonic/gin" in deps
    assert "github.com/stretchr/testify" in deps


def test_scan_imports_happy_finds_quoted_imports() -> None:
    imports = go_plugin.plugin.scan_imports(fixture_path("go", "happy"))
    assert "github.com/gin-gonic/gin" in imports


def test_match_boundary_returns_record_empty_deps() -> None:
    record = go_plugin.plugin.match(fixture_path("go", "boundary"))
    assert record is not None
    assert record.declared_deps == ()


def test_match_failure_returns_record() -> None:
    # go.mod failure fixture has unterminated require block; declared_deps is best-effort.
    record = go_plugin.plugin.match(fixture_path("go", "failure"))
    assert record is not None


def test_match_empty_repo_returns_none() -> None:
    assert go_plugin.plugin.match(EMPTY_REPO_ROOT) is None


def test_standard_dirs() -> None:
    dirs = go_plugin.plugin.standard_dirs()
    assert "" in dirs["test"]
    assert "cmd/" in dirs["source"]
