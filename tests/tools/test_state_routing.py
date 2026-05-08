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
    """Base payload with a valid routing block but no refine_attempts yet.

    Refine is full-tier only — `tools.state.increment_refine_attempts`
    enforces ``tier == "full"``. Use full tier here so the helper
    guard does not preempt the behavior under test.
    """
    payload = _base_payload(feature_id)
    payload["tier"] = "full"
    payload["routing"] = {
        "idea": "Add coupon redemption to checkout",
        "final_tier": "full",
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
    assert persisted["routing"]["final_tier"] == "full"
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
    # Base payload has current_phase=refine but NO routing block. Tier
    # must be 'full' so the helper's tier guard does not preempt the
    # routing-absent check this test pins.
    payload = _base_payload()
    payload["tier"] = "full"
    state.write_state(target, payload, schema_path=schemas_dir / "state.schema.json")

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


def test_increment_refine_attempts_rejects_string_count_without_schema(
    tmp_path: Path,
) -> None:
    """Without schema_path, a tampered string must surface as StateError, not ValueError."""
    target = tmp_path / "state.json"
    payload = _routed_payload()
    payload["routing"]["refine_attempts"] = "4"
    state.write_state(target, payload)  # no schema_path → no validation gate

    with pytest.raises(state.StateError) as excinfo:
        state.increment_refine_attempts(target)  # no schema_path

    message = str(excinfo.value)
    assert "refine_attempts" in message
    assert "int" in message
    assert "str" in message
    # No write happened — file body unchanged.
    persisted = json.loads(target.read_text(encoding="utf-8"))
    assert persisted["routing"]["refine_attempts"] == "4"


def test_increment_refine_attempts_rejects_garbage_string_without_schema(
    tmp_path: Path,
) -> None:
    """A non-numeric string must NOT bubble ValueError from int(); helper owns the contract."""
    target = tmp_path / "state.json"
    payload = _routed_payload()
    payload["routing"]["refine_attempts"] = "abc"
    state.write_state(target, payload)

    with pytest.raises(state.StateError, match="int"):
        state.increment_refine_attempts(target)


def test_increment_refine_attempts_rejects_bool_without_schema(tmp_path: Path) -> None:
    """`bool` is a Python `int` subclass — explicit reject so True+1==2 cannot leak."""
    target = tmp_path / "state.json"
    payload = _routed_payload()
    payload["routing"]["refine_attempts"] = True
    state.write_state(target, payload)

    with pytest.raises(state.StateError) as excinfo:
        state.increment_refine_attempts(target)

    message = str(excinfo.value)
    assert "bool" in message
    assert "int" in message


def test_increment_refine_attempts_rejects_negative_without_schema(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    payload = _routed_payload()
    payload["routing"]["refine_attempts"] = -1
    state.write_state(target, payload)

    with pytest.raises(state.StateError, match="negative"):
        state.increment_refine_attempts(target)


def test_state_schema_rejects_refined_idea_over_cap(tmp_path: Path, schemas_dir: Path) -> None:
    """Schema-level maxLength must mirror the helper's _REFINED_IDEA_MAX_CHARS cap."""
    payload = _base_payload()
    payload["refined_idea"] = "z" * 4001
    target = tmp_path / "state.json"

    with pytest.raises(state.StateError, match="schema"):
        state.write_state(target, payload, schema_path=schemas_dir / "state.schema.json")

    assert not target.exists()


def test_state_schema_accepts_refined_idea_at_cap(tmp_path: Path, schemas_dir: Path) -> None:
    payload = _base_payload()
    payload["refined_idea"] = "z" * 4000
    target = tmp_path / "state.json"

    state.write_state(target, payload, schema_path=schemas_dir / "state.schema.json")

    assert len(json.loads(target.read_text(encoding="utf-8"))["refined_idea"]) == 4000


# ---------------------------------------------------------------------------
# Refine-attempts cap (deep-M-A1)
# ---------------------------------------------------------------------------


def test_increment_refine_attempts_raises_at_cap(tmp_path: Path, schemas_dir: Path) -> None:
    """The 5-round prose cap must be machine-enforced by the helper."""
    target = tmp_path / "state.json"
    payload = _routed_payload()
    payload["routing"]["refine_attempts"] = 5
    state.write_state(target, payload, schema_path=schemas_dir / "state.schema.json")

    with pytest.raises(state.StateError) as excinfo:
        state.increment_refine_attempts(
            target,
            schema_path=schemas_dir / "state.schema.json",
        )

    message = str(excinfo.value)
    assert "cap" in message
    assert "5" in message
    # No write happened — the count must remain at 5, not climb to 6.
    persisted = json.loads(target.read_text(encoding="utf-8"))
    assert persisted["routing"]["refine_attempts"] == 5


def test_increment_refine_attempts_succeeds_at_cap_minus_one(
    tmp_path: Path, schemas_dir: Path
) -> None:
    """Boundary: the fifth call (current=4 -> 5) is the last legal one."""
    target = tmp_path / "state.json"
    payload = _routed_payload()
    payload["routing"]["refine_attempts"] = 4
    state.write_state(target, payload, schema_path=schemas_dir / "state.schema.json")

    new_count = state.increment_refine_attempts(
        target,
        schema_path=schemas_dir / "state.schema.json",
    )

    assert new_count == 5


def test_schema_rejects_refine_attempts_over_cap(tmp_path: Path, schemas_dir: Path) -> None:
    """Schema-level maximum must mirror the helper cap so tampered state.json is rejected."""
    payload = _base_payload()
    payload["routing"] = {
        "idea": "x",
        "final_tier": "full",
        "decided_at": "2026-05-04T09:55:00Z",
        "refine_attempts": 6,
    }
    target = tmp_path / "state.json"

    with pytest.raises(state.StateError) as excinfo:
        state.write_state(target, payload, schema_path=schemas_dir / "state.schema.json")

    message = str(excinfo.value)
    assert "5" in message or "maximum" in message
    assert not target.exists()


# ---------------------------------------------------------------------------
# Tier guard (deep-M-A2)
# ---------------------------------------------------------------------------


def test_increment_refine_attempts_raises_when_tier_not_full(
    tmp_path: Path, schemas_dir: Path
) -> None:
    """Refine is full-tier only; standard/focused tiers must trip the helper guard."""
    target = tmp_path / "state.json"
    payload = _routed_payload()
    payload["tier"] = "standard"
    payload["routing"]["final_tier"] = "standard"
    state.write_state(target, payload, schema_path=schemas_dir / "state.schema.json")

    with pytest.raises(state.StateError) as excinfo:
        state.increment_refine_attempts(
            target,
            schema_path=schemas_dir / "state.schema.json",
        )

    message = str(excinfo.value)
    assert "'standard'" in message
    assert "full" in message


def test_increment_refine_attempts_succeeds_for_full_tier(
    tmp_path: Path, schemas_dir: Path
) -> None:
    """Sanity: the canonical full-tier happy path still works after the tier guard lands."""
    target = tmp_path / "state.json"
    state.write_state(target, _routed_payload(), schema_path=schemas_dir / "state.schema.json")

    assert (
        state.increment_refine_attempts(
            target,
            schema_path=schemas_dir / "state.schema.json",
        )
        == 1
    )


def test_standard_next_does_not_route_through_refine() -> None:
    """Standard tier was never supposed to enter refine; the map must not list it.

    Until deep-M-A2 lands, ``_STANDARD_NEXT['refine'] = '/forge:spec'`` was
    falsely advertising a standard-tier path through refine.
    """
    assert "refine" not in state._STANDARD_NEXT, (
        "_STANDARD_NEXT must not list 'refine' — refine is full-tier only"
    )


# ---------------------------------------------------------------------------
# Type-coercion guard (deep-L-A6)
# ---------------------------------------------------------------------------


def test_increment_refine_attempts_rejects_non_int_count(tmp_path: Path) -> None:
    """A schema-bypassed ``refine_attempts`` of type str must surface as StateError.

    Without ``schema_path`` (the validation gate) the helper itself owns
    the type contract. The pre-fix code coerced via ``int(current)`` which
    accepted the numeric string ``"3"`` silently and crashed on ``"abc"``;
    the post-fix code rejects every non-int up front.
    """
    target = tmp_path / "state.json"
    payload = _routed_payload()
    payload["routing"]["refine_attempts"] = "3"
    state.write_state(target, payload)  # no schema_path

    with pytest.raises(state.StateError) as excinfo:
        state.increment_refine_attempts(target)  # no schema_path

    message = str(excinfo.value)
    assert "int" in message
    assert "str" in message


# ---------------------------------------------------------------------------
# next_phase_command non-string fallback (deep-L-A1, ≥95% coverage gate)
# ---------------------------------------------------------------------------


def test_next_phase_command_returns_none_for_non_string_phase() -> None:
    """`next_phase_command` must guard against tampered payloads where
    ``current_phase`` or ``tier`` is not a string. The non-string fallback
    branch closes the last uncovered line in tools.state.py.
    """
    payload: dict[str, Any] = {
        "feature_id": "2026-05-04-demo",
        "tier": "standard",
        "current_phase": 42,  # non-string
        "phases": {},
        "skipped": [],
        "deviations": [],
        "commits": [],
    }
    assert state.next_phase_command(payload) is None


def test_next_phase_command_returns_none_for_non_string_tier() -> None:
    payload: dict[str, Any] = {
        "feature_id": "2026-05-04-demo",
        "tier": None,  # non-string
        "current_phase": "spec",
        "phases": {"spec": {"status": "in_progress"}},
        "skipped": [],
        "deviations": [],
        "commits": [],
    }
    assert state.next_phase_command(payload) is None
