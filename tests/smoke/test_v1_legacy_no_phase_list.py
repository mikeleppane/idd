"""Capstone smoke test for legacy v1 features predating ``routing.phase_list``.

Pre-research-routing features carried no ``routing.phase_list`` field at
all — the old standard-tier flow was implicit in ``_STANDARD_NEXT`` and
the legacy ``skipped[research]`` marker recorded that research had been
deferred at the milestone level.  Those features must keep walking to
ship via the static next-phase tables WITHOUT auto-migration: the
``routing.phase_list`` field stays absent (the schema makes it optional
exactly so legacy files validate), and ``next_phase_command`` consults
``_STANDARD_NEXT`` directly.

Pins:

  * A legacy v1 standard-tier payload (no ``routing.phase_list``,
    ``skipped`` carries the M3-era research deferral) validates against
    the live schema unchanged.
  * :func:`tools.state.get_phase_list` lazy-derives the canonical
    8-entry standard list — NO ``research`` slot, since the legacy
    ``skipped[research]`` marker precludes it and the standard table
    starts at ``spec``.
  * Every transition through ``spec → scenarios → plan → crucible →
    review (plan) → execute → review (code) → verify → ship → qa``
    yields the documented slash literal via :func:`next_phase_command`,
    and no transition surfaces ``/forge:research`` at any boundary.
  * The legacy file reaches the terminal-None boundary (``current_phase
    == "done"``) without anyone ever writing ``routing.phase_list``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tests.smoke.test_full_tier_walk_with_research import _walk_post_ship_to_done
from tools.state import (
    complete_phase,
    complete_review_target,
    get_phase_list,
    next_phase_command,
    read_state,
    set_review_target,
    start_phase,
    write_state,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "schemas" / "state.schema.json"


def _advance(state_path: Path, *, done: str, nxt: str, expected_next_command: str) -> None:
    """Complete ``done``, start ``nxt``, assert ``next_phase_command`` matches."""
    complete_phase(state_path, done, schema_path=SCHEMA_PATH)
    start_phase(state_path, nxt, schema_path=SCHEMA_PATH)
    payload = read_state(state_path, schema_path=SCHEMA_PATH)
    assert payload["current_phase"] == nxt
    assert next_phase_command(payload) == expected_next_command


def _walk_review_target(state_path: Path, *, target: str) -> None:
    """Open the review slot, set + complete the given target."""
    set_review_target(state_path, review_target=target, schema_path=SCHEMA_PATH)
    complete_review_target(state_path, review_target=target, schema_path=SCHEMA_PATH)


def test_v1_legacy_feature_resumes_without_phase_list(tmp_path: Path) -> None:
    """Legacy v1 standard feature resumes via the static next-phase tables.

    The on-disk payload omits ``routing.phase_list`` and carries the
    M3-era ``skipped[research]`` marker.  ``next_phase_command`` consults
    ``_STANDARD_NEXT`` directly; ``get_phase_list`` lazy-derives the
    no-research standard list.  No auto-migration writes
    ``routing.phase_list`` mid-walk.
    """
    feature_id = "2026-04-15-legacy"
    feature_dir = tmp_path / ".forge" / "features" / feature_id
    feature_dir.mkdir(parents=True)
    state_path = feature_dir / "state.json"

    payload: dict[str, Any] = {
        "feature_id": feature_id,
        "tier": "standard",
        "flow_version": 1,
        "current_phase": "spec",
        "phases": {
            "spec": {"status": "in_progress", "started_at": "2026-04-15T12:00:00Z"},
        },
        "skipped": [
            # Legacy on-disk shape: this v1 fixture predates the neutral
            # skipped-reason swap, so the literal text is preserved
            # verbatim to keep the backward-compat assertion honest.
            # Newer features write the neutral string from
            # ``tools.archive._RESEARCH_SKIPPED_ENTRY`` instead.
            {"phase": "research", "reason": "M3 deferred — manual research acceptable"},
        ],
        "deviations": [],
        "commits": [],
        "routing": {
            "idea": "legacy feature",
            "final_tier": "standard",
            "decided_at": "2026-04-15T12:00:00Z",
            "constitution_present": False,
            # NOTE: no phase_list field — legacy v1 features predate the
            # research-routing wire-up entirely.
        },
    }
    write_state(state_path, payload, schema_path=SCHEMA_PATH)

    # ------------------------------------------------------------------
    # Legacy payload validates and lazy-derives the standard 8-phase list.
    # ------------------------------------------------------------------
    payload = read_state(state_path, schema_path=SCHEMA_PATH)
    assert "phase_list" not in payload["routing"], (
        "legacy v1 feature must NOT carry routing.phase_list — auto-migration is forbidden"
    )

    derived = get_phase_list(payload)
    assert derived is not None  # narrows for type-checker; lazy-derive is non-None for valid tier
    assert derived == [
        "spec",
        "scenarios",
        "plan",
        "crucible",
        "review",
        "execute",
        "verify",
        "ship",
    ]
    # No research in the lazy-derived list — the standard table omits it
    # (research is opt-in via /forge:do --research, which writes an
    # explicit phase_list rather than relying on lazy-derive).
    assert "research" not in derived
    # The legacy skipped[research] marker is preserved (no migration).
    assert any(s.get("phase") == "research" for s in payload["skipped"])

    # ------------------------------------------------------------------
    # Spec entry: standard table -> /forge:scenarios.
    # ------------------------------------------------------------------
    assert payload["current_phase"] == "spec"
    assert next_phase_command(payload) == "/forge:scenarios"

    # ------------------------------------------------------------------
    # Walk standard tier through to ship via the static tables.
    # No transition may surface /forge:research.
    # ------------------------------------------------------------------
    _advance(state_path, done="spec", nxt="scenarios", expected_next_command="/forge:plan")
    _advance(state_path, done="scenarios", nxt="plan", expected_next_command="/forge:crucible")
    _advance(
        state_path,
        done="plan",
        nxt="crucible",
        expected_next_command="/forge:review --target plan",
    )

    # Crucible -> Review (target=plan): dual-target review pass.
    complete_phase(state_path, "crucible", schema_path=SCHEMA_PATH)
    start_phase(state_path, "review", schema_path=SCHEMA_PATH)
    _walk_review_target(state_path, target="plan")
    payload = read_state(state_path, schema_path=SCHEMA_PATH)
    assert payload["current_phase"] == "review"
    # plan-pass: _next_review_command -> /forge:execute (plan done, code pending).
    assert next_phase_command(payload) == "/forge:execute"

    # Review (plan) -> Execute. start_phase pivots without completing review.
    start_phase(state_path, "execute", schema_path=SCHEMA_PATH)
    payload = read_state(state_path, schema_path=SCHEMA_PATH)
    assert payload["current_phase"] == "execute"
    assert next_phase_command(payload) == "/forge:review --target code"

    # Execute -> Review (target=code).
    complete_phase(state_path, "execute", schema_path=SCHEMA_PATH)
    start_phase(state_path, "review", schema_path=SCHEMA_PATH)
    _walk_review_target(state_path, target="code")
    payload = read_state(state_path, schema_path=SCHEMA_PATH)
    assert next_phase_command(payload) == "/forge:verify"

    # Review (code) -> Verify -> Ship.
    _advance(state_path, done="review", nxt="verify", expected_next_command="/forge:ship")
    _advance(
        state_path,
        done="verify",
        nxt="ship",
        expected_next_command="/forge:qa --against merged",
    )

    # Ship completes; migrate to v3 to seed the qa phase, then walk the
    # terminal-None boundary. routing.phase_list MUST still be absent
    # (the migration only seeds phases.qa + flow_version, it does NOT
    # backfill the explicit phase_list — legacy lazy-derive remains the
    # source of truth for sequencing).
    complete_phase(state_path, "ship", schema_path=SCHEMA_PATH)
    payload = _walk_post_ship_to_done(tmp_path, state_path, feature_id=feature_id)
    assert "phase_list" not in payload["routing"], (
        "migrate_to_v3 must NOT backfill routing.phase_list on legacy features"
    )
