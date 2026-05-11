"""Tests for the additive substrate fields on schemas/state.schema.json.

Locks the contract for two additions:

1. ``routing.phase_list`` — optional ordered list of phases the feature will
   execute. Set at routing time, lazy-derived for legacy features. Order
   matters; entries are unique; the empty list is rejected.
2. ``phases.research`` — explicit per-phase shape mirroring the generic phase
   object plus an optional boolean ``parallel_used`` flag. Without the explicit
   shape, a worker copying a half-spec could drop the required ``status`` field.

Both additions are additive: state.json files that omit them must still
validate.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest


def _validator_for(schemas_dir: Path) -> jsonschema.Draft202012Validator:
    schema = json.loads((schemas_dir / "state.schema.json").read_text(encoding="utf-8"))
    return jsonschema.Draft202012Validator(
        schema,
        format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER,
    )


def _base_state() -> dict[str, Any]:
    """Minimal valid state.json payload with a routing block."""
    return {
        "feature_id": "2026-05-09-substrate-demo",
        "tier": "standard",
        "current_phase": "spec",
        "phases": {"spec": {"status": "in_progress"}},
        "skipped": [],
        "deviations": [],
        "commits": [],
        "routing": {
            "idea": "ship a thing",
            "final_tier": "standard",
            "decided_at": "2026-05-09T10:00:00Z",
        },
    }


def test_state_without_routing_phase_list_validates(schemas_dir: Path) -> None:
    payload = _base_state()
    assert "phase_list" not in payload["routing"]
    validator = _validator_for(schemas_dir)
    errors = list(validator.iter_errors(payload))
    assert errors == [], f"absent phase_list must validate: {errors}"


def test_state_with_valid_routing_phase_list_validates(schemas_dir: Path) -> None:
    payload = _base_state()
    payload["routing"]["phase_list"] = ["spec", "execute", "verify"]
    validator = _validator_for(schemas_dir)
    errors = list(validator.iter_errors(payload))
    assert errors == [], f"valid phase_list rejected: {errors}"


def test_state_with_duplicate_phase_list_entries_rejected(schemas_dir: Path) -> None:
    payload = _base_state()
    payload["routing"]["phase_list"] = ["spec", "spec", "verify"]
    validator = _validator_for(schemas_dir)
    errors = list(validator.iter_errors(payload))
    assert errors, "duplicate phase_list entries must be rejected by uniqueItems"


def test_state_with_empty_phase_list_rejected(schemas_dir: Path) -> None:
    payload = _base_state()
    payload["routing"]["phase_list"] = []
    validator = _validator_for(schemas_dir)
    errors = list(validator.iter_errors(payload))
    assert errors, "empty phase_list must be rejected by minItems: 1"


def test_state_with_unknown_phase_in_phase_list_rejected(schemas_dir: Path) -> None:
    payload = _base_state()
    payload["routing"]["phase_list"] = ["spec", "frobnicate", "verify"]
    validator = _validator_for(schemas_dir)
    errors = list(validator.iter_errors(payload))
    assert errors, "unknown phase string must be rejected by items.enum"


def test_phases_research_with_status_and_started_at_validates(schemas_dir: Path) -> None:
    payload = _base_state()
    payload["current_phase"] = "research"
    payload["phases"] = {
        "research": {
            "status": "in_progress",
            "started_at": "2026-05-09T10:00:00Z",
        }
    }
    validator = _validator_for(schemas_dir)
    errors = list(validator.iter_errors(payload))
    assert errors == [], f"minimal research phase rejected: {errors}"


def test_phases_research_missing_status_rejected(schemas_dir: Path) -> None:
    payload = _base_state()
    payload["current_phase"] = "research"
    payload["phases"] = {"research": {"started_at": "2026-05-09T10:00:00Z"}}
    validator = _validator_for(schemas_dir)
    errors = list(validator.iter_errors(payload))
    assert errors, "research phase missing required 'status' must be rejected"


def test_phases_research_parallel_used_string_rejected(schemas_dir: Path) -> None:
    payload = _base_state()
    payload["current_phase"] = "research"
    payload["phases"] = {
        "research": {
            "status": "in_progress",
            "parallel_used": "yes",
        }
    }
    validator = _validator_for(schemas_dir)
    errors = list(validator.iter_errors(payload))
    assert errors, "parallel_used must be a boolean, not a string"


def test_phases_research_parallel_used_true_validates(schemas_dir: Path) -> None:
    payload = _base_state()
    payload["current_phase"] = "research"
    payload["phases"] = {
        "research": {
            "status": "in_progress",
            "parallel_used": True,
        }
    }
    validator = _validator_for(schemas_dir)
    errors = list(validator.iter_errors(payload))
    assert errors == [], f"boolean parallel_used rejected: {errors}"


def test_routing_extra_property_still_rejected(schemas_dir: Path) -> None:
    """Adding phase_list must not loosen routing.additionalProperties: false."""
    payload = _base_state()
    payload["routing"]["bogus_field"] = "anything"
    validator = _validator_for(schemas_dir)
    errors = list(validator.iter_errors(payload))
    assert errors, "routing must continue to reject unknown properties"


@pytest.mark.parametrize(
    "phase",
    [
        "refine",
        "research",
        "spec",
        "domain",
        "scenarios",
        "plan",
        "crucible",
        "review",
        "execute",
        "verify",
        "ship",
        "qa",
    ],
)
def test_phase_list_accepts_every_canonical_phase(schemas_dir: Path, phase: str) -> None:
    payload = _base_state()
    payload["routing"]["phase_list"] = [phase]
    validator = _validator_for(schemas_dir)
    errors = list(validator.iter_errors(payload))
    assert errors == [], f"phase_list rejected canonical phase {phase!r}: {errors}"
