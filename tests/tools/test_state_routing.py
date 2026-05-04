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


def test_record_refined_idea_rejects_empty_string(tmp_path: Path, schemas_dir: Path) -> None:
    target = tmp_path / "state.json"
    state.write_state(target, _base_payload(), schema_path=schemas_dir / "state.schema.json")

    with pytest.raises(state.StateError, match="non-empty"):
        state.record_refined_idea(
            target,
            refined="",
            schema_path=schemas_dir / "state.schema.json",
        )
