"""Tests for review-target sub-state in tools.state."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tools import state


def _payload_with_review(targets_done: list[str], current_target: str | None) -> dict[str, Any]:
    review_block: dict[str, Any] = {"status": "in_progress", "started_at": "2026-05-04T11:00:00Z"}
    if targets_done:
        review_block["targets_done"] = targets_done
    if current_target is not None:
        review_block["current_target"] = current_target
    return {
        "feature_id": "2026-05-04-demo",
        "tier": "standard",
        "current_phase": "review",
        "phases": {
            "spec": {"status": "done"},
            "scenarios": {"status": "done"},
            "plan": {"status": "done"},
            "crucible": {"status": "done"},
            "review": review_block,
        },
        "skipped": [],
        "deviations": [],
        "commits": [],
    }


def test_state_schema_accepts_review_target_fields(tmp_path: Path, schemas_dir: Path) -> None:
    payload = _payload_with_review(targets_done=["plan"], current_target="code")
    target = tmp_path / "state.json"

    state.write_state(target, payload, schema_path=schemas_dir / "state.schema.json")

    written = json.loads(target.read_text(encoding="utf-8"))
    assert written["phases"]["review"]["current_target"] == "code"
    assert written["phases"]["review"]["targets_done"] == ["plan"]


def test_state_schema_accepts_review_without_target_fields(
    tmp_path: Path, schemas_dir: Path
) -> None:
    """Backward compat: fixtures without target sub-state still validate."""
    payload = _payload_with_review(targets_done=[], current_target=None)
    target = tmp_path / "state.json"

    state.write_state(target, payload, schema_path=schemas_dir / "state.schema.json")

    written = json.loads(target.read_text(encoding="utf-8"))
    assert "current_target" not in written["phases"]["review"]
    assert "targets_done" not in written["phases"]["review"]


def test_state_schema_rejects_unknown_review_target_value(
    tmp_path: Path, schemas_dir: Path
) -> None:
    payload = _payload_with_review(targets_done=[], current_target="plan")
    payload["phases"]["review"]["current_target"] = "everything"
    target = tmp_path / "state.json"

    with pytest.raises(state.StateError, match="schema"):
        state.write_state(target, payload, schema_path=schemas_dir / "state.schema.json")
