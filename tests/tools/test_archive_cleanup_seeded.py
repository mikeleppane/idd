"""Tests for cleanup_seeded_feature.

The helper is a distinct call-site alias for cleanup_orphan_feature: same
generalized predicate (refine|spec x in_progress + no commits + folder
contents subset of _ORPHAN_FEATURE_FILES), same race-narrowing recheck,
same shutil.rmtree, but stderr WARN messages name `cleanup_seeded_feature`
so log lines point at the actual entry point /forge:do uses.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tools.archive import ArchiveError, cleanup_seeded_feature


def _write_state(folder: Path, **overrides: Any) -> None:
    """Write a minimal seed-orphan-candidate state.json into folder.

    Default body matches a /forge:do focused-tier seed: current_phase=spec
    with phases.spec.status=in_progress.
    """
    folder.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "feature_id": folder.name,
        "tier": "focused",
        "current_phase": "spec",
        "phases": {"spec": {"status": "in_progress"}},
        "skipped": [],
        "deviations": [],
        "commits": [],
    }
    payload.update(overrides)
    (folder / "state.json").write_text(json.dumps(payload), encoding="utf-8")


def _seed_focused(repo_root: Path, feature_id: str) -> Path:
    """Create a /forge:do focused-tier seed feature folder."""
    folder = repo_root / ".forge" / "features" / feature_id
    _write_state(folder)
    (folder / "SPEC.md").write_text("# SPEC\n", encoding="utf-8")
    (folder / "decisions.md").write_text("# Decisions\n", encoding="utf-8")
    return folder


def _seed_standard(repo_root: Path, feature_id: str) -> Path:
    """Create a /forge:do standard-tier seed feature folder."""
    folder = repo_root / ".forge" / "features" / feature_id
    _write_state(folder, tier="standard")
    (folder / "SPEC.md").write_text("# SPEC\n", encoding="utf-8")
    (folder / "decisions.md").write_text("# Decisions\n", encoding="utf-8")
    return folder


# ---------------------------------------------------------------------------
# Happy paths — focused + standard tier seeds remove cleanly
# ---------------------------------------------------------------------------


def test_cleanup_seeded_feature_coerces_string_repo_root(tmp_path: Path) -> None:
    """Boundary coercion: a string repo_root must not trip ``TypeError``.

    Mirrors the pattern locked into ``tools.bdd_detect.detect`` — agent
    callers that pass a string for an annotated ``Path`` parameter must
    not crash four frames deep on ``str / ".forge" / ...``; instead the
    documented missing-folder WARN+False branch should fire.
    """
    feature_id = "2026-05-08-focused-seed"
    folder = _seed_focused(tmp_path, feature_id)
    assert folder.is_dir()

    result = cleanup_seeded_feature(str(tmp_path), feature_id)

    assert result is True
    assert not folder.exists()


def test_cleanup_seeded_feature_focused_in_progress_removes_folder(tmp_path: Path) -> None:
    feature_id = "2026-05-08-focused-seed"
    folder = _seed_focused(tmp_path, feature_id)
    assert folder.is_dir()

    result = cleanup_seeded_feature(tmp_path, feature_id)

    assert result is True
    assert not folder.exists()


def test_cleanup_seeded_feature_standard_in_progress_removes_folder(tmp_path: Path) -> None:
    feature_id = "2026-05-08-standard-seed"
    folder = _seed_standard(tmp_path, feature_id)
    assert folder.is_dir()

    result = cleanup_seeded_feature(tmp_path, feature_id)

    assert result is True
    assert not folder.exists()


# ---------------------------------------------------------------------------
# Refusal — phase advanced past spec
# ---------------------------------------------------------------------------


def test_cleanup_seeded_feature_refuses_when_phase_advanced_to_scenarios(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    feature_id = "2026-05-08-advanced-scenarios"
    folder = tmp_path / ".forge" / "features" / feature_id
    _write_state(
        folder,
        current_phase="scenarios",
        phases={
            "spec": {"status": "done"},
            "scenarios": {"status": "in_progress"},
        },
    )
    (folder / "SPEC.md").write_text("# SPEC\n", encoding="utf-8")

    result = cleanup_seeded_feature(tmp_path, feature_id)

    assert result is False
    assert folder.is_dir()
    captured = capsys.readouterr()
    assert "WARN" in captured.err
    assert "cleanup_seeded_feature" in captured.err


def test_cleanup_seeded_feature_refuses_when_phase_advanced_to_plan(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    feature_id = "2026-05-08-advanced-plan"
    folder = tmp_path / ".forge" / "features" / feature_id
    _write_state(
        folder,
        current_phase="plan",
        phases={
            "spec": {"status": "done"},
            "plan": {"status": "in_progress"},
        },
    )
    (folder / "SPEC.md").write_text("# SPEC\n", encoding="utf-8")

    result = cleanup_seeded_feature(tmp_path, feature_id)

    assert result is False
    assert folder.is_dir()
    assert "WARN" in capsys.readouterr().err


def test_cleanup_seeded_feature_refuses_when_phase_advanced_to_execute(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    feature_id = "2026-05-08-advanced-execute"
    folder = tmp_path / ".forge" / "features" / feature_id
    _write_state(
        folder,
        current_phase="execute",
        phases={
            "spec": {"status": "done"},
            "execute": {"status": "in_progress"},
        },
    )
    (folder / "SPEC.md").write_text("# SPEC\n", encoding="utf-8")

    result = cleanup_seeded_feature(tmp_path, feature_id)

    assert result is False
    assert folder.is_dir()
    assert "WARN" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Refusal — non-empty commits / extra files / malformed JSON
# ---------------------------------------------------------------------------


def test_cleanup_seeded_feature_refuses_when_commits_nonempty(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    feature_id = "2026-05-08-has-commits"
    folder = _seed_focused(tmp_path, feature_id)
    payload = json.loads((folder / "state.json").read_text(encoding="utf-8"))
    payload["commits"] = [{"sha": "abc1234", "subject": "feat: stuff"}]
    (folder / "state.json").write_text(json.dumps(payload), encoding="utf-8")

    result = cleanup_seeded_feature(tmp_path, feature_id)

    assert result is False
    assert folder.is_dir()
    captured = capsys.readouterr()
    assert "WARN" in captured.err
    assert "cleanup_seeded_feature" in captured.err


def test_cleanup_seeded_feature_refuses_when_extra_files_present(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    feature_id = "2026-05-08-has-extras"
    folder = _seed_focused(tmp_path, feature_id)
    (folder / "PLAN.md").write_text("# Plan\n", encoding="utf-8")

    result = cleanup_seeded_feature(tmp_path, feature_id)

    assert result is False
    assert folder.is_dir()
    assert (folder / "PLAN.md").exists()
    captured = capsys.readouterr()
    assert "WARN" in captured.err
    assert "cleanup_seeded_feature" in captured.err


def test_cleanup_seeded_feature_refuses_on_malformed_state_json_with_warn(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    feature_id = "2026-05-08-bad-json"
    folder = tmp_path / ".forge" / "features" / feature_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text("{not valid json", encoding="utf-8")

    result = cleanup_seeded_feature(tmp_path, feature_id)

    assert result is False
    assert folder.is_dir()
    captured = capsys.readouterr()
    assert "WARN" in captured.err
    assert "cleanup_seeded_feature" in captured.err


# ---------------------------------------------------------------------------
# Refusal — fail-closed on malformed commits (external review finding)
# ---------------------------------------------------------------------------


def test_cleanup_seeded_feature_refuses_when_commits_is_empty_string(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """commits == "" must NOT pass the orphan check (fail-closed).

    Pre-fix the predicate ran ``payload.get("commits") or []`` which coerced
    every falsy value (including ``""``) to ``[]`` and removed the folder.
    The post-fix code rejects any non-empty-list shape outright.
    """
    feature_id = "2026-05-08-empty-string-commits"
    folder = _seed_focused(tmp_path, feature_id)
    payload = json.loads((folder / "state.json").read_text(encoding="utf-8"))
    payload["commits"] = ""  # malformed — must refuse cleanup
    (folder / "state.json").write_text(json.dumps(payload), encoding="utf-8")

    result = cleanup_seeded_feature(tmp_path, feature_id)

    assert result is False
    assert folder.is_dir()
    captured = capsys.readouterr()
    assert "WARN" in captured.err


def test_cleanup_seeded_feature_refuses_when_commits_is_none(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """commits == null in JSON must NOT pass the orphan check (fail-closed)."""
    feature_id = "2026-05-08-none-commits"
    folder = _seed_focused(tmp_path, feature_id)
    payload = json.loads((folder / "state.json").read_text(encoding="utf-8"))
    payload["commits"] = None
    (folder / "state.json").write_text(json.dumps(payload), encoding="utf-8")

    result = cleanup_seeded_feature(tmp_path, feature_id)

    assert result is False
    assert folder.is_dir()
    assert "WARN" in capsys.readouterr().err


def test_cleanup_seeded_feature_refuses_when_commits_key_missing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """state.json with no commits key at all must refuse cleanup (fail-closed)."""
    feature_id = "2026-05-08-no-commits-key"
    folder = tmp_path / ".forge" / "features" / feature_id
    folder.mkdir(parents=True)
    payload: dict[str, Any] = {
        "feature_id": feature_id,
        "tier": "focused",
        "current_phase": "spec",
        "phases": {"spec": {"status": "in_progress"}},
        "skipped": [],
        "deviations": [],
        # commits key intentionally omitted
    }
    (folder / "state.json").write_text(json.dumps(payload), encoding="utf-8")
    (folder / "SPEC.md").write_text("# x\n", encoding="utf-8")

    result = cleanup_seeded_feature(tmp_path, feature_id)

    assert result is False
    assert folder.is_dir()
    assert "WARN" in capsys.readouterr().err


def test_cleanup_seeded_feature_refuses_when_commits_is_false(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """commits == false must NOT pass the orphan check."""
    feature_id = "2026-05-08-false-commits"
    folder = _seed_focused(tmp_path, feature_id)
    payload = json.loads((folder / "state.json").read_text(encoding="utf-8"))
    payload["commits"] = False
    (folder / "state.json").write_text(json.dumps(payload), encoding="utf-8")

    result = cleanup_seeded_feature(tmp_path, feature_id)

    assert result is False
    assert folder.is_dir()
    assert "WARN" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Idempotency — second call is a no-op with WARN
# ---------------------------------------------------------------------------


def test_cleanup_seeded_feature_idempotent_recall(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    feature_id = "2026-05-08-idempotent"
    folder = _seed_focused(tmp_path, feature_id)

    first = cleanup_seeded_feature(tmp_path, feature_id)
    assert first is True
    assert not folder.exists()

    # Drain captured stderr so we only see the second-call output.
    capsys.readouterr()

    second = cleanup_seeded_feature(tmp_path, feature_id)
    assert second is False
    captured = capsys.readouterr()
    assert "WARN" in captured.err
    assert "cleanup_seeded_feature" in captured.err
    assert "not a directory" in captured.err


# ---------------------------------------------------------------------------
# Invalid feature_id raises ArchiveError
# ---------------------------------------------------------------------------


def test_cleanup_seeded_feature_invalid_feature_id_raises(tmp_path: Path) -> None:
    with pytest.raises(ArchiveError, match="invalid feature id"):
        cleanup_seeded_feature(tmp_path, "not-a-valid-id")


def test_cleanup_seeded_feature_invalid_feature_id_raises_empty(tmp_path: Path) -> None:
    with pytest.raises(ArchiveError, match="invalid feature id"):
        cleanup_seeded_feature(tmp_path, "")


# ---------------------------------------------------------------------------
# Log label parity — WARN line names the actual entry point
# ---------------------------------------------------------------------------


def test_cleanup_seeded_feature_warn_label_says_cleanup_seeded_feature(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """All WARN lines must name cleanup_seeded_feature, never cleanup_orphan_feature."""
    feature_id = "2026-05-08-label-check"
    folder = tmp_path / ".forge" / "features" / feature_id
    _write_state(
        folder,
        current_phase="plan",
        phases={"plan": {"status": "in_progress"}},
    )

    result = cleanup_seeded_feature(tmp_path, feature_id)

    assert result is False
    captured = capsys.readouterr()
    assert "cleanup_seeded_feature" in captured.err
    assert "cleanup_orphan_feature" not in captured.err
