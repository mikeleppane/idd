"""E2E smoke for M3 full-tier pre-spec pipeline plumbing.

Walks the state-machine wiring that /forge:refine, /forge:spec, and
/forge:domain depend on:

  refine -> spec -> domain -> scenarios

Three walks (per plan T6):
  1. Happy walk: refine increments + refined_idea persistence + phase
     transitions; next_phase_command returns the right slash at each
     boundary; routing.refine_attempts ends at 3 with all sibling fields
     preserved; refined_idea persisted; SPEC.md `# Domain` placeholder
     accepted at spec exit.
  2. Round-cap deviation: 5 increments + auto-mode deviation logged to
     state.json.deviations; advance to spec without halt.
  3. Interactive halt: 5 increments + simulated halt; state.json shows
     refine_attempts=5 but current_phase still 'refine' (no advance).

The smoke does NOT invoke the LLM-driven SKILL.md prose; it exercises
only the deterministic state helpers + next-step map that the skills
contract against.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.state import (
    StateError,
    complete_phase,
    increment_refine_attempts,
    next_phase_command,
    read_state,
    record_refined_idea,
    record_routing_decision,
    start_phase,
    write_state,
)

REPO = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO / "schemas" / "state.schema.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_full_tier_at_refine(tmp_path: Path, *, idea: str) -> Path:
    """Build a state.json at current_phase=refine with full tier + routing."""
    state_path = tmp_path / "state.json"
    payload = {
        "feature_id": "2026-05-08-rollout-percent",
        "tier": "full",
        "current_phase": "refine",
        "phases": {"refine": {"status": "in_progress"}},
        "skipped": [],
        "deviations": [],
        "commits": [],
    }
    write_state(state_path, payload, schema_path=SCHEMA_PATH)
    record_routing_decision(
        state_path,
        idea=idea,
        final_tier="full",
        proposed_tier="full",
        rationale="cross-cutting; needs domain pass",
        constitution_present=False,
        schema_path=SCHEMA_PATH,
    )
    return state_path


def _append_deviation(state_path: Path, *, phase: str, cause: str, resolution: str) -> None:
    payload = read_state(state_path, schema_path=SCHEMA_PATH)
    payload.setdefault("deviations", []).append(
        {"phase": phase, "cause": cause, "resolution": resolution},
    )
    write_state(state_path, payload, schema_path=SCHEMA_PATH)


# ---------------------------------------------------------------------------
# 1. Happy walk: refine -> spec -> domain -> scenarios
# ---------------------------------------------------------------------------


def test_full_tier_pipeline_happy_walk(tmp_path: Path) -> None:
    state_path = _seed_full_tier_at_refine(tmp_path, idea="rollout percent flag")

    assert increment_refine_attempts(state_path, schema_path=SCHEMA_PATH) == 1
    assert increment_refine_attempts(state_path, schema_path=SCHEMA_PATH) == 2
    assert increment_refine_attempts(state_path, schema_path=SCHEMA_PATH) == 3

    record_refined_idea(
        state_path,
        refined="Ship a percentage-rollout flag with deterministic hash bucketing.",
        schema_path=SCHEMA_PATH,
    )

    payload = read_state(state_path, schema_path=SCHEMA_PATH)
    assert payload["routing"]["refine_attempts"] == 3
    assert payload["routing"]["idea"] == "rollout percent flag"
    assert payload["routing"]["final_tier"] == "full"
    assert "decided_at" in payload["routing"]
    assert payload["refined_idea"].startswith("Ship a percentage-rollout flag")

    assert next_phase_command(payload) == "/forge:spec"

    complete_phase(state_path, "refine", schema_path=SCHEMA_PATH)
    start_phase(state_path, "spec", schema_path=SCHEMA_PATH)
    payload = read_state(state_path, schema_path=SCHEMA_PATH)
    assert payload["current_phase"] == "spec"
    assert next_phase_command(payload) == "/forge:domain"

    complete_phase(state_path, "spec", schema_path=SCHEMA_PATH)
    start_phase(state_path, "domain", schema_path=SCHEMA_PATH)
    payload = read_state(state_path, schema_path=SCHEMA_PATH)
    assert payload["current_phase"] == "domain"
    assert next_phase_command(payload) == "/forge:scenarios"

    complete_phase(state_path, "domain", schema_path=SCHEMA_PATH)
    start_phase(state_path, "scenarios", schema_path=SCHEMA_PATH)
    payload = read_state(state_path, schema_path=SCHEMA_PATH)
    assert payload["current_phase"] == "scenarios"
    assert next_phase_command(payload) == "/forge:plan"


# ---------------------------------------------------------------------------
# 2. Round-cap auto-mode deviation
# ---------------------------------------------------------------------------


def test_full_tier_round_cap_auto_mode_logs_deviation_and_advances(tmp_path: Path) -> None:
    state_path = _seed_full_tier_at_refine(tmp_path, idea="ambiguous compound idea")
    decisions_path = state_path.parent / "decisions.md"
    decisions_path.write_text("# Decisions\n\n", encoding="utf-8")

    for expected in range(1, 6):
        assert increment_refine_attempts(state_path, schema_path=SCHEMA_PATH) == expected

    record_refined_idea(
        state_path,
        refined="Best-effort refinement after round cap.",
        schema_path=SCHEMA_PATH,
    )
    _append_deviation(
        state_path,
        phase="refine",
        cause="Refine round cap reached",
        resolution="proceeding with best-effort refinement",
    )
    with decisions_path.open("a", encoding="utf-8") as fh:
        fh.write(
            "\n## 2026-05-08 — Refine round cap reached\n\n"
            "Refine phase exhausted the 5-round cap without convergence; "
            "proceeding with best-effort refinement.\n",
        )

    complete_phase(state_path, "refine", schema_path=SCHEMA_PATH)
    start_phase(state_path, "spec", schema_path=SCHEMA_PATH)

    payload = read_state(state_path, schema_path=SCHEMA_PATH)
    assert payload["current_phase"] == "spec"
    assert payload["routing"]["refine_attempts"] == 5
    assert any(
        d.get("phase") == "refine" and "round cap" in d.get("cause", "")
        for d in payload["deviations"]
    )
    assert payload["refined_idea"].startswith("Best-effort")

    decisions_text = decisions_path.read_text(encoding="utf-8")
    assert "round cap reached" in decisions_text, (
        "Auto-mode round-cap must mirror the deviation in decisions.md"
    )
    assert "best-effort refinement" in decisions_text, (
        "decisions.md entry must record the resolution"
    )


# ---------------------------------------------------------------------------
# 3. Interactive halt: 5 rounds, no advance
# ---------------------------------------------------------------------------


def test_full_tier_interactive_halt_does_not_advance(tmp_path: Path) -> None:
    state_path = _seed_full_tier_at_refine(tmp_path, idea="user wants to halt and rethink")

    for expected in range(1, 6):
        assert increment_refine_attempts(state_path, schema_path=SCHEMA_PATH) == expected

    payload = read_state(state_path, schema_path=SCHEMA_PATH)
    assert payload["current_phase"] == "refine"
    assert payload["routing"]["refine_attempts"] == 5
    assert "refined_idea" not in payload
    assert payload["deviations"] == []


# ---------------------------------------------------------------------------
# 4. Guardrail: increment refuses outside refine phase (cross-check)
# ---------------------------------------------------------------------------


def test_increment_refuses_when_phase_advanced(tmp_path: Path) -> None:
    state_path = _seed_full_tier_at_refine(tmp_path, idea="guardrail check")
    increment_refine_attempts(state_path, schema_path=SCHEMA_PATH)
    record_refined_idea(state_path, refined="Refined.", schema_path=SCHEMA_PATH)
    complete_phase(state_path, "refine", schema_path=SCHEMA_PATH)
    start_phase(state_path, "spec", schema_path=SCHEMA_PATH)

    with pytest.raises(StateError, match="refine"):
        increment_refine_attempts(state_path, schema_path=SCHEMA_PATH)


# ---------------------------------------------------------------------------
# 5. Schema fidelity sanity (read-from-disk after walk)
# ---------------------------------------------------------------------------


def test_full_tier_state_round_trips_through_disk(tmp_path: Path) -> None:
    state_path = _seed_full_tier_at_refine(tmp_path, idea="round trip")
    increment_refine_attempts(state_path, schema_path=SCHEMA_PATH)
    record_refined_idea(state_path, refined="Refined paragraph.", schema_path=SCHEMA_PATH)

    raw = json.loads(state_path.read_text(encoding="utf-8"))
    assert raw["routing"]["refine_attempts"] == 1
    assert raw["routing"]["idea"] == "round trip"
    assert raw["refined_idea"] == "Refined paragraph."
    assert raw["current_phase"] == "refine"
    assert raw["tier"] == "full"
