"""Tests for find_active_feature precedence in tools.state."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import state


def _make_feature(repo_root: Path, feature_id: str, current_phase: str) -> Path:
    folder = repo_root / ".idd" / "features" / feature_id
    folder.mkdir(parents=True, exist_ok=True)
    payload = {
        "feature_id": feature_id,
        "tier": "focused",
        "current_phase": current_phase,
        "phases": ({} if current_phase == "done" else {current_phase: {"status": "in_progress"}}),
        "skipped": [],
        "deviations": [],
        "commits": [],
    }
    (folder / "state.json").write_text(json.dumps(payload), encoding="utf-8")
    return folder


def test_find_active_feature_explicit_flag_wins(tmp_path: Path) -> None:
    _make_feature(tmp_path, "2026-05-01-alpha", "spec")
    explicit = _make_feature(tmp_path, "2026-05-02-beta", "execute")
    _make_feature(tmp_path, "2026-05-03-gamma", "verify")

    resolved = state.find_active_feature(tmp_path, feature_id="2026-05-02-beta")

    assert resolved == explicit


def test_find_active_feature_single_active_when_unambiguous(tmp_path: Path) -> None:
    _make_feature(tmp_path, "2026-05-01-alpha", "done")
    expected = _make_feature(tmp_path, "2026-05-02-beta", "execute")
    _make_feature(tmp_path, "2026-05-03-gamma", "done")

    resolved = state.find_active_feature(tmp_path)

    assert resolved == expected


def test_find_active_feature_errors_when_zero_active(tmp_path: Path) -> None:
    _make_feature(tmp_path, "2026-05-01-alpha", "done")
    _make_feature(tmp_path, "2026-05-02-beta", "done")

    with pytest.raises(state.StateError, match="no active feature"):
        state.find_active_feature(tmp_path)


def test_find_active_feature_errors_when_multiple_active(tmp_path: Path) -> None:
    _make_feature(tmp_path, "2026-05-01-alpha", "spec")
    _make_feature(tmp_path, "2026-05-02-beta", "execute")

    with pytest.raises(state.StateError, match="multiple active features"):
        state.find_active_feature(tmp_path)


def test_find_active_feature_errors_when_explicit_id_missing(tmp_path: Path) -> None:
    _make_feature(tmp_path, "2026-05-01-alpha", "spec")

    with pytest.raises(state.StateError, match="not found"):
        state.find_active_feature(tmp_path, feature_id="2026-05-99-nope")


def test_find_active_feature_skips_archive_directory(tmp_path: Path) -> None:
    """Archive folder under .idd/features/archive/ must not be considered active."""
    expected = _make_feature(tmp_path, "2026-05-02-beta", "execute")
    archive = tmp_path / ".idd" / "features" / "archive" / "2026-04-99-shipped"
    archive.mkdir(parents=True, exist_ok=True)
    payload = {
        "feature_id": "2026-04-99-shipped",
        "tier": "focused",
        "current_phase": "done",
        "phases": {},
        "skipped": [],
        "deviations": [],
        "commits": [],
    }
    (archive / "state.json").write_text(json.dumps(payload), encoding="utf-8")

    resolved = state.find_active_feature(tmp_path)

    assert resolved == expected
