"""Tests for the research-opt-in branch of ``seed_routed_feature``.

Standard tier opts into the research phase via ``research_opt_in=True``;
the helper then seeds ``current_phase="research"`` AND writes
``routing.phase_list`` with ``"research"`` at index 0. Full tier always
includes research now (the legacy M3 ``skipped[research]`` deferral entry
is suppressed when research is part of the effective phase list). Focused
tier with ``research_opt_in=True`` refuses BEFORE any disk mutation.

Coverage targets:

  * Standard ``--research`` seeds ``current_phase="research"`` and the
    9-entry ``routing.phase_list`` with ``research`` at index 0.
  * Standard without ``--research`` keeps ``current_phase="spec"`` and
    omits ``routing.phase_list`` (legacy lazy-derive path).
  * Full tier (with or without the flag) seeds ``current_phase="refine"``
    and writes the 11-entry full-tier ``routing.phase_list`` incl.
    ``research``; the legacy skipped entry is suppressed.
  * Focused with ``research_opt_in=True`` raises ``ValueError`` with the
    locked spec wording; no folder is created.
  * Focused without ``--research`` is unchanged (current_phase="spec",
    skipped[research] still present per legacy behavior).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from tools.routing import seed_routed_feature

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "schemas" / "state.schema.json"

# Pinned today so feature-id assertions are stable across CI clocks.
TODAY = date(2026, 5, 11)


def _stage_repo(tmp_path: Path) -> Path:
    schema_dir = tmp_path / "schemas"
    schema_dir.mkdir()
    (schema_dir / "state.schema.json").write_text(
        SCHEMA_PATH.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return tmp_path


def _read_state(folder: Path) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads((folder / "state.json").read_text(encoding="utf-8"))
    return payload


# ---------------------------------------------------------------------------
# Standard tier with --research
# ---------------------------------------------------------------------------


def test_standard_research_opt_in_seeds_current_phase_research(tmp_path: Path) -> None:
    """Standard ``--research`` enters the research phase directly."""
    repo = _stage_repo(tmp_path)

    folder = seed_routed_feature(
        repo,
        idea="standard with research opt-in",
        final_tier="standard",
        research_opt_in=True,
        today=TODAY,
    )

    payload = _read_state(folder)
    assert payload["current_phase"] == "research"
    assert payload["phases"]["research"]["status"] == "in_progress"
    assert "started_at" in payload["phases"]["research"]


def test_standard_research_opt_in_writes_phase_list_with_research_first(tmp_path: Path) -> None:
    """Standard ``--research`` writes a 9-entry phase_list starting with research."""
    repo = _stage_repo(tmp_path)

    folder = seed_routed_feature(
        repo,
        idea="standard research phase_list",
        final_tier="standard",
        research_opt_in=True,
        today=TODAY,
    )

    payload = _read_state(folder)
    phase_list = payload["routing"]["phase_list"]
    assert phase_list[0] == "research"
    assert phase_list == [
        "research",
        "spec",
        "scenarios",
        "plan",
        "crucible",
        "review",
        "execute",
        "verify",
        "ship",
    ]
    assert len(phase_list) == 9


def test_standard_research_opt_in_state_validates_against_schema(tmp_path: Path) -> None:
    """The seeded payload round-trips through the state schema."""
    repo = _stage_repo(tmp_path)

    folder = seed_routed_feature(
        repo,
        idea="standard research schema check",
        final_tier="standard",
        research_opt_in=True,
        today=TODAY,
    )

    payload = _read_state(folder)
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.validate(instance=payload, schema=schema)


def test_standard_research_opt_in_suppresses_legacy_research_skip(tmp_path: Path) -> None:
    """Research IS the seed phase, so the legacy ``skipped[research]`` entry
    must be suppressed — leaving it would falsely advertise the phase as
    intentionally skipped while the feature is actively running it.
    """
    repo = _stage_repo(tmp_path)

    folder = seed_routed_feature(
        repo,
        idea="suppress legacy skip on research opt-in",
        final_tier="standard",
        research_opt_in=True,
        today=TODAY,
    )

    payload = _read_state(folder)
    assert payload["skipped"] == []


# ---------------------------------------------------------------------------
# Standard tier WITHOUT --research (legacy default, unchanged)
# ---------------------------------------------------------------------------


def test_standard_without_research_opt_in_unchanged(tmp_path: Path) -> None:
    """Standard without ``--research`` keeps the legacy spec entry + skip."""
    repo = _stage_repo(tmp_path)

    folder = seed_routed_feature(
        repo,
        idea="legacy standard no research",
        final_tier="standard",
        today=TODAY,
    )

    payload = _read_state(folder)
    assert payload["current_phase"] == "spec"
    assert payload["phases"]["spec"]["status"] == "in_progress"
    # Legacy behavior: research stays in skipped and phase_list is omitted
    # (lazy-derive returns the 8-entry standard list on read).
    assert payload["skipped"] == [
        {"phase": "research", "reason": "research deferred; manual research acceptable"}
    ]
    assert "phase_list" not in payload["routing"]


# ---------------------------------------------------------------------------
# Full tier — research always in the list
# ---------------------------------------------------------------------------


def test_full_tier_writes_phase_list_with_research(tmp_path: Path) -> None:
    """Full tier seeds the 11-entry phase list including research."""
    repo = _stage_repo(tmp_path)

    folder = seed_routed_feature(
        repo,
        idea="full tier writes phase list",
        final_tier="full",
        today=TODAY,
    )

    payload = _read_state(folder)
    assert payload["current_phase"] == "refine"
    phase_list = payload["routing"]["phase_list"]
    assert phase_list == [
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
    ]
    # Research is part of the run — legacy skip entry must be suppressed.
    assert payload["skipped"] == []


def test_full_tier_research_opt_in_idempotent(tmp_path: Path) -> None:
    """Passing ``research_opt_in=True`` on full tier is a no-op (research
    already runs); the seed shape is identical to the no-flag full path.
    """
    repo = _stage_repo(tmp_path)

    folder = seed_routed_feature(
        repo,
        idea="full tier research flag is no-op",
        final_tier="full",
        research_opt_in=True,
        today=TODAY,
    )

    payload = _read_state(folder)
    assert payload["current_phase"] == "refine"
    assert payload["routing"]["phase_list"][0] == "refine"
    assert "research" in payload["routing"]["phase_list"]
    assert payload["skipped"] == []


# ---------------------------------------------------------------------------
# Focused tier — research refused
# ---------------------------------------------------------------------------


def test_focused_research_opt_in_raises_value_error(tmp_path: Path) -> None:
    """Focused + ``research_opt_in=True`` refuses with the spec-locked hint
    BEFORE any disk mutation.
    """
    repo = _stage_repo(tmp_path)

    expected_message = (
        'research escalates to standard tier; use /forge:do --standard --research "<idea>"'
    )
    with pytest.raises(ValueError) as excinfo:
        seed_routed_feature(
            repo,
            idea="focused with research opt-in",
            final_tier="focused",
            research_opt_in=True,
            today=TODAY,
        )

    assert str(excinfo.value) == expected_message
    features_root = repo / ".forge" / "features"
    assert not features_root.exists() or not any(features_root.iterdir()), (
        "no folder may be seeded when focused refuses --research"
    )


def test_focused_without_research_opt_in_unchanged(tmp_path: Path) -> None:
    """Focused without ``--research`` is the legacy 3-phase path."""
    repo = _stage_repo(tmp_path)

    folder = seed_routed_feature(
        repo,
        idea="focused unchanged",
        final_tier="focused",
        today=TODAY,
    )

    payload = _read_state(folder)
    assert payload["current_phase"] == "spec"
    assert "phase_list" not in payload["routing"]
    # Legacy skip entry survives (focused never runs research).
    assert payload["skipped"] == [
        {"phase": "research", "reason": "research deferred; manual research acceptable"}
    ]
