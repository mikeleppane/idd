"""Tests for the M3 routing + refined_idea fields and helpers in tools.state."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tools import state


def _base_payload(feature_id: str = "2026-05-04-demo") -> dict[str, Any]:
    return {
        "feature_id": feature_id,
        "tier": "standard",
        "current_phase": "refine",
        "phases": {"refine": {"status": "in_progress", "started_at": "2026-05-04T10:00:00Z"}},
        "skipped": [],
        "deviations": [],
        "commits": [],
    }


def test_state_schema_accepts_routing_block(tmp_path: Path, schemas_dir: Path) -> None:
    payload = _base_payload()
    payload["routing"] = {
        "idea": "Add coupon redemption to checkout",
        "proposed_tier": "standard",
        "final_tier": "standard",
        "rationale": "Cross-cutting payment surface; standard tier appropriate",
        "constitution_present": False,
        "decided_at": "2026-05-04T09:55:00Z",
    }
    target = tmp_path / "state.json"

    state.write_state(target, payload, schema_path=schemas_dir / "state.schema.json")

    assert json.loads(target.read_text(encoding="utf-8"))["routing"]["final_tier"] == "standard"


def test_state_schema_accepts_payload_without_routing(tmp_path: Path, schemas_dir: Path) -> None:
    target = tmp_path / "state.json"

    state.write_state(target, _base_payload(), schema_path=schemas_dir / "state.schema.json")

    assert "routing" not in json.loads(target.read_text(encoding="utf-8"))


def test_state_schema_accepts_refined_idea(tmp_path: Path, schemas_dir: Path) -> None:
    payload = _base_payload()
    payload["refined_idea"] = (
        "Allow checkout to apply a single stackable coupon. Coupon code is validated "
        "against a cached registry; redemption is logged with the order id but never "
        "with the raw code."
    )
    target = tmp_path / "state.json"

    state.write_state(target, payload, schema_path=schemas_dir / "state.schema.json")

    assert json.loads(target.read_text(encoding="utf-8"))["refined_idea"].startswith("Allow")


def test_state_schema_rejects_non_string_refined_idea(tmp_path: Path, schemas_dir: Path) -> None:
    payload = _base_payload()
    payload["refined_idea"] = {"not": "a string"}
    target = tmp_path / "state.json"

    with pytest.raises(state.StateError, match="schema"):
        state.write_state(target, payload, schema_path=schemas_dir / "state.schema.json")

    assert not target.exists()


def test_record_routing_decision_writes_block(tmp_path: Path, schemas_dir: Path) -> None:
    target = tmp_path / "state.json"
    state.write_state(target, _base_payload(), schema_path=schemas_dir / "state.schema.json")

    result = state.record_routing_decision(
        target,
        idea="Add coupon redemption",
        proposed_tier="standard",
        final_tier="standard",
        rationale="Cross-cutting checkout change",
        constitution_present=False,
        schema_path=schemas_dir / "state.schema.json",
        now="2026-05-04T09:55:00Z",
    )

    assert result["routing"]["idea"] == "Add coupon redemption"
    assert result["routing"]["final_tier"] == "standard"
    assert result["routing"]["decided_at"] == "2026-05-04T09:55:00Z"
    assert result["routing"]["constitution_present"] is False


def test_record_routing_decision_overwrites_existing_block(
    tmp_path: Path, schemas_dir: Path
) -> None:
    target = tmp_path / "state.json"
    initial = _base_payload()
    initial["routing"] = {
        "idea": "old idea",
        "proposed_tier": "focused",
        "final_tier": "focused",
        "rationale": "stale",
        "constitution_present": False,
        "decided_at": "2026-05-04T08:00:00Z",
    }
    state.write_state(target, initial, schema_path=schemas_dir / "state.schema.json")

    result = state.record_routing_decision(
        target,
        idea="new idea",
        proposed_tier="standard",
        final_tier="full",
        rationale="user override to full",
        constitution_present=True,
        schema_path=schemas_dir / "state.schema.json",
        now="2026-05-04T09:55:00Z",
    )

    assert result["routing"]["idea"] == "new idea"
    assert result["routing"]["final_tier"] == "full"
    assert result["routing"]["constitution_present"] is True


def test_record_refined_idea_writes_string(tmp_path: Path, schemas_dir: Path) -> None:
    target = tmp_path / "state.json"
    state.write_state(target, _base_payload(), schema_path=schemas_dir / "state.schema.json")

    result = state.record_refined_idea(
        target,
        refined="Allow checkout to apply a single stackable coupon.",
        schema_path=schemas_dir / "state.schema.json",
    )

    assert result["refined_idea"] == "Allow checkout to apply a single stackable coupon."


def test_record_refined_idea_overwrites_previous(tmp_path: Path, schemas_dir: Path) -> None:
    target = tmp_path / "state.json"
    initial = _base_payload()
    initial["refined_idea"] = "old draft"
    state.write_state(target, initial, schema_path=schemas_dir / "state.schema.json")

    result = state.record_refined_idea(
        target,
        refined="new draft",
        schema_path=schemas_dir / "state.schema.json",
    )

    assert result["refined_idea"] == "new draft"


def test_record_routing_decision_rejects_unknown_final_tier_without_schema(
    tmp_path: Path,
) -> None:
    target = tmp_path / "state.json"
    state.write_state(target, _base_payload())  # no schema_path

    with pytest.raises(state.StateError, match="invalid final_tier"):
        state.record_routing_decision(
            target,
            idea="x",
            final_tier="exotic",
        )


def test_record_routing_decision_rejects_unknown_proposed_tier_without_schema(
    tmp_path: Path,
) -> None:
    target = tmp_path / "state.json"
    state.write_state(target, _base_payload())

    with pytest.raises(state.StateError, match="invalid proposed_tier"):
        state.record_routing_decision(
            target,
            idea="x",
            proposed_tier="exotic",
            final_tier="standard",
        )


def test_record_refined_idea_rejects_empty_string(tmp_path: Path, schemas_dir: Path) -> None:
    target = tmp_path / "state.json"
    state.write_state(target, _base_payload(), schema_path=schemas_dir / "state.schema.json")

    with pytest.raises(state.StateError, match="non-empty"):
        state.record_refined_idea(
            target,
            refined="",
            schema_path=schemas_dir / "state.schema.json",
        )


def test_record_refined_idea_raises_when_phase_not_refine(
    tmp_path: Path, schemas_dir: Path
) -> None:
    target = tmp_path / "state.json"
    payload = _base_payload()
    payload["current_phase"] = "spec"
    payload["phases"] = {"spec": {"status": "in_progress", "started_at": "2026-05-04T10:00:00Z"}}
    state.write_state(target, payload, schema_path=schemas_dir / "state.schema.json")

    with pytest.raises(state.StateError) as excinfo:
        state.record_refined_idea(
            target,
            refined="Allow checkout to apply a single stackable coupon.",
            schema_path=schemas_dir / "state.schema.json",
        )

    message = str(excinfo.value)
    assert "'refine'" in message
    assert "'spec'" in message


def test_record_refined_idea_succeeds_when_phase_refine(tmp_path: Path, schemas_dir: Path) -> None:
    target = tmp_path / "state.json"
    state.write_state(target, _base_payload(), schema_path=schemas_dir / "state.schema.json")

    result = state.record_refined_idea(
        target,
        refined="Allow checkout to apply a single stackable coupon.",
        schema_path=schemas_dir / "state.schema.json",
    )

    assert result["refined_idea"] == "Allow checkout to apply a single stackable coupon."


def test_record_refined_idea_raises_when_text_exceeds_cap(
    tmp_path: Path, schemas_dir: Path
) -> None:
    target = tmp_path / "state.json"
    state.write_state(target, _base_payload(), schema_path=schemas_dir / "state.schema.json")
    too_long = "x" * 4001

    with pytest.raises(state.StateError) as excinfo:
        state.record_refined_idea(
            target,
            refined=too_long,
            schema_path=schemas_dir / "state.schema.json",
        )

    message = str(excinfo.value)
    assert "4000" in message
    assert "4001" in message


def test_record_refined_idea_accepts_text_at_cap_boundary(
    tmp_path: Path, schemas_dir: Path
) -> None:
    target = tmp_path / "state.json"
    state.write_state(target, _base_payload(), schema_path=schemas_dir / "state.schema.json")
    at_cap = "y" * 4000

    result = state.record_refined_idea(
        target,
        refined=at_cap,
        schema_path=schemas_dir / "state.schema.json",
    )

    assert result["refined_idea"] == at_cap
    assert len(result["refined_idea"]) == 4000


def test_record_refined_idea_persists_no_writes_on_guard_failure(
    tmp_path: Path, schemas_dir: Path
) -> None:
    target = tmp_path / "state.json"
    payload = _base_payload()
    payload["current_phase"] = "spec"
    payload["phases"] = {"spec": {"status": "in_progress", "started_at": "2026-05-04T10:00:00Z"}}
    payload["refined_idea"] = "preexisting refined idea body"
    state.write_state(target, payload, schema_path=schemas_dir / "state.schema.json")

    with pytest.raises(state.StateError):
        state.record_refined_idea(
            target,
            refined="should never persist",
            schema_path=schemas_dir / "state.schema.json",
        )

    persisted = json.loads(target.read_text(encoding="utf-8"))
    assert persisted["refined_idea"] == "preexisting refined idea body"


def _routed_payload(feature_id: str = "2026-05-04-demo") -> dict[str, Any]:
    """Base payload with a valid routing block but no refine_attempts yet."""
    payload = _base_payload(feature_id)
    payload["routing"] = {
        "idea": "Add coupon redemption to checkout",
        "final_tier": "standard",
        "decided_at": "2026-05-04T09:55:00Z",
        "constitution_present": False,
    }
    return payload


def test_increment_refine_attempts_first_call_returns_one_preserves_siblings(
    tmp_path: Path, schemas_dir: Path
) -> None:
    target = tmp_path / "state.json"
    state.write_state(target, _routed_payload(), schema_path=schemas_dir / "state.schema.json")

    new_count = state.increment_refine_attempts(
        target,
        schema_path=schemas_dir / "state.schema.json",
    )

    assert new_count == 1
    persisted = json.loads(target.read_text(encoding="utf-8"))
    assert persisted["routing"]["refine_attempts"] == 1
    # Sibling fields preserved.
    assert persisted["routing"]["idea"] == "Add coupon redemption to checkout"
    assert persisted["routing"]["final_tier"] == "standard"
    assert persisted["routing"]["decided_at"] == "2026-05-04T09:55:00Z"
    assert persisted["routing"]["constitution_present"] is False


def test_increment_refine_attempts_second_call_returns_two(
    tmp_path: Path, schemas_dir: Path
) -> None:
    target = tmp_path / "state.json"
    payload = _routed_payload()
    payload["routing"]["refine_attempts"] = 1
    state.write_state(target, payload, schema_path=schemas_dir / "state.schema.json")

    new_count = state.increment_refine_attempts(
        target,
        schema_path=schemas_dir / "state.schema.json",
    )

    assert new_count == 2
    persisted = json.loads(target.read_text(encoding="utf-8"))
    assert persisted["routing"]["refine_attempts"] == 2


def test_increment_refine_attempts_third_call_returns_three(
    tmp_path: Path, schemas_dir: Path
) -> None:
    target = tmp_path / "state.json"
    payload = _routed_payload()
    payload["routing"]["refine_attempts"] = 2
    state.write_state(target, payload, schema_path=schemas_dir / "state.schema.json")

    new_count = state.increment_refine_attempts(
        target,
        schema_path=schemas_dir / "state.schema.json",
    )

    assert new_count == 3
    persisted = json.loads(target.read_text(encoding="utf-8"))
    assert persisted["routing"]["refine_attempts"] == 3


def test_increment_refine_attempts_raises_when_current_phase_not_refine(
    tmp_path: Path, schemas_dir: Path
) -> None:
    target = tmp_path / "state.json"
    payload = _routed_payload()
    payload["current_phase"] = "spec"
    payload["phases"] = {"spec": {"status": "in_progress", "started_at": "2026-05-04T10:00:00Z"}}
    state.write_state(target, payload, schema_path=schemas_dir / "state.schema.json")

    with pytest.raises(state.StateError, match="current_phase"):
        state.increment_refine_attempts(
            target,
            schema_path=schemas_dir / "state.schema.json",
        )


def test_increment_refine_attempts_persists_across_reads(tmp_path: Path, schemas_dir: Path) -> None:
    target = tmp_path / "state.json"
    state.write_state(target, _routed_payload(), schema_path=schemas_dir / "state.schema.json")

    state.increment_refine_attempts(target, schema_path=schemas_dir / "state.schema.json")
    state.increment_refine_attempts(target, schema_path=schemas_dir / "state.schema.json")
    final = state.increment_refine_attempts(target, schema_path=schemas_dir / "state.schema.json")

    assert final == 3
    reread = state.read_state(target, schema_path=schemas_dir / "state.schema.json")
    assert reread["routing"]["refine_attempts"] == 3


def test_increment_refine_attempts_validates_against_schema(
    tmp_path: Path, schemas_dir: Path
) -> None:
    """Resulting payload must still pass schema validation."""
    target = tmp_path / "state.json"
    state.write_state(target, _routed_payload(), schema_path=schemas_dir / "state.schema.json")

    state.increment_refine_attempts(target, schema_path=schemas_dir / "state.schema.json")

    # If the schema didn't pass on write, write_state would have raised.
    # Re-validate explicitly to lock the contract.
    state.read_state(target, schema_path=schemas_dir / "state.schema.json")


def test_increment_refine_attempts_raises_when_routing_block_absent(
    tmp_path: Path, schemas_dir: Path
) -> None:
    target = tmp_path / "state.json"
    # Base payload has current_phase=refine but NO routing block.
    state.write_state(target, _base_payload(), schema_path=schemas_dir / "state.schema.json")

    with pytest.raises(state.StateError) as excinfo:
        state.increment_refine_attempts(
            target,
            schema_path=schemas_dir / "state.schema.json",
        )

    message = str(excinfo.value)
    assert "routing" in message
    assert "/forge:do" in message
    # No partial write — file must remain unchanged (no routing block).
    persisted = json.loads(target.read_text(encoding="utf-8"))
    assert "routing" not in persisted
