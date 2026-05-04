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
        ("spec", "/idd:execute"),
        ("execute", "/idd:verify"),
        ("verify", None),
    ],
)
def test_next_phase_focused_tier(phase: str, expected: str | None) -> None:
    assert state.next_phase_command(_state("focused", phase)) == expected


@pytest.mark.parametrize(
    "phase,expected",
    [
        ("refine", "/idd:spec"),
        ("spec", "/idd:scenarios"),
        ("scenarios", "/idd:plan"),
        ("plan", "/idd:crucible"),
        ("crucible", "/idd:review --target plan"),
        ("execute", "/idd:review --target code"),
        ("verify", "/idd:ship"),
        ("ship", None),
    ],
)
def test_next_phase_standard_tier(phase: str, expected: str | None) -> None:
    assert state.next_phase_command(_state("standard", phase)) == expected


def test_next_phase_full_tier_inserts_domain_after_spec() -> None:
    assert state.next_phase_command(_state("full", "spec")) == "/idd:domain"


def test_next_phase_full_tier_domain_routes_to_scenarios() -> None:
    assert state.next_phase_command(_state("full", "domain")) == "/idd:scenarios"


def test_next_phase_review_routes_to_plan_when_no_targets_done() -> None:
    payload = _state("standard", "review", targets_done=[], current_target="plan")
    assert state.next_phase_command(payload) == "/idd:review --target plan"


def test_next_phase_review_routes_to_execute_when_plan_done() -> None:
    payload = _state("standard", "review", targets_done=["plan"], current_target="plan")
    assert state.next_phase_command(payload) == "/idd:execute"


def test_next_phase_review_routes_to_verify_when_both_done() -> None:
    payload = _state("standard", "review", targets_done=["plan", "code"], current_target="code")
    assert state.next_phase_command(payload) == "/idd:verify"


def test_next_phase_unknown_tier_returns_none() -> None:
    payload = _state("focused", "spec")
    payload["tier"] = "exotic"
    assert state.next_phase_command(payload) is None
