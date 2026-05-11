"""Tests for cross-AI CLI detection and reviewer routing.

Covers PATH-scan behavior with fully isolated env/path overrides (no host PATH
bleed) and the family-aware ``pick_reviewer`` routing rule: prefer a reviewer
from a different model family than the executor, fall back to same-family,
honor the ``allowed_clis`` filter, and tolerate unparseable executor labels.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from tools.cross_ai.detect import CLI, CLI_FAMILY, detect_clis, pick_reviewer


def _make_executable(path: Path) -> None:
    """Create ``path`` as a zero-byte file with the user-execute bit set."""
    path.write_text("")
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def test_detect_clis_returns_empty_tuple_when_path_has_no_clis(tmp_path: Path) -> None:
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()

    assert detect_clis(env={"PATH": str(empty_dir)}) == ()


def test_detect_clis_finds_codex_when_only_codex_on_path(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _make_executable(bin_dir / "codex")

    assert detect_clis(env={"PATH": str(bin_dir)}) == (CLI.codex,)


def test_detect_clis_returns_enum_declaration_order_regardless_of_path_order(
    tmp_path: Path,
) -> None:
    # Place each CLI in its own directory and order PATH so gemini comes first.
    gemini_dir = tmp_path / "g"
    claude_dir = tmp_path / "c"
    codex_dir = tmp_path / "x"
    for d in (gemini_dir, claude_dir, codex_dir):
        d.mkdir()
    _make_executable(gemini_dir / "gemini")
    _make_executable(claude_dir / "claude")
    _make_executable(codex_dir / "codex")

    path_value = os.pathsep.join([str(gemini_dir), str(claude_dir), str(codex_dir)])
    # Declaration order in the enum is codex, claude, gemini — assert that the
    # returned tuple follows the enum, not the PATH ordering above.
    assert detect_clis(env={"PATH": path_value}) == (CLI.codex, CLI.claude, CLI.gemini)


def test_detect_clis_uses_path_argument_over_env_path(tmp_path: Path) -> None:
    bin_dir = tmp_path / "explicit"
    bin_dir.mkdir()
    _make_executable(bin_dir / "claude")

    # env PATH points nowhere useful; path= argument should win.
    result = detect_clis(env={"PATH": str(tmp_path / "missing")}, path=str(bin_dir))

    assert result == (CLI.claude,)


def test_pick_reviewer_returns_different_family_when_available() -> None:
    assert pick_reviewer("claude", (CLI.codex, CLI.gemini), ()) == CLI.codex


def test_pick_reviewer_returns_same_family_when_no_different_family_available() -> None:
    assert pick_reviewer("claude", (CLI.claude,), ()) == CLI.claude


def test_pick_reviewer_returns_first_available_when_executor_model_is_none() -> None:
    assert pick_reviewer(None, (CLI.codex, CLI.gemini), ()) == CLI.codex


def test_pick_reviewer_returns_none_when_no_clis_available() -> None:
    assert pick_reviewer("claude", (), ()) is None


def test_pick_reviewer_filters_by_allowed_clis_and_returns_remaining() -> None:
    assert pick_reviewer("claude", (CLI.codex, CLI.gemini), ("gemini",)) == CLI.gemini


def test_pick_reviewer_returns_none_when_allowed_clis_excludes_everything() -> None:
    assert pick_reviewer("claude", (CLI.codex,), ("gemini",)) is None


def test_pick_reviewer_treats_unparseable_executor_model_as_no_executor() -> None:
    # "OpenAI-GPT-5" does not parse as a CLI member; falls through to the
    # "no executor family" branch — every available CLI counts as different,
    # so the first available wins.
    assert pick_reviewer("OpenAI-GPT-5", (CLI.codex, CLI.claude), ()) == CLI.codex


def test_cli_family_table_covers_every_cli_member() -> None:
    # Routing relies on CLI_FAMILY having an entry for every CLI; if a new CLI
    # lands without a family entry, pick_reviewer would silently classify it.
    assert set(CLI_FAMILY.keys()) == set(CLI)
