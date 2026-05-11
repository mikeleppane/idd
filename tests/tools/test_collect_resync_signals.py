"""Tests for tools.constitution_amend.collect_resync_signals."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest

from tools.constitution_amend import (
    AmendError,
    BootstrapSignals,
    SignalFile,
    collect_resync_signals,
)


def _write(repo: Path, rel: str, body: str | bytes) -> None:
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(body, bytes):
        target.write_bytes(body)
    else:
        target.write_text(body, encoding="utf-8")


def test_collect_resync_signals_returns_all_three_docs_in_priority_order(
    tmp_path: Path,
) -> None:
    _write(tmp_path, "AGENTS.md", "# Agents\nMUST cite Constitution.\n")
    _write(tmp_path, "CLAUDE.md", "# Claude\nUse the project skills.\n")
    _write(tmp_path, "README.md", "# Demo\n")

    result = collect_resync_signals(tmp_path)

    assert isinstance(result, BootstrapSignals)
    rels = [f.relative_path for f in result.files]
    assert rels == [
        PurePosixPath("AGENTS.md"),
        PurePosixPath("CLAUDE.md"),
        PurePosixPath("README.md"),
    ]
    assert all(isinstance(f, SignalFile) for f in result.files)
    assert all(not f.truncated for f in result.files)


def test_collect_resync_signals_returns_one_when_only_one_present(tmp_path: Path) -> None:
    _write(tmp_path, "CLAUDE.md", "# Claude\nSHALL log decisions.\n")

    result = collect_resync_signals(tmp_path)

    rels = [f.relative_path for f in result.files]
    assert rels == [PurePosixPath("CLAUDE.md")]


def test_collect_resync_signals_returns_empty_when_all_absent(tmp_path: Path) -> None:
    result = collect_resync_signals(tmp_path)

    assert result.files == []
    assert result.dropped_for_secrets == []
    assert result.truncated == []
    assert result.total_bytes == 0


def test_collect_resync_signals_skips_manifests(tmp_path: Path) -> None:
    # Manifests are present but only docs should be collected.
    _write(tmp_path, "pyproject.toml", '[project]\nname = "demo"\n')
    _write(tmp_path, "package.json", '{"name":"demo"}\n')
    _write(tmp_path, "Cargo.toml", '[package]\nname = "x"\n')
    _write(tmp_path, "AGENTS.md", "# Agents\n")

    result = collect_resync_signals(tmp_path)

    rels = [f.relative_path for f in result.files]
    assert PurePosixPath("AGENTS.md") in rels
    assert PurePosixPath("pyproject.toml") not in rels
    assert PurePosixPath("package.json") not in rels
    assert PurePosixPath("Cargo.toml") not in rels


def test_collect_resync_signals_drops_secret_in_agents(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "AGENTS.md",
        '# Agents\n\nConfig:\napi_key = "sk-live-1234567890abcdef"\n',
    )
    _write(tmp_path, "README.md", "# Readme\n")

    result = collect_resync_signals(tmp_path)

    rels = [f.relative_path for f in result.files]
    assert PurePosixPath("AGENTS.md") not in rels
    assert PurePosixPath("AGENTS.md") in result.dropped_for_secrets
    assert PurePosixPath("README.md") in rels


def test_collect_resync_signals_truncates_oversized_doc(tmp_path: Path) -> None:
    big_body = "x" * (20 * 1024)  # 20 KiB
    _write(tmp_path, "README.md", big_body)

    # Pin the nonce so the truncation marker is reproducible.
    result = collect_resync_signals(tmp_path, nonce_hex="0123456789abcdef")

    assert len(result.files) == 1
    sf = result.files[0]
    assert sf.truncated is True
    assert sf.relative_path in result.truncated
    marker = "\n--- truncated at 16384 bytes [0123456789abcdef] ---\n"
    assert sf.body.endswith(marker)


def test_collect_resync_signals_raises_when_repo_root_missing(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"

    with pytest.raises((FileNotFoundError, AmendError)):
        collect_resync_signals(missing)


def test_collect_resync_signals_is_deterministic(tmp_path: Path) -> None:
    _write(tmp_path, "AGENTS.md", "# Agents\n")
    _write(tmp_path, "README.md", "# Readme\n")

    first = collect_resync_signals(tmp_path)
    second = collect_resync_signals(tmp_path)

    assert first == second
