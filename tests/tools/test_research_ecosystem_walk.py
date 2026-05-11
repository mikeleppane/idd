"""Tests for the shared filesystem walk helpers.

The eleven ecosystem plugins share ``iter_source_files`` /
``scan_with_regex`` / ``normalize_dep`` from
``tools.research.ecosystems._walk``. These tests cover boundary
branches that the per-plugin tests don't exercise — a missing
repo root, an excluded directory mid-tree, the ``max_files`` ceiling,
and ``OSError`` on read.
"""

import re
from pathlib import Path
from unittest.mock import patch

from tools.research.ecosystems._walk import (
    iter_source_files,
    normalize_dep,
    scan_with_regex,
)


def test_iter_source_files_missing_repo_root_returns_empty(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    assert list(iter_source_files(missing, (".py",))) == []


def test_iter_source_files_skips_excluded_dir_names(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "kept.py").write_text("import a\n", encoding="utf-8")
    skipped = tmp_path / "node_modules"
    skipped.mkdir()
    (skipped / "ignored.py").write_text("import b\n", encoding="utf-8")

    found = {p.name for p in iter_source_files(tmp_path, (".py",))}
    assert "kept.py" in found
    assert "ignored.py" not in found


def test_iter_source_files_recovers_from_permission_error(tmp_path: Path) -> None:
    # Simulate iterdir raising PermissionError on a subdirectory: the
    # walk swallows the error and continues with siblings.
    good = tmp_path / "good"
    good.mkdir()
    (good / "kept.py").write_text("import a\n", encoding="utf-8")
    bad = tmp_path / "bad"
    bad.mkdir()

    real_iterdir = Path.iterdir

    def fake_iterdir(self: Path) -> object:
        if self == bad:
            raise PermissionError("blocked")
        return real_iterdir(self)

    with patch.object(Path, "iterdir", fake_iterdir):
        found = {p.name for p in iter_source_files(tmp_path, (".py",))}
    assert "kept.py" in found


def test_scan_with_regex_respects_max_files_ceiling(tmp_path: Path) -> None:
    pattern = re.compile(r"^\s*import\s+(\w+)")
    for i in range(5):
        (tmp_path / f"m{i}.py").write_text(f"import mod{i}\n", encoding="utf-8")
    # Force the inner ``count >= max_files`` short-circuit.
    matches = scan_with_regex(tmp_path, (".py",), pattern, max_files=2)
    assert len(matches) <= 2


def test_scan_with_regex_recovers_from_read_error(tmp_path: Path) -> None:
    pattern = re.compile(r"^\s*import\s+(\w+)")
    bad = tmp_path / "bad.py"
    bad.write_text("import oops\n", encoding="utf-8")
    good = tmp_path / "good.py"
    good.write_text("import okay\n", encoding="utf-8")

    real_read_text = Path.read_text

    def fake_read_text(self: Path, *args: object, **kwargs: object) -> str:
        if self == bad:
            raise OSError("blocked")
        return real_read_text(self, *args, **kwargs)  # type: ignore[arg-type]

    with patch.object(Path, "read_text", fake_read_text):
        matches = scan_with_regex(tmp_path, (".py",), pattern)
    assert "okay" in matches
    assert "oops" not in matches


def test_scan_with_regex_outer_oserror_falls_back_to_empty(tmp_path: Path) -> None:
    pattern = re.compile(r"^\s*import\s+(\w+)")

    def boom(*_args: object, **_kwargs: object) -> object:
        raise OSError("walker exploded")

    # Patch the iterator helper to raise on the first ``next()`` call so
    # the outer try/except in scan_with_regex catches it.
    with patch("tools.research.ecosystems._walk.iter_source_files", boom):
        assert scan_with_regex(tmp_path, (".py",), pattern) == ()


def test_normalize_dep_lowercases_and_swaps_hyphens() -> None:
    assert normalize_dep("My-Package-Name") == "my_package_name"
    assert normalize_dep("ALREADY_UNDERSCORED") == "already_underscored"
    assert normalize_dep("") == ""
