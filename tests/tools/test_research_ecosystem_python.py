"""Tests for the Python ecosystem plugin."""

from pathlib import Path

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


def test_manifest_paths_lists_canonical_filenames() -> None:
    paths = python_plugin.plugin.manifest_paths()
    assert paths == ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt")


def test_declared_deps_requirements_txt_only(tmp_path: Path) -> None:
    # No pyproject.toml — exercise the early-return branch in
    # _collect_pyproject and the requirements-file collector.
    (tmp_path / "requirements.txt").write_text(
        """
        # comment line — must be skipped
        -r other.txt

        httpx>=0.25
        Pydantic==2.5.0
        """,
        encoding="utf-8",
    )
    deps = python_plugin.plugin.declared_deps(tmp_path)
    assert "httpx" in deps
    assert "pydantic" in deps


def test_declared_deps_extra_requirements_glob(tmp_path: Path) -> None:
    (tmp_path / "requirements-dev.txt").write_text("ruff\nmypy\n", encoding="utf-8")
    record = python_plugin.plugin.match(tmp_path)
    assert record is not None
    assert "requirements-dev.txt" in record.manifest_paths
    deps = python_plugin.plugin.declared_deps(tmp_path)
    assert "ruff" in deps
    assert "mypy" in deps


def test_declared_deps_pyproject_with_non_dict_project_skipped(tmp_path: Path) -> None:
    # ``project`` is a non-dict value — _collect_pyproject hits the
    # early return at the isinstance check.
    (tmp_path / "pyproject.toml").write_text(
        'project = "not-a-table"\n',
        encoding="utf-8",
    )
    assert python_plugin.plugin.declared_deps(tmp_path) == ()


def test_declared_deps_pyproject_with_non_string_dependency_ignored(tmp_path: Path) -> None:
    # Non-string entry in dependencies list is silently skipped.
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="0.0.1"\ndependencies = ["httpx", 42]\n',
        encoding="utf-8",
    )
    deps = python_plugin.plugin.declared_deps(tmp_path)
    assert deps == ("httpx",)


def test_declared_deps_pyproject_optional_non_list_or_non_string_ignored(
    tmp_path: Path,
) -> None:
    # ``optional-dependencies`` group must be a list of strings; non-list
    # group values and non-string entries are silently skipped.
    (tmp_path / "pyproject.toml").write_text(
        """
[project]
name = "x"
version = "0.0.1"

[project.optional-dependencies]
dev = ["ruff", 99]
ignored = "not-a-list"
""",
        encoding="utf-8",
    )
    deps = python_plugin.plugin.declared_deps(tmp_path)
    assert "ruff" in deps


def test_declared_deps_requirement_with_unparseable_line_skipped(tmp_path: Path) -> None:
    # Lines that don't match the requirement-name regex are silently
    # skipped via the early-return in _add_requirement.
    (tmp_path / "requirements.txt").write_text("===\nhttpx\n", encoding="utf-8")
    deps = python_plugin.plugin.declared_deps(tmp_path)
    assert deps == ("httpx",)
