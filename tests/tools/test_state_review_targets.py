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


def test_set_review_target_initializes_target_fields(tmp_path: Path, schemas_dir: Path) -> None:
    target = tmp_path / "state.json"
    initial = _payload_with_review(targets_done=[], current_target=None)
    state.write_state(target, initial, schema_path=schemas_dir / "state.schema.json")

    result = state.set_review_target(
        target,
        review_target="plan",
        schema_path=schemas_dir / "state.schema.json",
    )

    assert result["phases"]["review"]["current_target"] == "plan"
    assert result["phases"]["review"]["targets_done"] == []


def test_set_review_target_rejects_unknown_target(tmp_path: Path, schemas_dir: Path) -> None:
    target = tmp_path / "state.json"
    state.write_state(
        target,
        _payload_with_review(targets_done=[], current_target=None),
        schema_path=schemas_dir / "state.schema.json",
    )

    with pytest.raises(state.StateError, match="review_target"):
        state.set_review_target(
            target,
            review_target="docs",
            schema_path=schemas_dir / "state.schema.json",
        )


def test_complete_review_target_appends_and_returns(tmp_path: Path, schemas_dir: Path) -> None:
    target = tmp_path / "state.json"
    initial = _payload_with_review(targets_done=[], current_target="plan")
    state.write_state(target, initial, schema_path=schemas_dir / "state.schema.json")

    result = state.complete_review_target(
        target,
        review_target="plan",
        schema_path=schemas_dir / "state.schema.json",
    )

    assert result["phases"]["review"]["targets_done"] == ["plan"]


def test_complete_review_target_rejects_when_not_current(tmp_path: Path, schemas_dir: Path) -> None:
    target = tmp_path / "state.json"
    state.write_state(
        target,
        _payload_with_review(targets_done=[], current_target="plan"),
        schema_path=schemas_dir / "state.schema.json",
    )

    with pytest.raises(state.StateError, match="current_target is 'plan'"):
        state.complete_review_target(
            target,
            review_target="code",
            schema_path=schemas_dir / "state.schema.json",
        )


def test_complete_review_target_idempotent_within_same_target(
    tmp_path: Path, schemas_dir: Path
) -> None:
    target = tmp_path / "state.json"
    initial = _payload_with_review(targets_done=["plan"], current_target="plan")
    state.write_state(target, initial, schema_path=schemas_dir / "state.schema.json")

    result = state.complete_review_target(
        target,
        review_target="plan",
        schema_path=schemas_dir / "state.schema.json",
    )

    assert result["phases"]["review"]["targets_done"] == ["plan"]


def test_complete_phase_review_blocks_when_only_plan_done(
    tmp_path: Path, schemas_dir: Path
) -> None:
    target = tmp_path / "state.json"
    initial = _payload_with_review(targets_done=["plan"], current_target="plan")
    state.write_state(target, initial, schema_path=schemas_dir / "state.schema.json")

    with pytest.raises(state.StateError, match="both review targets"):
        state.complete_phase(
            target,
            phase="review",
            schema_path=schemas_dir / "state.schema.json",
            now="2026-05-04T12:00:00Z",
        )


def test_complete_phase_review_passes_when_both_targets_done(
    tmp_path: Path, schemas_dir: Path
) -> None:
    target = tmp_path / "state.json"
    initial = _payload_with_review(targets_done=["plan", "code"], current_target="code")
    state.write_state(target, initial, schema_path=schemas_dir / "state.schema.json")

    result = state.complete_phase(
        target,
        phase="review",
        schema_path=schemas_dir / "state.schema.json",
        now="2026-05-04T12:00:00Z",
    )

    assert result["phases"]["review"]["status"] == "done"
    assert result["phases"]["review"]["completed_at"] == "2026-05-04T12:00:00Z"


def test_set_review_target_rejects_when_review_status_done(
    tmp_path: Path, schemas_dir: Path
) -> None:
    target = tmp_path / "state.json"
    initial = _payload_with_review(targets_done=["plan", "code"], current_target="code")
    initial["phases"]["review"]["status"] = "done"
    initial["phases"]["review"]["completed_at"] = "2026-05-04T12:00:00Z"
    state.write_state(target, initial, schema_path=schemas_dir / "state.schema.json")

    with pytest.raises(state.StateError, match="status is 'done'"):
        state.set_review_target(
            target,
            review_target="plan",
            schema_path=schemas_dir / "state.schema.json",
        )


def test_set_review_target_rejects_when_review_status_pending(
    tmp_path: Path, schemas_dir: Path
) -> None:
    target = tmp_path / "state.json"
    initial = _payload_with_review(targets_done=[], current_target=None)
    initial["phases"]["review"]["status"] = "pending"
    state.write_state(target, initial, schema_path=schemas_dir / "state.schema.json")

    with pytest.raises(state.StateError, match="status is 'pending'"):
        state.set_review_target(
            target,
            review_target="plan",
            schema_path=schemas_dir / "state.schema.json",
        )


def test_complete_review_target_rejects_when_review_status_done(
    tmp_path: Path, schemas_dir: Path
) -> None:
    target = tmp_path / "state.json"
    initial = _payload_with_review(targets_done=["plan"], current_target="plan")
    initial["phases"]["review"]["status"] = "done"
    initial["phases"]["review"]["completed_at"] = "2026-05-04T12:00:00Z"
    state.write_state(target, initial, schema_path=schemas_dir / "state.schema.json")

    with pytest.raises(state.StateError, match="status is 'done'"):
        state.complete_review_target(
            target,
            review_target="plan",
            schema_path=schemas_dir / "state.schema.json",
        )


def test_complete_phase_non_review_unchanged_by_target_gate(
    tmp_path: Path, schemas_dir: Path
) -> None:
    """Gate must not affect other phases."""
    target = tmp_path / "state.json"
    initial = {
        "feature_id": "2026-05-04-demo",
        "tier": "focused",
        "current_phase": "spec",
        "phases": {"spec": {"status": "in_progress"}},
        "skipped": [],
        "deviations": [],
        "commits": [],
    }
    state.write_state(target, initial, schema_path=schemas_dir / "state.schema.json")

    result = state.complete_phase(
        target,
        phase="spec",
        schema_path=schemas_dir / "state.schema.json",
        now="2026-05-04T12:00:00Z",
    )

    assert result["phases"]["spec"]["status"] == "done"


def test_dual_review_pass_completes_without_manual_target_replay(
    tmp_path: Path, schemas_dir: Path
) -> None:
    """End-to-end dual-review flow: target=plan, execute, target=code, complete.

    Reproduces the real /idd:review consumer chain: the second invocation
    must NOT manually re-record target=plan to clear the gate; preserved
    targets_done plus the per-target helpers are sufficient.
    """
    target = tmp_path / "state.json"
    schema = schemas_dir / "state.schema.json"

    initial = {
        "feature_id": "2026-05-04-demo",
        "tier": "standard",
        "current_phase": "crucible",
        "phases": {
            "spec": {"status": "done"},
            "scenarios": {"status": "done"},
            "plan": {"status": "done"},
            "crucible": {"status": "in_progress"},
        },
        "skipped": [],
        "deviations": [],
        "commits": [],
    }
    state.write_state(target, initial, schema_path=schema)
    state.complete_phase(target, phase="crucible", schema_path=schema)

    state.start_phase(target, phase="review", schema_path=schema)
    state.set_review_target(target, review_target="plan", schema_path=schema)
    state.complete_review_target(target, review_target="plan", schema_path=schema)

    state.start_phase(target, phase="execute", schema_path=schema)
    state.complete_phase(target, phase="execute", schema_path=schema)

    state.start_phase(target, phase="review", schema_path=schema)
    after_restart = state.read_state(target, schema_path=schema)
    assert after_restart["phases"]["review"]["targets_done"] == ["plan"]

    state.set_review_target(target, review_target="code", schema_path=schema)
    state.complete_review_target(target, review_target="code", schema_path=schema)

    result = state.complete_phase(target, phase="review", schema_path=schema)
    assert result["phases"]["review"]["status"] == "done"
    assert sorted(result["phases"]["review"]["targets_done"]) == ["code", "plan"]


def test_state_schema_rejects_review_target_fields_on_non_review_phase(
    tmp_path: Path, schemas_dir: Path
) -> None:
    """Review-target sub-state belongs only to phases.review."""
    payload = {
        "feature_id": "2026-05-04-demo",
        "tier": "standard",
        "current_phase": "spec",
        "phases": {
            "spec": {
                "status": "in_progress",
                "current_target": "plan",
            },
        },
        "skipped": [],
        "deviations": [],
        "commits": [],
    }
    target = tmp_path / "state.json"

    with pytest.raises(state.StateError, match="schema"):
        state.write_state(target, payload, schema_path=schemas_dir / "state.schema.json")


def test_start_phase_review_preserves_targets_done_across_restart(
    tmp_path: Path, schemas_dir: Path
) -> None:
    """Second /idd:review pass must not wipe target audit from the first pass."""
    target = tmp_path / "state.json"
    initial = _payload_with_review(targets_done=["plan"], current_target="plan")
    initial["current_phase"] = "execute"
    initial["phases"]["execute"] = {"status": "done"}
    state.write_state(target, initial, schema_path=schemas_dir / "state.schema.json")

    result = state.start_phase(
        target,
        phase="review",
        schema_path=schemas_dir / "state.schema.json",
        now="2026-05-04T12:30:00Z",
    )

    assert result["phases"]["review"]["status"] == "in_progress"
    assert result["phases"]["review"]["targets_done"] == ["plan"]
    assert result["phases"]["review"]["current_target"] == "plan"


def test_start_phase_non_review_does_not_carry_review_keys(
    tmp_path: Path, schemas_dir: Path
) -> None:
    target = tmp_path / "state.json"
    initial = _payload_with_review(targets_done=["plan"], current_target="plan")
    state.write_state(target, initial, schema_path=schemas_dir / "state.schema.json")

    result = state.start_phase(
        target,
        phase="execute",
        schema_path=schemas_dir / "state.schema.json",
        now="2026-05-04T12:30:00Z",
    )

    assert "current_target" not in result["phases"]["execute"]
    assert "targets_done" not in result["phases"]["execute"]


def test_state_schema_rejects_targets_done_on_non_review_phase(
    tmp_path: Path, schemas_dir: Path
) -> None:
    payload = {
        "feature_id": "2026-05-04-demo",
        "tier": "standard",
        "current_phase": "execute",
        "phases": {
            "execute": {
                "status": "in_progress",
                "targets_done": ["plan"],
            },
        },
        "skipped": [],
        "deviations": [],
        "commits": [],
    }
    target = tmp_path / "state.json"

    with pytest.raises(state.StateError, match="schema"):
        state.write_state(target, payload, schema_path=schemas_dir / "state.schema.json")
