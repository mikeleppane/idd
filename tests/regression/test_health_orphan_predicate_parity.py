"""Regression: health.py orphan check and cleanup_orphan_feature share the same predicate.

Asserts that:
  1. Both modules import _ORPHAN_FEATURE_FILES from tools.validate._feature_layout
     (identity check, not just equality — same object).
  2. A folder with PLAN.md present (which disqualifies it from _ORPHAN_FEATURE_FILES)
     is NOT flagged as orphan by health AND is refused by cleanup_orphan_feature.
  3. A folder with only state.json + SPEC.md (both in _ORPHAN_FEATURE_FILES) IS
     flagged as orphan by health AND IS removed by cleanup_orphan_feature.

Reviewer-2 finding: predicates must be identical so health never recommends cleanup
on a folder that the helper would refuse.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tools import archive as archive_mod
from tools.archive import cleanup_orphan_feature
from tools.validate import _feature_layout as layout_mod
from tools.validate import health as health_mod
from tools.validate import validate_health

# ---------------------------------------------------------------------------
# Source-of-truth check: both modules import from tools.validate._feature_layout
# ---------------------------------------------------------------------------


def test_both_modules_import_orphan_set_from_feature_layout() -> None:
    """archive.py and health.py both source _ORPHAN_FEATURE_FILES from _feature_layout.

    Static check via source-text inspection rather than runtime attribute
    access on private names (avoids mypy / ruff complaints about reaching
    into module privates from test code).
    """
    archive_src = Path(__file__).resolve().parents[2] / "tools" / "archive.py"
    health_src = Path(__file__).resolve().parents[2] / "tools" / "validate" / "health.py"
    archive_text = archive_src.read_text(encoding="utf-8")
    health_text = health_src.read_text(encoding="utf-8")
    assert "from tools.validate._feature_layout import" in archive_text, (
        "tools/archive.py must import from tools.validate._feature_layout"
    )
    assert "_ORPHAN_FEATURE_FILES" in archive_text, (
        "tools/archive.py must reference _ORPHAN_FEATURE_FILES"
    )
    assert "from ._feature_layout import" in health_text, (
        "tools/validate/health.py must import from ._feature_layout"
    )
    assert "_ORPHAN_FEATURE_FILES" in health_text, (
        "tools/validate/health.py must reference _ORPHAN_FEATURE_FILES"
    )


def test_orphan_seed_phases_shared_via_feature_layout() -> None:
    """archive.py and health.py both bind the SAME _ORPHAN_SEED_PHASES object.

    Identity check (via the canonical module) ensures a future change to one
    predicate's phase set cannot silently desync from the other.  M3 P6.1 T7
    finding p6-1-M1.
    """
    canonical = layout_mod._ORPHAN_SEED_PHASES
    archive_attr = getattr(archive_mod, "_ORPHAN_SEED_PHASES")  # noqa: B009
    health_attr = getattr(health_mod, "_ORPHAN_SEED_PHASES")  # noqa: B009
    assert archive_attr is canonical, (
        "tools.archive._ORPHAN_SEED_PHASES must be the SAME object as "
        "tools.validate._feature_layout._ORPHAN_SEED_PHASES"
    )
    assert health_attr is canonical, (
        "tools.validate.health._ORPHAN_SEED_PHASES must be the SAME object as "
        "tools.validate._feature_layout._ORPHAN_SEED_PHASES"
    )
    assert canonical == frozenset({"refine", "spec"}), (
        f"_ORPHAN_SEED_PHASES drifted from documented contract: {canonical!r}"
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_feature(
    repo_root: Path,
    feature_id: str,
    extra_files: list[str] | None = None,
    **state_overrides: Any,
) -> Path:
    """Create a feature folder with an orphan-candidate state.json."""
    folder = repo_root / ".forge" / "features" / feature_id
    folder.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "feature_id": feature_id,
        "tier": "focused",
        "current_phase": "refine",
        "phases": {"refine": {"status": "in_progress"}},
        "skipped": [],
        "deviations": [],
        "commits": [],
    }
    payload.update(state_overrides)
    (folder / "state.json").write_text(json.dumps(payload), encoding="utf-8")
    (folder / "SPEC.md").write_text("# SPEC\n", encoding="utf-8")
    for fname in extra_files or []:
        (folder / fname).write_text(f"# {fname}\n", encoding="utf-8")
    return folder


# ---------------------------------------------------------------------------
# Parity test: PLAN.md disqualifies — both predicates refuse
# ---------------------------------------------------------------------------


def test_plan_md_disqualifies_both_predicates(tmp_path: Path) -> None:
    """Folder with PLAN.md: health must NOT produce orphan LOW; cleanup must return False."""
    feature_id = "2026-05-08-with-plan"
    folder = _seed_feature(tmp_path, feature_id, extra_files=["PLAN.md"])

    # Health should NOT flag this as an orphan.
    findings = validate_health(tmp_path)
    orphan_findings = [
        f for f in findings if "orphan" in f.message.lower() and feature_id in f.message
    ]
    assert orphan_findings == [], (
        f"health incorrectly flagged {feature_id} as orphan despite PLAN.md; "
        f"findings: {orphan_findings}"
    )

    # cleanup_orphan_feature should also refuse.
    result = cleanup_orphan_feature(tmp_path, feature_id)
    assert result is False, "cleanup_orphan_feature must refuse when PLAN.md is present"
    assert folder.is_dir(), "folder must remain intact after refusal"


# ---------------------------------------------------------------------------
# Parity test: clean orphan — both predicates agree it IS an orphan
# ---------------------------------------------------------------------------


def test_clean_orphan_both_predicates_agree(tmp_path: Path) -> None:
    """Folder with only state.json+SPEC.md: health flags orphan AND cleanup succeeds."""
    # --- Health check on original ---
    feature_id = "2026-05-08-clean-orphan"
    _seed_feature(tmp_path, feature_id)

    findings = validate_health(tmp_path)
    orphan_findings = [
        f for f in findings if "orphan" in f.message.lower() and feature_id in f.message
    ]
    assert len(orphan_findings) == 1, f"health must flag clean orphan; got {orphan_findings}"
    assert orphan_findings[0].severity == "LOW"

    # --- cleanup on a separate copy ---
    tmp2 = tmp_path / "_cleanup_copy"
    tmp2.mkdir()
    _seed_feature(tmp2, feature_id)

    result = cleanup_orphan_feature(tmp2, feature_id)
    assert result is True, "cleanup_orphan_feature must succeed on clean orphan"
    assert not (tmp2 / ".forge" / "features" / feature_id).exists()


# ---------------------------------------------------------------------------
# Generalized predicate parity (T0.5) — health flags spec-tier seed orphans
# ---------------------------------------------------------------------------


def test_validate_health_flags_orphan_for_spec_in_progress(tmp_path: Path) -> None:
    """A /forge:do focused/standard pre-seed (current_phase=spec, no commits, no extras)
    must surface a LOW orphan finding, mirroring the refine-tier path."""
    feature_id = "2026-05-08-spec-seed-orphan"
    _seed_feature(
        tmp_path,
        feature_id,
        current_phase="spec",
        phases={"spec": {"status": "in_progress"}},
    )

    findings = validate_health(tmp_path)
    orphan_findings = [
        f for f in findings if "orphan" in f.message.lower() and feature_id in f.message
    ]
    assert len(orphan_findings) == 1, f"health must flag spec-seed orphan; got {orphan_findings}"
    assert orphan_findings[0].severity == "LOW"


def test_validate_health_flags_orphan_for_refine_in_progress_unchanged(tmp_path: Path) -> None:
    """The refine-tier path must keep firing post-generalization (regression guard)."""
    feature_id = "2026-05-08-refine-seed-orphan"
    _seed_feature(tmp_path, feature_id)  # default current_phase=refine

    findings = validate_health(tmp_path)
    orphan_findings = [
        f for f in findings if "orphan" in f.message.lower() and feature_id in f.message
    ]
    assert len(orphan_findings) == 1
    assert orphan_findings[0].severity == "LOW"


def test_validate_health_orphan_message_names_phase_and_helper(tmp_path: Path) -> None:
    """Orphan finding message must (a) name the actual phase and (b) point at the
    correct helper: cleanup_seeded_feature for spec, cleanup_orphan_feature for refine.
    """
    # --- Spec seed: helper is cleanup_seeded_feature ---
    spec_id = "2026-05-08-spec-helper-name"
    _seed_feature(
        tmp_path,
        spec_id,
        current_phase="spec",
        phases={"spec": {"status": "in_progress"}},
    )

    # --- Refine seed: helper is cleanup_orphan_feature ---
    refine_id = "2026-05-08-refine-helper-name"
    _seed_feature(tmp_path, refine_id)

    findings = validate_health(tmp_path)
    spec_findings = [f for f in findings if spec_id in f.message and "orphan" in f.message.lower()]
    refine_findings = [
        f for f in findings if refine_id in f.message and "orphan" in f.message.lower()
    ]

    assert len(spec_findings) == 1
    assert "spec" in spec_findings[0].message
    assert "cleanup_seeded_feature" in spec_findings[0].message

    assert len(refine_findings) == 1
    assert "refine" in refine_findings[0].message
    assert "cleanup_orphan_feature" in refine_findings[0].message
