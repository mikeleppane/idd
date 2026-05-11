"""Tests for the PHP ecosystem plugin."""

from tools.research.ecosystems import php as php_plugin

from .research_plugin_helpers import EMPTY_REPO_ROOT, fixture_path


def test_match_happy_returns_record() -> None:
    record = php_plugin.plugin.match(fixture_path("php", "happy"))
    assert record is not None
    assert record.name == "php"
    assert record.manifest_paths == ("composer.json",)


def test_declared_deps_happy_includes_packages() -> None:
    deps = php_plugin.plugin.declared_deps(fixture_path("php", "happy"))
    # Composer keeps the vendor/package slash form; only hyphens normalise.
    assert "symfony/console" in deps
    assert "monolog/monolog" in deps
    assert "phpunit/phpunit" in deps
    # The "php" runtime requirement should be filtered out.
    assert "php" not in deps


def test_scan_imports_happy_finds_use_statements() -> None:
    imports = php_plugin.plugin.scan_imports(fixture_path("php", "happy"))
    assert "symfony\\component\\console\\application" in imports


def test_match_boundary_returns_record_empty_deps() -> None:
    record = php_plugin.plugin.match(fixture_path("php", "boundary"))
    assert record is not None
    assert record.declared_deps == ()


def test_match_failure_returns_record_empty_deps() -> None:
    record = php_plugin.plugin.match(fixture_path("php", "failure"))
    assert record is not None
    assert record.declared_deps == ()


def test_match_empty_repo_returns_none() -> None:
    assert php_plugin.plugin.match(EMPTY_REPO_ROOT) is None


def test_standard_dirs() -> None:
    dirs = php_plugin.plugin.standard_dirs()
    assert "tests/" in dirs["test"]
    assert "src/" in dirs["source"]
