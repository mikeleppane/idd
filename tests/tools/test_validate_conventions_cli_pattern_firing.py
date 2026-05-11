"""CLI pattern-firing coverage for ``--target conventions``.

The CLI accepts ``--commit <sha>`` and ``--diff-file <path>`` flags so that
``commit_body`` / ``diff`` scope rules in ``.forge/conventions.json`` actually
fire at validate time, not just at shape time. Without either flag the call
remains shape-only — backward-compatible with the prior CLI contract.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tools import validate as validate_pkg
from tools.validate import conventions as conventions_module


def _well_formed(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "ban-claude-coauthor",
        "source_file": "AGENTS.md",
        "source_line": 42,
        "pattern_kind": "forbidden_text",
        "pattern": r"Co-Authored-By: Claude",
        "scope": ["commit_body"],
        "severity": "BLOCK",
    }
    base.update(overrides)
    return base


def _write_conventions(repo_root: Path, entries: list[dict[str, Any]]) -> None:
    forge = repo_root / ".forge"
    forge.mkdir(exist_ok=True)
    (forge / "conventions.json").write_text(json.dumps(entries), encoding="utf-8")


def _init_git_repo(repo_root: Path) -> None:
    """Make a throwaway git repo so ``git show -s --format=%B <sha>`` works.

    Tests that need a real SHA seed a single commit and surface its hash to
    the caller.
    """
    subprocess.run(
        ["git", "init", "-q", "-b", "main", str(repo_root)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_root), "config", "user.email", "test@example.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_root), "config", "user.name", "Test"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_root), "config", "commit.gpgsign", "false"],
        check=True,
        capture_output=True,
    )


def _commit(repo_root: Path, body: str) -> str:
    """Author one commit with ``body`` and return its full SHA."""
    (repo_root / "README.md").write_text("seed", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repo_root), "add", "README.md"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_root), "commit", "-q", "-m", body],
        check=True,
        capture_output=True,
    )
    result = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


@pytest.fixture(autouse=True)
def _reset_load_cache() -> Iterator[None]:
    """Clear the per-process conventions cache so tests start clean."""
    conventions_module._LOAD_CACHE.clear()
    yield
    conventions_module._LOAD_CACHE.clear()


# --- Commit-body pattern firing ---------------------------------------------


def test_cli_commit_flag_fires_commit_body_rule(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--commit <sha>`` feeds the commit body so commit_body rules fire."""
    _init_git_repo(tmp_path)
    _write_conventions(
        tmp_path,
        [
            _well_formed(
                id="ban-claude-coauthor",
                pattern=r"Co-Authored-By: Claude",
                scope=["commit_body"],
                severity="BLOCK",
            )
        ],
    )
    sha = _commit(tmp_path, "feat: x\n\nCo-Authored-By: Claude <noreply@anthropic.com>\n")
    rc = validate_pkg.main(
        [
            "--target",
            "conventions",
            "--repo-root",
            str(tmp_path),
            "--commit",
            sha,
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == 1, payload
    fired = [f for f in payload["findings"] if "ban-claude-coauthor" in f["message"]]
    assert len(fired) == 1, payload
    assert fired[0]["severity"] == "BLOCK"


def test_cli_commit_flag_silent_when_body_clean(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _init_git_repo(tmp_path)
    _write_conventions(
        tmp_path,
        [
            _well_formed(
                id="ban-claude-coauthor",
                pattern=r"Co-Authored-By: Claude",
                scope=["commit_body"],
                severity="BLOCK",
            )
        ],
    )
    sha = _commit(tmp_path, "feat: ok\n\nNothing to see here.\n")
    rc = validate_pkg.main(
        [
            "--target",
            "conventions",
            "--repo-root",
            str(tmp_path),
            "--commit",
            sha,
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == 0, payload
    assert payload["findings"] == []


def test_cli_commit_flag_warns_on_unknown_sha(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Missing SHA degrades to WARN; other shape rules still run."""
    _init_git_repo(tmp_path)
    _write_conventions(
        tmp_path,
        [
            _well_formed(
                id="ban-claude-coauthor",
                pattern=r"Co-Authored-By: Claude",
                scope=["commit_body"],
                severity="BLOCK",
            )
        ],
    )
    rc = validate_pkg.main(
        [
            "--target",
            "conventions",
            "--repo-root",
            str(tmp_path),
            "--commit",
            "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    # WARN is below EXIT_NONZERO_SEVERITIES so the run still exits 0.
    assert rc == 0, payload
    warns = [f for f in payload["findings"] if f["severity"] == "WARN"]
    assert warns, payload
    assert any("commit lookup failed" in f["message"] for f in warns), payload


# --- Diff-file pattern firing ------------------------------------------------


def test_cli_diff_file_flag_fires_glob_rule(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--diff-file <path>`` feeds the unified diff so diff-scope rules fire."""
    _write_conventions(
        tmp_path,
        [
            _well_formed(
                id="ban-env-files",
                pattern_kind="filename_glob_forbidden",
                pattern="**/*.env",
                scope=["diff"],
                severity="HIGH",
            )
        ],
    )
    diff_path = tmp_path / "feature.diff"
    diff_path.write_text(
        "diff --git a/configs/.env b/configs/.env\n"
        "--- /dev/null\n"
        "+++ b/configs/.env\n"
        "@@\n"
        "+SECRET_KEY=abc\n",
        encoding="utf-8",
    )
    rc = validate_pkg.main(
        [
            "--target",
            "conventions",
            "--repo-root",
            str(tmp_path),
            "--diff-file",
            str(diff_path),
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == 1, payload
    fired = [f for f in payload["findings"] if "ban-env-files" in f["message"]]
    assert len(fired) == 1, payload
    assert fired[0]["severity"] == "HIGH"


def test_cli_diff_file_flag_errors_when_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Missing diff file is a CLI-misuse error (BLOCK, exit non-zero)."""
    _write_conventions(tmp_path, [_well_formed()])
    rc = validate_pkg.main(
        [
            "--target",
            "conventions",
            "--repo-root",
            str(tmp_path),
            "--diff-file",
            str(tmp_path / "does-not-exist.diff"),
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == 1, payload
    assert any(
        f["severity"] == "BLOCK" and "diff-file" in f["message"].lower()
        for f in payload["findings"]
    ), payload


def test_cli_plain_glob_matches_nested_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Plain ``*.env`` (no ``**``) must match a nested ``configs/.env`` path."""
    _write_conventions(
        tmp_path,
        [
            _well_formed(
                id="ban-env-plain",
                pattern_kind="filename_glob_forbidden",
                pattern="*.env",
                scope=["diff"],
                severity="HIGH",
            )
        ],
    )
    diff_path = tmp_path / "feature.diff"
    diff_path.write_text("+++ b/configs/.env\n@@\n+SECRET=1\n", encoding="utf-8")
    rc = validate_pkg.main(
        [
            "--target",
            "conventions",
            "--repo-root",
            str(tmp_path),
            "--diff-file",
            str(diff_path),
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == 1, payload
    fired = [f for f in payload["findings"] if "ban-env-plain" in f["message"]]
    assert len(fired) == 1, payload


def test_cli_plain_glob_skips_non_matching_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``*.env`` must NOT match ``README.md`` even with new nested semantics."""
    _write_conventions(
        tmp_path,
        [
            _well_formed(
                id="ban-env-plain",
                pattern_kind="filename_glob_forbidden",
                pattern="*.env",
                scope=["diff"],
                severity="HIGH",
            )
        ],
    )
    diff_path = tmp_path / "feature.diff"
    diff_path.write_text("+++ b/README.md\n@@\n+text\n", encoding="utf-8")
    rc = validate_pkg.main(
        [
            "--target",
            "conventions",
            "--repo-root",
            str(tmp_path),
            "--diff-file",
            str(diff_path),
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == 0, payload
    assert payload["findings"] == []


# --- Backward compat: no flags = shape only ---------------------------------


def test_cli_no_flags_is_shape_only(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Without ``--commit`` / ``--diff-file`` the CLI does shape validation only.

    A commit_body-scope rule that would fire against arbitrary commit bodies
    must NOT fire here because the CLI was given no body to test against.
    """
    _write_conventions(
        tmp_path,
        [
            _well_formed(
                id="ban-claude-coauthor",
                pattern=r"Co-Authored-By: Claude",
                scope=["commit_body"],
                severity="BLOCK",
            )
        ],
    )
    rc = validate_pkg.main(["--target", "conventions", "--repo-root", str(tmp_path)])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == 0, payload
    assert payload["findings"] == []


def test_cli_flags_ignored_by_other_targets(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--commit`` / ``--diff-file`` are accepted but ignored by non-conventions targets."""
    # ``health`` is repo-wide and ignores positional path. Pass the flags and
    # verify argparse accepts them without erroring.
    (tmp_path / ".forge").mkdir()
    rc = validate_pkg.main(
        [
            "--target",
            "health",
            "--repo-root",
            str(tmp_path),
            "--commit",
            "abc123",
            "--diff-file",
            "/dev/null",
        ]
    )
    captured = capsys.readouterr()
    json.loads(captured.out)  # must be valid JSON
    # rc may be 0 or 1 depending on health rules; we only care that argparse accepted the flags.
    assert rc in (0, 1)


# --- Cache invalidation -----------------------------------------------------


def test_cached_load_invalidates_on_file_change(tmp_path: Path) -> None:
    """The conventions parse cache invalidates when conventions.json changes."""
    _write_conventions(
        tmp_path,
        [_well_formed(id="rule-one", pattern=r"AAA")],
    )
    first = conventions_module._cached_load_conventions(tmp_path)
    assert [r.id for r in first] == ["rule-one"]

    # Re-write with a different rule + bump mtime so the cache invalidates.
    # The implementation uses (path, mtime_ns) as the cache key so a real
    # file change naturally misses the cache.
    new_mtime_ns = (tmp_path / ".forge" / "conventions.json").stat().st_mtime_ns + 1_000_000_000
    _write_conventions(
        tmp_path,
        [_well_formed(id="rule-two", pattern=r"BBB")],
    )
    os.utime(
        tmp_path / ".forge" / "conventions.json",
        ns=(new_mtime_ns, new_mtime_ns),
    )
    second = conventions_module._cached_load_conventions(tmp_path)
    assert [r.id for r in second] == ["rule-two"]


def test_cached_load_returns_empty_when_file_missing(tmp_path: Path) -> None:
    assert conventions_module._cached_load_conventions(tmp_path) == []
