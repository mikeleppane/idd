"""Research-aware routing entries in tools.state next-phase tables."""

from __future__ import annotations

from typing import Any

from tools import state


def _state(tier: str, phase: str) -> dict[str, Any]:
    return {
        "feature_id": "2026-05-11-demo",
        "tier": tier,
        "current_phase": phase,
        "phases": {phase: {"status": "in_progress"}},
        "skipped": [],
        "deviations": [],
        "commits": [],
    }


def test_full_tier_refine_routes_to_research() -> None:
    assert state.next_phase_command(_state("full", "refine")) == "/forge:research"


def test_full_tier_research_routes_to_spec() -> None:
    assert state.next_phase_command(_state("full", "research")) == "/forge:spec"


def test_standard_tier_research_routes_to_spec() -> None:
    """Standard tier reaches research only when /forge:do --standard --research
    seeds routing.phase_list with research at index 0; once the feature lands
    in current_phase="research", the static table advances it to spec."""
    assert state.next_phase_command(_state("standard", "research")) == "/forge:spec"


def test_focused_tier_does_not_advance_research() -> None:
    """Focused tier never enters research; the focused next-table lacks the
    entry, so a (hypothetical, malformed) focused-tier research state has
    no next command."""
    payload = _state("focused", "research")
    assert state.next_phase_command(payload) is None


def test_derive_phase_list_full_includes_research_pre_v3() -> None:
    phases = state.derive_phase_list(tier="full", flow_version=1)
    assert "research" in phases
    assert phases.index("research") == phases.index("refine") + 1
    assert phases.index("research") == phases.index("spec") - 1


def test_derive_phase_list_full_includes_research_v3() -> None:
    phases = state.derive_phase_list(tier="full", flow_version=3)
    assert "research" in phases
    assert phases[-1] == "qa"


def test_derive_phase_list_standard_excludes_research() -> None:
    """Standard-tier lazy-derive ships the 8-phase shape without research;
    the --research opt-in writes routing.phase_list explicitly to insert it."""
    phases = state.derive_phase_list(tier="standard")
    assert "research" not in phases
    assert phases[0] == "spec"


def test_derive_phase_list_focused_excludes_research() -> None:
    phases = state.derive_phase_list(tier="focused")
    assert "research" not in phases
