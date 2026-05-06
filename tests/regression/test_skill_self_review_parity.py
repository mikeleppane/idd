"""Per-skill regression suite: validators on real M1/M2 fixture trees.

Locks parity between the migrated `tools.validate` validators and the inline
self-review prose they replace in the four IDD skills (idd-spec, idd-plan,
idd-scenarios, idd-execute). Two complementary axes:

1.  **Fixture-pair parity** (per validator): for each migrated check, drive
    the validator against the fixtures the skill's M1/M2 self-review
    historically gated on, and assert the gate verdict (BLOCK/HIGH gates
    phase exit; MEDIUM/LOW are advisory) matches the expected outcome. The
    failing fixtures here come from the validator's own test fixture pool
    (Tasks 1-5) — that pool was *seeded* from the inline prose, so parity is
    real, not tautological.

2.  **Smoke-tree parity** (per migrated skill phase): when feature-shaped
    smoke fixtures exist under ``tests/smoke/``, every validator that gates
    that phase must produce zero BLOCK/HIGH findings on every smoke
    feature. If no smoke fixtures exist yet, the test skips with a
    deliberate marker (the plan calls this out — see Task 7.1).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tools import validate

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "_validate"
ANCHORS_REPO = FIX / "anchors_repo"
SMOKE = Path(__file__).resolve().parents[1] / "smoke"


def _gates(findings: list[validate.Finding]) -> bool:
    """Return True iff any finding has a gating severity (BLOCK or HIGH)."""
    return any(f.severity in {"BLOCK", "HIGH"} for f in findings)


# ---------------------------------------------------------------------------
# idd-scenarios — replaces orphan-scenario / unmapped-AC inline prose.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fixture", "should_gate"),
    [
        ("spec_scenarios_pass.md", False),
        ("spec_scenarios_orphan_scenario.md", True),
        ("spec_scenarios_unmapped_ac.md", True),
        ("spec_scenarios_measurable_ac.md", False),
        ("spec_scenarios_no_scenarios_section.md", True),
    ],
)
def test_idd_scenarios_parity(fixture: str, should_gate: bool) -> None:
    findings = validate.validate_scenarios(FIX / fixture)
    assert _gates(findings) is should_gate, [f.message for f in findings]


# ---------------------------------------------------------------------------
# idd-spec — replaces inline scenarios + anchors prose under self-review.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fixture", "should_gate"),
    [
        ("spec_anchors_pass.md", False),
        ("spec_anchors_no_section.md", False),
        ("spec_anchors_missing_path.md", True),
        ("spec_anchors_absolute_path.md", True),
        ("spec_anchors_traversal.md", True),
        # missing_symbol is MEDIUM only — advisory, must NOT gate.
        ("spec_anchors_missing_symbol.md", False),
    ],
)
def test_idd_spec_anchors_parity(fixture: str, should_gate: bool) -> None:
    findings = validate.validate_anchors(FIX / fixture, repo_root=ANCHORS_REPO)
    assert _gates(findings) is should_gate, [f.message for f in findings]


# ---------------------------------------------------------------------------
# idd-plan — replaces plan-tasks + verified-deps inline prose under self-review.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fixture_dir", "should_gate"),
    [
        ("plan_tasks_pass", False),
        ("plan_tasks_unmapped_ac", True),
        ("plan_tasks_double_mapped", True),
        ("plan_tasks_file_collision", True),
        ("plan_tasks_shared_file", False),
        # Backticked files exercise the regex; the fixture intentionally
        # collides "a.py" across slices, so the validator gates HIGH.
        ("plan_tasks_backticked_files", True),
    ],
)
def test_idd_plan_tasks_parity(fixture_dir: str, should_gate: bool) -> None:
    plan = FIX / fixture_dir / "PLAN.md"
    spec = FIX / fixture_dir / "SPEC.md"
    findings = validate.validate_plan_tasks(plan, spec_path=spec)
    assert _gates(findings) is should_gate, [f.message for f in findings]


@pytest.mark.parametrize(
    ("fixture", "should_gate"),
    [
        ("verified_deps_pass.md", False),
        ("verified_deps_no_section.md", False),  # section is optional.
        ("verified_deps_no_table.md", True),
        ("verified_deps_only_separator.md", True),
        ("verified_deps_missing_columns.md", True),
        # blank notes / unknown ecosystem are MEDIUM/LOW — advisory only.
        ("verified_deps_blank_notes_column.md", False),
        # Unknown ecosystem is a HIGH gate — the table claims support for a
        # registry the validator does not recognize.
        ("verified_deps_unknown_ecosystem.md", True),
    ],
)
def test_idd_plan_verified_deps_parity(fixture: str, should_gate: bool) -> None:
    findings = validate.validate_verified_deps(FIX / fixture)
    assert _gates(findings) is should_gate, [f.message for f in findings]


# ---------------------------------------------------------------------------
# idd-execute — replaces "no deviations marked unresolved" inline prose.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fixture_dir", "should_gate"),
    [
        ("deviations_pass", False),
        # Orphan decision drift surfaces only as INFO — advisory, not gating.
        ("deviations_orphan_decision", False),
        ("deviations_unrecorded", True),
        ("deviations_missing_decisions_md", True),
        ("deviations_unparseable_state", True),
        # endash / hyphen normalization fixtures must NOT gate.
        ("deviations_endash_decision", False),
        ("deviations_hyphen_decision", False),
    ],
)
def test_idd_execute_deviations_parity(fixture_dir: str, should_gate: bool) -> None:
    findings = validate.validate_deviations(FIX / fixture_dir)
    assert _gates(findings) is should_gate, [f.message for f in findings]


# ---------------------------------------------------------------------------
# Smoke-tree parity — every migrated validator ships green on real feature
# folders. If `tests/smoke/` carries no feature-shaped subdirectories yet,
# these are vacuously true and skip with a marker (per plan Task 7.1).
# ---------------------------------------------------------------------------


def _smoke_features_with(filename: str) -> list[Path]:
    if not SMOKE.is_dir():
        return []
    return sorted(p for p in SMOKE.iterdir() if p.is_dir() and (p / filename).is_file())


_SMOKE_SPEC_DIRS = _smoke_features_with("SPEC.md")
_SMOKE_PLAN_DIRS = _smoke_features_with("PLAN.md")
_SMOKE_STATE_DIRS = _smoke_features_with("state.json")


@pytest.mark.skipif(not _SMOKE_SPEC_DIRS, reason="no smoke fixtures present")
@pytest.mark.parametrize("feature_dir", _SMOKE_SPEC_DIRS)
def test_validate_spec_semantic_passes_on_smoke_fixtures(feature_dir: Path) -> None:
    """Every M1-shape SPEC ships green for both scenarios + anchors."""
    spec = feature_dir / "SPEC.md"
    findings = validate.validate_scenarios(spec) + validate.validate_anchors(
        spec, repo_root=feature_dir
    )
    blocking = [f for f in findings if f.severity in {"BLOCK", "HIGH"}]
    assert not blocking, f"{spec}: {[f.message for f in blocking]}"


@pytest.mark.skipif(not _SMOKE_PLAN_DIRS, reason="no smoke fixtures present")
@pytest.mark.parametrize("feature_dir", _SMOKE_PLAN_DIRS)
def test_validate_plan_passes_on_smoke_fixtures(feature_dir: Path) -> None:
    plan = feature_dir / "PLAN.md"
    spec = feature_dir / "SPEC.md"
    findings = validate.validate_plan_tasks(plan, spec_path=spec) + validate.validate_verified_deps(
        plan
    )
    blocking = [f for f in findings if f.severity in {"BLOCK", "HIGH"}]
    assert not blocking, f"{plan}: {[f.message for f in blocking]}"


@pytest.mark.skipif(not _SMOKE_STATE_DIRS, reason="no smoke fixtures present")
@pytest.mark.parametrize("feature_dir", _SMOKE_STATE_DIRS)
def test_validate_deviations_passes_on_smoke_fixtures(feature_dir: Path) -> None:
    findings = validate.validate_deviations(feature_dir)
    blocking = [f for f in findings if f.severity in {"BLOCK", "HIGH"}]
    assert not blocking, f"{feature_dir}: {[f.message for f in blocking]}"


def test_validate_scenarios_measurable_ac_parity() -> None:
    """idd-spec allows AC -> 'one scenario or one measurable outcome'.

    The new validator must NOT flag a measurable-outcome AC as unmapped
    (correction #9 from the plan's pre-execution review).
    """
    findings = validate.validate_scenarios(FIX / "spec_scenarios_measurable_ac.md")
    blocking = [f for f in findings if f.severity in {"BLOCK", "HIGH"}]
    assert not blocking, [f.message for f in blocking]
