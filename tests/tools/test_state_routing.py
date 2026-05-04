"""Tests for the M3 routing + refined_idea fields and helpers in tools.state."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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
