"""Tests for cleanup_orphan_feature (D-2a contract).

9 paths per Open Scoping #4 + plan-body:
  1. Happy path: valid feature, only state.json+SPEC.md+decisions.md, correct state → True + folder gone.
  2. Refuse on current_phase != refine → False + folder intact.
  3. Refuse on commits != [] → False + folder intact.
  4. Refuse on extra file (PLAN.md) → False + folder intact.
  5. Refuse on phases.refine.status != in_progress → False + folder intact.
  6. Race-narrowing recheck: file added between first check and rmtree → False + folder intact.
  7. Invalid feature_id → raises ArchiveError.
  8. Missing folder → False (not raise).
  9. Permission error from rmtree propagates.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from tools.archive import ArchiveError, cleanup_orphan_feature


def _write_state(folder: Path, **overrides: Any) -> None:
    """Write a minimal orphan-candidate state.json into folder."""
    folder.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "feature_id": folder.name,
        "tier": "focused",
        "current_phase": "refine",
        "phases": {"refine": {"status": "in_progress"}},
        "skipped": [],
        "deviations": [],
        "commits": [],
    }
    payload.update(overrides)
    (folder / "state.json").write_text(json.dumps(payload), encoding="utf-8")


def _seed_orphan(repo_root: Path, feature_id: str) -> Path:
    """Create a minimal valid orphan feature folder (all three orphan files)."""
    folder = repo_root / ".forge" / "features" / feature_id
    _write_state(folder)
    (folder / "SPEC.md").write_text("# SPEC\n", encoding="utf-8")
    (folder / "decisions.md").write_text("# Decisions\n", encoding="utf-8")
    return folder


# ---------------------------------------------------------------------------
# Path 1 — Happy path
# ---------------------------------------------------------------------------


def test_happy_path_removes_folder(tmp_path: Path) -> None:
    """Valid orphan: refine+in_progress+no commits+only orphan files → True + gone."""
    feature_id = "2026-05-08-happy-orphan"
    folder = _seed_orphan(tmp_path, feature_id)
    assert folder.is_dir()

    result = cleanup_orphan_feature(tmp_path, feature_id)

    assert result is True
    assert not folder.exists()


def test_happy_path_state_json_only(tmp_path: Path) -> None:
    """Orphan with only state.json (a subset of orphan files) → True + gone."""
    feature_id = "2026-05-08-state-only"
    folder = tmp_path / ".forge" / "features" / feature_id
    _write_state(folder)
    # No SPEC.md or decisions.md — still a strict subset.

    result = cleanup_orphan_feature(tmp_path, feature_id)

    assert result is True
    assert not folder.exists()


# ---------------------------------------------------------------------------
# Path 2 — Refuse on current_phase != refine
# ---------------------------------------------------------------------------


def test_refuse_current_phase_not_refine(tmp_path: Path) -> None:
    """current_phase advanced past the seed-phase set → returns False, folder intact.

    The predicate accepts both ``refine`` and ``spec`` as seed phases.
    This test asserts refusal once the phase has advanced PAST those
    seeds (plan, execute, ...).
    """
    feature_id = "2026-05-08-wrong-phase"
    folder = tmp_path / ".forge" / "features" / feature_id
    _write_state(folder, current_phase="plan", phases={"plan": {"status": "in_progress"}})
    (folder / "SPEC.md").write_text("# SPEC\n", encoding="utf-8")

    result = cleanup_orphan_feature(tmp_path, feature_id)

    assert result is False
    assert folder.is_dir()


# ---------------------------------------------------------------------------
# Path 3 — Refuse on commits != []
# ---------------------------------------------------------------------------


def test_refuse_commits_not_empty(tmp_path: Path) -> None:
    """commits has one entry → returns False, folder intact."""
    feature_id = "2026-05-08-has-commits"
    folder = tmp_path / ".forge" / "features" / feature_id
    _write_state(folder, commits=["abc1234"])

    result = cleanup_orphan_feature(tmp_path, feature_id)

    assert result is False
    assert folder.is_dir()


# ---------------------------------------------------------------------------
# Path 4 — Refuse on extra file (PLAN.md)
# ---------------------------------------------------------------------------


def test_refuse_extra_file_plan_md(tmp_path: Path) -> None:
    """PLAN.md present alongside orphan files → returns False, folder intact."""
    feature_id = "2026-05-08-has-plan"
    folder = _seed_orphan(tmp_path, feature_id)
    (folder / "PLAN.md").write_text("# Plan\n", encoding="utf-8")

    result = cleanup_orphan_feature(tmp_path, feature_id)

    assert result is False
    assert folder.is_dir()
    assert (folder / "PLAN.md").exists()


# ---------------------------------------------------------------------------
# Path 5 — Refuse on phases.refine.status != in_progress
# ---------------------------------------------------------------------------


def test_refuse_refine_status_done(tmp_path: Path) -> None:
    """phases.refine.status='done' → returns False, folder intact."""
    feature_id = "2026-05-08-refine-done"
    folder = tmp_path / ".forge" / "features" / feature_id
    _write_state(folder, phases={"refine": {"status": "done"}})

    result = cleanup_orphan_feature(tmp_path, feature_id)

    assert result is False
    assert folder.is_dir()


def test_refuse_refine_status_missing(tmp_path: Path) -> None:
    """phases.refine block has no 'status' key → returns False, folder intact."""
    feature_id = "2026-05-08-refine-no-status"
    folder = tmp_path / ".forge" / "features" / feature_id
    _write_state(folder, phases={"refine": {}})

    result = cleanup_orphan_feature(tmp_path, feature_id)

    assert result is False
    assert folder.is_dir()


# ---------------------------------------------------------------------------
# Path 6 — Race-narrowing recheck
# ---------------------------------------------------------------------------


def test_race_narrowing_recheck_aborts_when_conditions_change(tmp_path: Path) -> None:
    """Re-check between preflight and rmtree must abort if conditions changed.

    The race window in cleanup_orphan_feature is between the second
    `_orphan_conditions_met` call and `shutil.rmtree`. A real-world
    out-of-band mutation (concurrent writer, IDE, watch process) would
    invalidate the orphan precondition between the two events. We simulate
    that by patching `_orphan_conditions_met` so it returns True on the
    first (preflight) call and False on the second (race-narrow) call.

    Expected behavior: function returns False without invoking rmtree.
    """
    feature_id = "2026-05-08-race-check"
    folder = _seed_orphan(tmp_path, feature_id)

    call_results = iter([True, False])

    def _flaky_conditions(_folder: Path) -> bool:
        return next(call_results)

    with (
        patch("tools.archive._orphan_conditions_met", side_effect=_flaky_conditions),
        patch("tools.archive.shutil.rmtree") as mock_rmtree,
    ):
        result = cleanup_orphan_feature(tmp_path, feature_id)

    assert result is False
    # Folder still exists — rmtree was never invoked by our function.
    assert folder.is_dir()
    mock_rmtree.assert_not_called()


# ---------------------------------------------------------------------------
# Path 7 — Invalid feature_id raises ArchiveError
# ---------------------------------------------------------------------------


def test_invalid_feature_id_raises_archive_error(tmp_path: Path) -> None:
    """Malformed feature_id slug → raises ArchiveError immediately."""
    with pytest.raises(ArchiveError, match="invalid feature id"):
        cleanup_orphan_feature(tmp_path, "not-a-valid-id")


def test_invalid_feature_id_raises_archive_error_empty(tmp_path: Path) -> None:
    """Empty feature_id slug → raises ArchiveError immediately."""
    with pytest.raises(ArchiveError, match="invalid feature id"):
        cleanup_orphan_feature(tmp_path, "")


# ---------------------------------------------------------------------------
# Path 8 — Missing folder returns False (not raise)
# ---------------------------------------------------------------------------


def test_missing_folder_returns_false(tmp_path: Path) -> None:
    """Valid feature_id but folder doesn't exist → returns False (does NOT raise)."""
    feature_id = "2026-05-08-no-folder"

    result = cleanup_orphan_feature(tmp_path, feature_id)

    assert result is False


# ---------------------------------------------------------------------------
# Path 9 — Permission error propagates
# ---------------------------------------------------------------------------


def test_permission_error_propagates(tmp_path: Path) -> None:
    """PermissionError from shutil.rmtree must propagate (not be swallowed)."""
    feature_id = "2026-05-08-perm-error"
    _seed_orphan(tmp_path, feature_id)

    def _raise_permission(path: Any, **kwargs: Any) -> None:
        raise PermissionError("simulated permission denied")

    with (
        patch("tools.archive.shutil.rmtree", side_effect=_raise_permission),
        pytest.raises(PermissionError, match="simulated permission denied"),
    ):
        cleanup_orphan_feature(tmp_path, feature_id)


# ---------------------------------------------------------------------------
# Generalized predicate parity (T0.5) — refine path still works post-generalization
# ---------------------------------------------------------------------------


def test_cleanup_orphan_feature_still_works_for_refine_phase(tmp_path: Path) -> None:
    """Generalizing _orphan_conditions_met to accept refine|spec must not break refine."""
    feature_id = "2026-05-08-refine-still-works"
    folder = _seed_orphan(tmp_path, feature_id)
    assert folder.is_dir()

    result = cleanup_orphan_feature(tmp_path, feature_id)

    assert result is True
    assert not folder.exists()


def test_cleanup_orphan_feature_refuses_when_commits_is_empty_string(
    tmp_path: Path,
) -> None:
    """commits == "" must fail-closed (parity with cleanup_seeded_feature)."""
    feature_id = "2026-05-08-orphan-empty-str-commits"
    folder = _seed_orphan(tmp_path, feature_id)
    payload = json.loads((folder / "state.json").read_text(encoding="utf-8"))
    payload["commits"] = ""
    (folder / "state.json").write_text(json.dumps(payload), encoding="utf-8")

    result = cleanup_orphan_feature(tmp_path, feature_id)

    assert result is False
    assert folder.is_dir()


def test_cleanup_orphan_feature_refuses_when_commits_is_none(tmp_path: Path) -> None:
    """commits == null must fail-closed (parity with cleanup_seeded_feature)."""
    feature_id = "2026-05-08-orphan-null-commits"
    folder = _seed_orphan(tmp_path, feature_id)
    payload = json.loads((folder / "state.json").read_text(encoding="utf-8"))
    payload["commits"] = None
    (folder / "state.json").write_text(json.dumps(payload), encoding="utf-8")

    result = cleanup_orphan_feature(tmp_path, feature_id)

    assert result is False
    assert folder.is_dir()


def test_cleanup_orphan_feature_warn_label_says_cleanup_orphan_feature(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """WARN line label parity: cleanup_orphan_feature's stderr must name itself."""
    feature_id = "2026-05-08-orphan-label"
    folder = tmp_path / ".forge" / "features" / feature_id
    _write_state(folder, commits=["abc1234"])

    result = cleanup_orphan_feature(tmp_path, feature_id)

    assert result is False
    captured = capsys.readouterr()
    assert "cleanup_orphan_feature" in captured.err
    assert "cleanup_seeded_feature" not in captured.err
