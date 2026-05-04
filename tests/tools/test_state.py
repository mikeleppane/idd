"""Tests for tools.state — feature state.json read/write/transition."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import state


def test_read_state_returns_parsed_dict_for_valid_file(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    payload = {
        "feature_id": "2026-05-03-demo-feature",
        "tier": "focused",
        "current_phase": "spec",
        "phases": {"spec": {"status": "in_progress"}},
        "skipped": [],
        "deviations": [],
        "commits": [],
    }
    target.write_text(json.dumps(payload), encoding="utf-8")

    result = state.read_state(target)

    assert result == payload


def test_read_state_raises_on_missing_file(tmp_path: Path) -> None:
    with pytest.raises(state.StateError, match="not found"):
        state.read_state(tmp_path / "missing.json")


def test_read_state_raises_on_invalid_json(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    target.write_text("{not json", encoding="utf-8")

    with pytest.raises(state.StateError, match="invalid JSON"):
        state.read_state(target)
