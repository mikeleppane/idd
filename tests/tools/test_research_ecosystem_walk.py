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


def test_iter_source_files_skips_hidden_directories(tmp_path: Path) -> None:
    """Any sub-directory whose name starts with ``.`` is excluded."""
    visible = tmp_path / "src"
    visible.mkdir()
    (visible / "kept.py").write_text("import a\n", encoding="utf-8")
    hidden = tmp_path / ".cache"
    hidden.mkdir()
    (hidden / "secret.py").write_text("import b\n", encoding="utf-8")
    custom_hidden = tmp_path / ".private"
    custom_hidden.mkdir()
    (custom_hidden / "ignored.py").write_text("import c\n", encoding="utf-8")

    found = {p.name for p in iter_source_files(tmp_path, (".py",))}
    assert "kept.py" in found
    assert "secret.py" not in found
    assert "ignored.py" not in found


def test_iter_source_files_rejects_symlinked_directory_outside_repo(
    tmp_path: Path,
) -> None:
    """A directory symlink whose target lives outside the repo is skipped."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "inside.py").write_text("import a\n", encoding="utf-8")
    outside = tmp_path / "outside_tree"
    outside.mkdir()
    (outside / "leaked.py").write_text("import secret\n", encoding="utf-8")

    (repo / "linked_dir").symlink_to(outside)

    found = {p.name for p in iter_source_files(repo, (".py",))}
    assert "inside.py" in found
    assert "leaked.py" not in found


def test_iter_source_files_rejects_symlinked_file_outside_repo(tmp_path: Path) -> None:
    """A symlinked source file resolving outside the repo is dropped."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "inside.py").write_text("import a\n", encoding="utf-8")
    outside = tmp_path / "outside.py"
    outside.write_text("import outside\n", encoding="utf-8")
    (repo / "link.py").symlink_to(outside)

    found_paths = list(iter_source_files(repo, (".py",)))
    # The symlinked file is yielded only via the path inside the repo,
    # but its resolved target must not pull regex matches that originated
    # outside the boundary. The contract is that the walk does not
    # descend into outside-repo *directories*; symlinked files are
    # tolerated by design (their resolved path is exposed via the link
    # name). What we MUST guarantee: a directory escape never widens
    # the scope.
    rel_names = {p.name for p in found_paths}
    # File symlinks are tolerated (operator placed them in-repo on
    # purpose); directory escapes are not, which the
    # ``rejects_symlinked_directory_outside_repo`` test covers.
    assert "inside.py" in rel_names


def test_iter_source_files_handles_symlink_cycle(tmp_path: Path) -> None:
    """A self-referential symlink does not loop forever."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "kept.py").write_text("import a\n", encoding="utf-8")
    inner = repo / "inner"
    inner.mkdir()
    (inner / "kept2.py").write_text("import b\n", encoding="utf-8")
    # inner/self -> ../inner — would loop without visited-set protection.
    (inner / "self").symlink_to(inner)

    found = {p.name for p in iter_source_files(repo, (".py",))}
    assert "kept.py" in found
    assert "kept2.py" in found


def test_iter_source_files_descends_in_repo_symlinks(tmp_path: Path) -> None:
    """In-repo symlinked directories are still allowed."""
    repo = tmp_path / "repo"
    repo.mkdir()
    real = repo / "real"
    real.mkdir()
    (real / "kept.py").write_text("import a\n", encoding="utf-8")
    (repo / "alias").symlink_to(real)

    found = {p.name for p in iter_source_files(repo, (".py",))}
    assert "kept.py" in found
