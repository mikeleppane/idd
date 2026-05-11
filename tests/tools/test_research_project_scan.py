"""Tests for ``tools.research.project_scan`` assembly layer.

The scan ties the ecosystem detector to library_extract and produces a
single ``ScanResult`` consumable by the research subagent. Coverage:

* single-ecosystem (python happy fixture) — declared_deps/top_modules/
  unioned_libraries derive from the plugin output.
* polyglot (node + python) — multi-ecosystem result is alphabetically
  ordered and ``unioned_libraries`` is the deduped union.
* pinned ecosystems honored — passing through to ``ecosystem.detect``.
* generic-fallback (empty repo) — generic is filtered out of the
  ``ecosystems`` tuple and library data is empty.
* plugin parse failure does not propagate — ``declared_deps[name]``
  becomes empty tuple.
* layout exclusions — hidden / vendor / build dirs are filtered from
  the layout summary.
"""

import dataclasses
from pathlib import Path
from unittest.mock import patch

from tools.research.project_scan import ScanResult, scan

FIXTURES_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "_research"
PYTHON_HAPPY = FIXTURES_ROOT / "ecosystems" / "python" / "happy"
PYTHON_FAILURE = FIXTURES_ROOT / "ecosystems" / "python" / "failure"
POLYGLOT = FIXTURES_ROOT / "polyglot_node_python"
EMPTY_REPO = FIXTURES_ROOT / "empty_repo"


def test_scan_returns_frozen_result() -> None:
    result = scan(PYTHON_HAPPY)
    assert isinstance(result, ScanResult)
    # Frozen dataclass: assignment must raise.
    try:
        result.ecosystems = ("changed",)  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        return
    raise AssertionError("ScanResult should be frozen")


def test_single_python_ecosystem_populates_deps_and_layout() -> None:
    result = scan(PYTHON_HAPPY)
    assert result.ecosystems == ("python",)
    # Happy fixture declares httpx, pytest, mypy (optional).
    assert result.declared_deps["python"] == ("httpx", "pytest", "mypy")
    assert result.top_modules["python"] == ("httpx", "pytest", "mypy")
    assert result.unioned_libraries == ("httpx", "pytest", "mypy")
    assert "src" in result.layout


def test_single_python_entrypoint_best_effort() -> None:
    # Happy fixture has no __main__.py / setup.py / src/<pkg>/__main__.py.
    # Best-effort fallback is the empty string.
    result = scan(PYTHON_HAPPY)
    assert "python" in result.entrypoints
    assert isinstance(result.entrypoints["python"], str)


def test_polyglot_returns_both_ecosystems_alphabetically() -> None:
    result = scan(POLYGLOT)
    assert result.ecosystems == ("node", "python")
    assert "node" in result.declared_deps
    assert "python" in result.declared_deps
    assert "react" in result.declared_deps["node"]
    assert "fastapi" in result.declared_deps["python"]


def test_polyglot_unioned_libraries_dedups_across_ecosystems() -> None:
    result = scan(POLYGLOT)
    # Both ecosystems' deps appear in the union (already normalized).
    assert "react" in result.unioned_libraries
    assert "fastapi" in result.unioned_libraries
    # Order is preserved per dedup contract; no duplicates.
    assert len(result.unioned_libraries) == len(set(result.unioned_libraries))


def test_pinned_ecosystems_filters_to_subset() -> None:
    result = scan(POLYGLOT, pinned_ecosystems=["python"])
    assert result.ecosystems == ("python",)
    assert "node" not in result.declared_deps
    assert "fastapi" in result.declared_deps["python"]


def test_generic_fallback_empty_repo_returns_empty_ecosystem_data() -> None:
    result = scan(EMPTY_REPO)
    # Generic is filtered out of the public ecosystems tuple.
    assert result.ecosystems == ()
    assert result.declared_deps == {}
    assert result.top_modules == {}
    assert result.unioned_libraries == ()
    assert result.entrypoints == {}


def test_plugin_parse_failure_does_not_propagate() -> None:
    # Malformed pyproject.toml — plugin's declared_deps already returns ()
    # and we still surface the ecosystem (manifest is present).
    result = scan(PYTHON_FAILURE)
    assert result.ecosystems == ("python",)
    assert result.declared_deps["python"] == ()
    assert result.top_modules["python"] == ()
    assert result.unioned_libraries == ()


def test_layout_excludes_hidden_and_vendor_directories(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "target").mkdir()
    (tmp_path / "build").mkdir()
    (tmp_path / "dist").mkdir()
    (tmp_path / ".venv").mkdir()
    (tmp_path / "venv").mkdir()
    (tmp_path / ".tox").mkdir()
    (tmp_path / "vendor").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "README.md").write_text("readme", encoding="utf-8")

    result = scan(tmp_path)
    assert result.layout == ("src", "tests")


def test_layout_sorted_alphabetically(tmp_path: Path) -> None:
    (tmp_path / "zeta").mkdir()
    (tmp_path / "alpha").mkdir()
    (tmp_path / "mu").mkdir()
    result = scan(tmp_path)
    assert result.layout == ("alpha", "mu", "zeta")


def test_top_modules_capped_at_five(tmp_path: Path) -> None:
    pyproject = (
        "[project]\n"
        'name = "many"\n'
        'version = "0.0.1"\n'
        "dependencies = [" + ",".join(f'"dep{i}"' for i in range(8)) + "]\n"
    )
    (tmp_path / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    result = scan(tmp_path)
    assert len(result.declared_deps["python"]) == 8
    assert len(result.top_modules["python"]) == 5
    assert result.top_modules["python"] == result.declared_deps["python"][:5]


def test_node_entrypoint_reads_main_field(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"name":"x","version":"0.0.1","main":"lib/server.js","dependencies":{"react":"^18"}}',
        encoding="utf-8",
    )
    result = scan(tmp_path)
    assert result.entrypoints["node"] == "lib/server.js"


def test_node_entrypoint_falls_back_to_index_js(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"name":"x","version":"0.0.1","dependencies":{"react":"^18"}}',
        encoding="utf-8",
    )
    (tmp_path / "index.js").write_text("module.exports = {};\n", encoding="utf-8")
    result = scan(tmp_path)
    assert result.entrypoints["node"] == "index.js"


def test_node_entrypoint_empty_when_nothing_found(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"name":"x","version":"0.0.1"}',
        encoding="utf-8",
    )
    result = scan(tmp_path)
    assert result.entrypoints["node"] == ""


def test_python_entrypoint_picks_root_main(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "0.0.1"\n', encoding="utf-8"
    )
    (tmp_path / "__main__.py").write_text("print('hi')\n", encoding="utf-8")
    result = scan(tmp_path)
    assert result.entrypoints["python"] == "__main__.py"


def test_python_entrypoint_picks_setup_py_when_no_main(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "0.0.1"\n', encoding="utf-8"
    )
    (tmp_path / "setup.py").write_text("from setuptools import setup\nsetup()\n", encoding="utf-8")
    result = scan(tmp_path)
    assert result.entrypoints["python"] == "setup.py"


def test_python_entrypoint_picks_src_pkg_main(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "0.0.1"\n', encoding="utf-8"
    )
    pkg = tmp_path / "src" / "mypkg"
    pkg.mkdir(parents=True)
    (pkg / "__main__.py").write_text("print('hi')\n", encoding="utf-8")
    result = scan(tmp_path)
    assert result.entrypoints["python"] == "src/mypkg/__main__.py"


def test_rust_entrypoint(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "x"\nversion = "0.1.0"\nedition = "2021"\n', encoding="utf-8"
    )
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.rs").write_text("fn main() {}\n", encoding="utf-8")
    result = scan(tmp_path)
    assert result.entrypoints["rust"] == "src/main.rs"


def test_go_entrypoint_first_cmd(tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text("module x\n\ngo 1.22\n", encoding="utf-8")
    cmd_alpha = tmp_path / "cmd" / "alpha"
    cmd_alpha.mkdir(parents=True)
    (cmd_alpha / "main.go").write_text("package main\nfunc main(){}\n", encoding="utf-8")
    cmd_beta = tmp_path / "cmd" / "beta"
    cmd_beta.mkdir(parents=True)
    (cmd_beta / "main.go").write_text("package main\nfunc main(){}\n", encoding="utf-8")
    result = scan(tmp_path)
    # Sorted lookup picks "alpha" first deterministically.
    assert result.entrypoints["go"] == "cmd/alpha/main.go"


def test_layout_returns_empty_when_repo_root_unreadable(tmp_path: Path) -> None:
    # iterdir failing (e.g., permission denied) collapses to an empty
    # layout instead of raising — exercises the OSError branch in
    # ``_layout``.
    real_iterdir = Path.iterdir

    def fake_iterdir(self: Path) -> object:
        if self == tmp_path:
            raise PermissionError("blocked")
        return real_iterdir(self)

    with patch.object(Path, "iterdir", fake_iterdir):
        result = scan(tmp_path)
    assert result.layout == ()


def test_node_entrypoint_invalid_json_falls_back_to_index_js(tmp_path: Path) -> None:
    # Malformed package.json triggers the JSONDecodeError branch; the
    # resolver then falls back to index.js when present.
    (tmp_path / "package.json").write_text("{not-json", encoding="utf-8")
    (tmp_path / "index.js").write_text("module.exports = {};\n", encoding="utf-8")
    result = scan(tmp_path)
    assert result.entrypoints["node"] == "index.js"


def test_node_entrypoint_main_field_non_string_ignored(tmp_path: Path) -> None:
    # ``main`` is a number — the isinstance check in _node_entrypoint
    # rejects it and falls back to index.js.
    (tmp_path / "package.json").write_text(
        '{"name":"x","version":"0.0.1","main":42}',
        encoding="utf-8",
    )
    (tmp_path / "index.js").write_text("module.exports = {};\n", encoding="utf-8")
    result = scan(tmp_path)
    assert result.entrypoints["node"] == "index.js"


def test_node_entrypoint_top_level_array_ignored(tmp_path: Path) -> None:
    # JSON parses successfully but is not a dict — exercises the
    # isinstance(data, dict) guard.
    (tmp_path / "package.json").write_text("[1, 2, 3]", encoding="utf-8")
    (tmp_path / "index.js").write_text("module.exports = {};\n", encoding="utf-8")
    result = scan(tmp_path)
    assert result.entrypoints["node"] == "index.js"


def test_go_entrypoint_empty_when_cmd_dir_missing(tmp_path: Path) -> None:
    # No ``cmd/`` directory — _go_entrypoint hits the early return.
    (tmp_path / "go.mod").write_text("module x\n\ngo 1.22\n", encoding="utf-8")
    result = scan(tmp_path)
    assert result.entrypoints["go"] == ""


def test_go_entrypoint_empty_when_no_main_go_in_subdirs(tmp_path: Path) -> None:
    # ``cmd/`` exists but contains a sibling file rather than a
    # subdirectory with main.go — exercises the loop-completes-without-
    # match branch.
    (tmp_path / "go.mod").write_text("module x\n\ngo 1.22\n", encoding="utf-8")
    cmd = tmp_path / "cmd"
    cmd.mkdir()
    (cmd / "README.md").write_text("hello\n", encoding="utf-8")
    nested = cmd / "alpha"
    nested.mkdir()
    # No main.go in nested/
    (nested / "helper.go").write_text("package alpha\n", encoding="utf-8")
    result = scan(tmp_path)
    assert result.entrypoints["go"] == ""
