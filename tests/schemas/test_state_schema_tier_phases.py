"""Pin the per-tier ``phases.propertyNames`` constraint in state.schema.json.

The audit's F-12 finding noted that the loose schema admitted any of the
twelve lifecycle phases on any tier. This regression suite locks in the
tightened contract:

  * focused: {spec, execute, verify}
  * standard: {research, spec, scenarios, plan, crucible, review, execute,
    verify, ship, qa}
  * full: {refine, research, spec, domain, scenarios, plan, crucible,
    review, execute, verify, ship, qa}

A tier/phase pairing that falls outside the allowed set must refuse at
the schema layer.
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "schemas" / "state.schema.json"


def _validator() -> jsonschema.Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return jsonschema.Draft202012Validator(schema)


def _coherent_payload(*, tier: str, phase: str) -> dict[str, object]:
    return {
        "feature_id": "2026-05-12-tier-phase-probe",
        "tier": tier,
        "current_phase": phase,
        "phases": {phase: {"status": "in_progress", "started_at": "2026-05-12T00:00:00Z"}},
        "skipped": [],
        "deviations": [],
        "commits": [],
    }


_FOCUSED_ALLOWED = ("spec", "execute", "verify")
_FOCUSED_FORBIDDEN = (
    "refine",
    "research",
    "scenarios",
    "domain",
    "plan",
    "crucible",
    "review",
    "ship",
    "qa",
)

_STANDARD_ALLOWED = (
    "research",
    "spec",
    "scenarios",
    "plan",
    "crucible",
    "review",
    "execute",
    "verify",
    "ship",
    "qa",
)
_STANDARD_FORBIDDEN = ("refine", "domain")

_FULL_ALLOWED = (
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
)


@pytest.mark.parametrize("phase", _FOCUSED_ALLOWED)
def test_focused_accepts_allowed_phase(phase: str) -> None:
    payload = _coherent_payload(tier="focused", phase=phase)
    errors = list(_validator().iter_errors(payload))
    assert errors == []


@pytest.mark.parametrize("phase", _FOCUSED_FORBIDDEN)
def test_focused_refuses_forbidden_phase(phase: str) -> None:
    payload = _coherent_payload(tier="focused", phase=phase)
    errors = list(_validator().iter_errors(payload))
    assert any("propertyNames" in str(e.schema_path) for e in errors), (
        f"focused tier must refuse phase {phase!r}; errors: {[e.message for e in errors]}"
    )


@pytest.mark.parametrize("phase", _STANDARD_ALLOWED)
def test_standard_accepts_allowed_phase(phase: str) -> None:
    payload = _coherent_payload(tier="standard", phase=phase)
    errors = list(_validator().iter_errors(payload))
    assert errors == []


@pytest.mark.parametrize("phase", _STANDARD_FORBIDDEN)
def test_standard_refuses_forbidden_phase(phase: str) -> None:
    payload = _coherent_payload(tier="standard", phase=phase)
    errors = list(_validator().iter_errors(payload))
    assert any("propertyNames" in str(e.schema_path) for e in errors), (
        f"standard tier must refuse phase {phase!r}; errors: {[e.message for e in errors]}"
    )


@pytest.mark.parametrize("phase", _FULL_ALLOWED)
def test_full_accepts_every_lifecycle_phase(phase: str) -> None:
    payload = _coherent_payload(tier="full", phase=phase)
    errors = list(_validator().iter_errors(payload))
    assert errors == []


def test_focused_refuses_extra_phase_alongside_allowed() -> None:
    """Focused with a valid spec block AND a stray refine block must refuse."""
    payload = _coherent_payload(tier="focused", phase="spec")
    payload["phases"]["refine"] = {"status": "pending"}  # type: ignore[index]
    errors = list(_validator().iter_errors(payload))
    assert any("propertyNames" in str(e.schema_path) for e in errors)


def test_full_admits_focused_and_standard_phase_sets() -> None:
    """The full-tier allowed set is the superset of the other two tiers."""
    payload = _coherent_payload(tier="full", phase="spec")
    payload["phases"]["execute"] = {"status": "pending"}  # type: ignore[index]
    payload["phases"]["ship"] = {"status": "pending"}  # type: ignore[index]
    payload["phases"]["qa"] = {"status": "pending"}  # type: ignore[index]
    errors = list(_validator().iter_errors(payload))
    assert errors == []
