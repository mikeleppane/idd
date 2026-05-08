"""Tests for next_phase_command static dispatch in tools.state."""

from __future__ import annotations

from typing import Any

import pytest

from tools import state


def _state(tier: str, phase: str, **review_extra: object) -> dict[str, Any]:
    review_block: dict[str, Any] = {"status": "in_progress"}
    review_block.update(review_extra)
    phases: dict[str, Any] = (
        {phase: review_block} if phase == "review" else {phase: {"status": "in_progress"}}
    )
    return {
        "feature_id": "2026-05-04-demo",
        "tier": tier,
        "current_phase": phase,
        "phases": phases,
        "skipped": [],
        "deviations": [],
        "commits": [],
    }


@pytest.mark.parametrize(
    "phase,expected",
    [
        ("spec", "/forge:execute"),
        ("execute", "/forge:verify"),
        ("verify", None),
    ],
)
def test_next_phase_focused_tier(phase: str, expected: str | None) -> None:
    assert state.next_phase_command(_state("focused", phase)) == expected


@pytest.mark.parametrize(
    "phase,expected",
    [
        # 'refine' is full-tier only; standard tier never enters refine
        # so it is intentionally absent from the standard-tier next map
        # (deep-M-A2). See test_standard_next_does_not_route_through_refine.
        ("spec", "/forge:scenarios"),
        ("scenarios", "/forge:plan"),
        ("plan", "/forge:crucible"),
        ("crucible", "/forge:review --target plan"),
        ("execute", "/forge:review --target code"),
        ("verify", "/forge:ship"),
        ("ship", None),
    ],
)
def test_next_phase_standard_tier(phase: str, expected: str | None) -> None:
    assert state.next_phase_command(_state("standard", phase)) == expected


def test_next_phase_standard_tier_refine_returns_none() -> None:
    """Refine is full-tier only; querying it on standard-tier yields no next.

    Pinning this explicitly makes the deep-M-A2 cleanup self-evident in the
    test surface: the misroute that previously claimed standard tier could
    advance refine -> /forge:spec is gone.
    """
    assert state.next_phase_command(_state("standard", "refine")) is None


def test_next_phase_full_tier_inserts_domain_after_spec() -> None:
    assert state.next_phase_command(_state("full", "spec")) == "/forge:domain"


def test_next_phase_full_tier_domain_routes_to_scenarios() -> None:
    assert state.next_phase_command(_state("full", "domain")) == "/forge:scenarios"


def test_next_phase_review_routes_to_plan_when_no_targets_done() -> None:
    payload = _state("standard", "review", targets_done=[], current_target="plan")
    assert state.next_phase_command(payload) == "/forge:review --target plan"


def test_next_phase_review_routes_to_execute_when_plan_done() -> None:
    payload = _state("standard", "review", targets_done=["plan"], current_target="plan")
    assert state.next_phase_command(payload) == "/forge:execute"


def test_next_phase_review_routes_to_verify_when_both_done() -> None:
    payload = _state("standard", "review", targets_done=["plan", "code"], current_target="code")
    assert state.next_phase_command(payload) == "/forge:verify"


def test_next_phase_unknown_tier_returns_none() -> None:
    payload = _state("focused", "spec")
    payload["tier"] = "exotic"
    assert state.next_phase_command(payload) is None
