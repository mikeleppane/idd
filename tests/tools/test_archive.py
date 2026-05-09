"""Archival + canonical-spec write — pure file ops with explicit failure modes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import tools.archive as archive_mod
from tools.archive import (
    ArchiveError,
    archive_feature,
    archive_feature_after_qa,
    canonical_spec_path,
    ship_feature,
    write_canonical_spec,
)


def _seed_feature(repo_root: Path, feature_id: str, *, files: dict[str, str]) -> Path:
    feature_dir = repo_root / ".forge" / "features" / feature_id
    feature_dir.mkdir(parents=True)
    for name, body in files.items():
        (feature_dir / name).write_text(body, encoding="utf-8")
    return feature_dir


def test_archive_feature_moves_folder_under_features_archive(tmp_path: Path) -> None:
    feature_id = "2026-05-04-toggle-add"
    _seed_feature(tmp_path, feature_id, files={"SPEC.md": "# spec\n", "state.json": "{}\n"})

    archived = archive_feature(tmp_path, feature_id)

    assert archived == tmp_path / ".forge" / "features" / "archive" / feature_id
    assert archived.is_dir()
    assert (archived / "SPEC.md").read_text(encoding="utf-8") == "# spec\n"
    assert not (tmp_path / ".forge" / "features" / feature_id).exists()


def test_archive_feature_refuses_when_target_exists(tmp_path: Path) -> None:
    feature_id = "2026-05-04-toggle-add"
    _seed_feature(tmp_path, feature_id, files={"SPEC.md": "# spec\n"})
    (tmp_path / ".forge" / "features" / "archive" / feature_id).mkdir(parents=True)

    with pytest.raises(ArchiveError, match="already archived"):
        archive_feature(tmp_path, feature_id)


def test_archive_feature_missing_source_raises(tmp_path: Path) -> None:
    with pytest.raises(ArchiveError, match="not found"):
        archive_feature(tmp_path, "2026-05-04-missing")


def test_canonical_spec_path_uses_specs_capability_spec(tmp_path: Path) -> None:
    assert (
        canonical_spec_path(tmp_path, "feature-flag")
        == tmp_path / ".forge" / "specs" / "feature-flag" / "SPEC.md"
    )


def test_write_canonical_spec_creates_folder_and_file(tmp_path: Path) -> None:
    body = "---\ncapability: feature-flag\nstatus: shipped\n---\n# Feature Flag\n"

    written = write_canonical_spec(tmp_path, "feature-flag", body)

    assert written == tmp_path / ".forge" / "specs" / "feature-flag" / "SPEC.md"
    assert written.read_text(encoding="utf-8") == body


def test_write_canonical_spec_refuses_to_overwrite(tmp_path: Path) -> None:
    capability = "feature-flag"
    canonical_spec_path(tmp_path, capability).parent.mkdir(parents=True)
    canonical_spec_path(tmp_path, capability).write_text("existing\n", encoding="utf-8")

    with pytest.raises(ArchiveError, match="already exists"):
        write_canonical_spec(tmp_path, capability, "new\n")


@pytest.mark.parametrize("bad", ["", "Bad-Slug", "with space", "../escape", "TRUE/false"])
def test_invalid_capability_slug_rejected(tmp_path: Path, bad: str) -> None:
    with pytest.raises(ArchiveError, match="invalid capability"):
        write_canonical_spec(tmp_path, bad, "---\n---\n")


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "bad-id",
        "2026-13-99-x",
        "../escape",
        # Trailing hyphen and double hyphen previously slipped through the
        # lax ``[a-z0-9-]+`` slug pattern; the tightened regex rejects both.
        "2026-05-08-foo-",
        "2026-05-08-foo--bar",
    ],
)
def test_invalid_feature_id_rejected(tmp_path: Path, bad: str) -> None:
    with pytest.raises(ArchiveError, match="invalid feature id"):
        archive_feature(tmp_path, bad)


def _seed_constitution(repo_root: Path) -> None:
    """Write a minimal valid Constitution with one CRITICAL article."""
    cdir = repo_root / ".forge"
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "CONSTITUTION.md").write_text(
        '---\nversion: 0.1.0\ncreated: "2026-05-07"\n---\n\n'
        "# Project Constitution\n\n"
        "## Article 1 — Repo pattern [CRITICAL]\n"
        "**Rule:** ORM via repository/.\n"
        "**Reference:** —\n"
        "**Rationale:** —\n"
        "**Exception:** None.\n",
        encoding="utf-8",
    )


def test_ship_feature_warns_on_unguarded_constitution_with_unresolved_findings(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """H5 — advisory stderr warning when ship_feature runs without a hook
    while a Constitution exists AND REVIEW.code.md still carries open
    `[constitution:A<n>]` findings.

    The warning is best-effort: it must not change ship_feature's contract
    (no raise, no return-shape change) and any internal exception in the
    helper must be swallowed. The signal exists so a misconfigured retry
    that drops the gate hook fails loudly to the operator.
    """
    feature_id = "2026-05-04-toggle-add"
    capability = "feature-flag"
    body = (
        "---\ncapability: feature-flag\nstatus: shipped\n"
        "created: 2026-05-04\nlast_updated: 2026-05-04\n"
        "evidence:\n"
        "  - 2026-05-04-toggle-add: features/archive/2026-05-04-toggle-add/\n"
        "bounded_context: null\n---\n# Feature Flag\n"
    )
    review_body = (
        "---\nspec: 2026-05-04-toggle-add\ntarget: code\nstatus: open\ncycles: 1\n---\n\n"
        "# Findings\n\n"
        "| ID | Severity | Status | Location | Problem | Recommended Fix | Source |\n"
        "|----|----------|--------|----------|---------|-----------------|--------|\n"
        "| F-1 | HIGH | open | src/x.py:1 | [constitution:A1] direct ORM | move | self |\n"
    )
    _seed_feature(
        tmp_path,
        feature_id,
        files={"SPEC.md": "# spec\n", "state.json": "{}\n", "REVIEW.code.md": review_body},
    )
    _seed_constitution(tmp_path)

    # No pre_archive_hook. Warning must fire to stderr; ship still succeeds.
    ship_feature(tmp_path, feature_id, capability, body)

    captured = capsys.readouterr()
    assert "Constitution gate skipped" in captured.err, (
        f"expected stderr warning, got stderr={captured.err!r}"
    )


def test_ship_feature_does_not_warn_when_constitution_absent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """H5 — without a Constitution there is nothing to gate; no warning fires."""
    feature_id = "2026-05-04-toggle-add"
    capability = "feature-flag"
    body = (
        "---\ncapability: feature-flag\nstatus: shipped\n"
        "created: 2026-05-04\nlast_updated: 2026-05-04\n"
        "evidence:\n"
        "  - 2026-05-04-toggle-add: features/archive/2026-05-04-toggle-add/\n"
        "bounded_context: null\n---\n# Feature Flag\n"
    )
    _seed_feature(tmp_path, feature_id, files={"SPEC.md": "# spec\n", "state.json": "{}\n"})

    ship_feature(tmp_path, feature_id, capability, body)
    assert "Constitution gate skipped" not in capsys.readouterr().err


def test_ship_feature_does_not_warn_when_hook_supplied(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """H5 — when caller passes pre_archive_hook the warning is suppressed."""
    feature_id = "2026-05-04-toggle-add"
    capability = "feature-flag"
    body = (
        "---\ncapability: feature-flag\nstatus: shipped\n"
        "created: 2026-05-04\nlast_updated: 2026-05-04\n"
        "evidence:\n"
        "  - 2026-05-04-toggle-add: features/archive/2026-05-04-toggle-add/\n"
        "bounded_context: null\n---\n# Feature Flag\n"
    )
    review_body = (
        "---\nspec: 2026-05-04-toggle-add\ntarget: code\nstatus: open\ncycles: 1\n---\n\n"
        "# Findings\n\n"
        "| ID | Severity | Status | Location | Problem | Recommended Fix | Source |\n"
        "|----|----------|--------|----------|---------|-----------------|--------|\n"
        "| F-1 | HIGH | open | src/x.py:1 | [constitution:A1] direct ORM | move | self |\n"
    )
    _seed_feature(
        tmp_path,
        feature_id,
        files={"SPEC.md": "# spec\n", "state.json": "{}\n", "REVIEW.code.md": review_body},
    )
    _seed_constitution(tmp_path)

    ship_feature(
        tmp_path,
        feature_id,
        capability,
        body,
        pre_archive_hook=lambda _src: None,
    )
    assert "Constitution gate skipped" not in capsys.readouterr().err


def test_ship_feature_happy_path_writes_canonical_and_archives(tmp_path: Path) -> None:
    feature_id = "2026-05-04-toggle-add"
    capability = "feature-flag"
    body = (
        "---\ncapability: feature-flag\nstatus: shipped\n"
        "created: 2026-05-04\nlast_updated: 2026-05-04\n"
        "evidence:\n"
        "  - 2026-05-04-toggle-add: features/archive/2026-05-04-toggle-add/\n"
        "bounded_context: null\n---\n# Feature Flag\n"
    )
    _seed_feature(tmp_path, feature_id, files={"SPEC.md": "# spec\n", "state.json": "{}\n"})

    canonical, archive_path = ship_feature(tmp_path, feature_id, capability, body)

    assert canonical == tmp_path / ".forge" / "specs" / capability / "SPEC.md"
    assert canonical.read_text(encoding="utf-8") == body
    assert archive_path == tmp_path / ".forge" / "features" / "archive" / feature_id
    assert (archive_path / "SPEC.md").read_text(encoding="utf-8") == "# spec\n"
    assert not (tmp_path / ".forge" / "features" / feature_id).exists()


def test_ship_feature_refuses_when_canonical_already_exists(tmp_path: Path) -> None:
    feature_id = "2026-05-04-toggle-add"
    capability = "feature-flag"
    _seed_feature(tmp_path, feature_id, files={"SPEC.md": "# spec\n"})
    canonical = tmp_path / ".forge" / "specs" / capability / "SPEC.md"
    canonical.parent.mkdir(parents=True)
    canonical.write_text("existing\n", encoding="utf-8")

    with pytest.raises(ArchiveError, match=r"already exists|already shipped"):
        ship_feature(tmp_path, feature_id, capability, "---\n---\n")

    # Source untouched
    assert (tmp_path / ".forge" / "features" / feature_id / "SPEC.md").exists()
    # Canonical untouched (still says "existing")
    assert canonical.read_text(encoding="utf-8") == "existing\n"
    # No archive created
    assert not (tmp_path / ".forge" / "features" / "archive" / feature_id).exists()


def test_ship_feature_refuses_when_archive_target_exists(tmp_path: Path) -> None:
    feature_id = "2026-05-04-toggle-add"
    capability = "feature-flag"
    _seed_feature(tmp_path, feature_id, files={"SPEC.md": "# spec\n"})
    (tmp_path / ".forge" / "features" / "archive" / feature_id).mkdir(parents=True)

    with pytest.raises(ArchiveError, match="already archived"):
        ship_feature(tmp_path, feature_id, capability, "---\n---\n")

    # No canonical written
    assert not (tmp_path / ".forge" / "specs" / capability / "SPEC.md").exists()
    # Source untouched
    assert (tmp_path / ".forge" / "features" / feature_id / "SPEC.md").exists()


def test_ship_feature_refuses_when_source_missing(tmp_path: Path) -> None:
    with pytest.raises(ArchiveError, match="not found"):
        ship_feature(tmp_path, "2026-05-04-missing", "feature-flag", "---\n---\n")
    # No canonical written even though it would have been writable
    assert not (tmp_path / ".forge" / "specs" / "feature-flag" / "SPEC.md").exists()


@pytest.mark.parametrize("bad_feature_id", ["", "bad-id", "../escape"])
def test_ship_feature_rejects_invalid_feature_id(tmp_path: Path, bad_feature_id: str) -> None:
    with pytest.raises(ArchiveError, match="invalid feature id"):
        ship_feature(tmp_path, bad_feature_id, "feature-flag", "---\n---\n")


@pytest.mark.parametrize("bad_capability", ["", "Bad-Slug", "../escape"])
def test_ship_feature_rejects_invalid_capability(tmp_path: Path, bad_capability: str) -> None:
    feature_id = "2026-05-04-ok-id"
    _seed_feature(tmp_path, feature_id, files={"SPEC.md": "# spec\n"})
    with pytest.raises(ArchiveError, match="invalid capability"):
        ship_feature(tmp_path, feature_id, bad_capability, "---\n---\n")


def test_ship_feature_rolls_back_canonical_when_archive_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If archive_feature raises after canonical write, canonical is removed and ArchiveError re-raised."""
    feature_id = "2026-05-04-toggle-add"
    capability = "feature-flag"
    _seed_feature(tmp_path, feature_id, files={"SPEC.md": "# spec\n"})

    def boom(repo_root: Path, fid: str) -> Path:
        raise ArchiveError("simulated archive failure")

    monkeypatch.setattr(archive_mod, "archive_feature", boom)

    with pytest.raises(ArchiveError, match=r"archive failed|simulated archive failure"):
        ship_feature(tmp_path, feature_id, capability, "---\ncapability: feature-flag\n---\n# x\n")

    # Canonical spec rolled back
    canonical = tmp_path / ".forge" / "specs" / capability / "SPEC.md"
    assert not canonical.exists(), "canonical spec should be rolled back when archive fails"
    # Source still present
    assert (tmp_path / ".forge" / "features" / feature_id / "SPEC.md").exists()


def test_ship_feature_pre_archive_hook_runs_against_live_source(tmp_path: Path) -> None:
    """Hook receives the live source path (pre-move) and its writes survive the move."""
    feature_id = "2026-05-04-toggle-add"
    capability = "feature-flag"
    _seed_feature(tmp_path, feature_id, files={"state.json": '{"current_phase":"ship"}\n'})

    captured: list[Path] = []

    def hook(source: Path) -> None:
        captured.append(source)
        # Simulate marking state done before the move.
        (source / "state.json").write_text(
            '{"current_phase":"done"}\n',
            encoding="utf-8",
        )

    canonical, archived = ship_feature(
        tmp_path,
        feature_id,
        capability,
        "---\ncapability: feature-flag\n---\n# x\n",
        pre_archive_hook=hook,
    )

    assert captured == [tmp_path / ".forge" / "features" / feature_id]
    assert canonical.is_file()
    # The hook's mutation traveled with the move.
    assert (archived / "state.json").read_text(encoding="utf-8") == '{"current_phase":"done"}\n'


def test_ship_feature_rolls_back_canonical_when_pre_archive_hook_raises(tmp_path: Path) -> None:
    """A hook failure must trigger canonical rollback and surface the cause."""
    feature_id = "2026-05-04-toggle-add"
    capability = "feature-flag"
    _seed_feature(tmp_path, feature_id, files={"SPEC.md": "# spec\n"})

    def hook(_: Path) -> None:
        raise RuntimeError("state mutation went wrong")

    with pytest.raises(ArchiveError, match=r"pre_archive_hook failed.*state mutation went wrong"):
        ship_feature(
            tmp_path,
            feature_id,
            capability,
            "---\ncapability: feature-flag\n---\n# x\n",
            pre_archive_hook=hook,
        )

    canonical = tmp_path / ".forge" / "specs" / capability / "SPEC.md"
    assert not canonical.exists(), "canonical rolled back on hook failure"
    assert (tmp_path / ".forge" / "features" / feature_id / "SPEC.md").exists()
    assert not (tmp_path / ".forge" / "features" / "archive" / feature_id).exists()


# ---------------------------------------------------------------------------
# v3 live-until-qa lifecycle
# ---------------------------------------------------------------------------


_V3_BODY = (
    "---\ncapability: feature-flag\nstatus: shipped\n"
    "created: 2026-05-09\nlast_updated: 2026-05-09\n"
    "evidence:\n"
    "  - 2026-05-09-toggle-add: features/archive/2026-05-09-toggle-add/\n"
    "bounded_context: null\n---\n# Feature Flag\n"
)


def _v3_state(*, qa_status: str = "pending") -> dict[str, object]:
    """A minimal post-ship v3 state.json payload."""
    return {
        "feature_id": "2026-05-09-toggle-add",
        "tier": "standard",
        "current_phase": "qa",
        "flow_version": 3,
        "phases": {
            "ship": {
                "status": "done",
                "started_at": "2026-05-09T11:00:00Z",
                "completed_at": "2026-05-09T11:15:00Z",
            },
            "qa": {"status": qa_status},
        },
        "skipped": [],
        "deviations": [],
        "commits": [],
        "shipped_at": "2026-05-09T11:15:00Z",
    }


def _v1_state() -> dict[str, object]:
    """A legacy state.json without flow_version (treated as v1)."""
    return {
        "feature_id": "2026-05-09-toggle-add",
        "tier": "standard",
        "current_phase": "ship",
        "phases": {
            "ship": {"status": "in_progress", "started_at": "2026-05-09T11:00:00Z"},
        },
        "skipped": [],
        "deviations": [],
        "commits": [],
    }


def test_ship_v3_feature_does_not_archive_folder(tmp_path: Path) -> None:
    """v3 ship publishes canonical spec and leaves the feature folder in active."""
    feature_id = "2026-05-09-toggle-add"
    capability = "feature-flag"
    _seed_feature(
        tmp_path,
        feature_id,
        files={
            "SPEC.md": "# spec\n",
            "state.json": json.dumps(_v1_state() | {"flow_version": 3}) + "\n",
        },
    )

    canonical, archived = ship_feature(tmp_path, feature_id, capability, _V3_BODY)

    # Canonical spec was written (v3 must publish too).
    assert canonical.is_file()
    assert canonical.read_text(encoding="utf-8") == _V3_BODY
    # Feature folder remains in active dir.
    assert (tmp_path / ".forge" / "features" / feature_id).is_dir()
    # No archive folder yet.
    assert not (tmp_path / ".forge" / "features" / "archive" / feature_id).exists()
    # Returned "archive" path points at the still-live source for v3 callers.
    assert archived == tmp_path / ".forge" / "features" / feature_id


def test_ship_v1_feature_archives_at_ship_legacy(tmp_path: Path) -> None:
    """v1 features (no flow_version) preserve the historical archive-at-ship behavior."""
    feature_id = "2026-05-09-toggle-add"
    capability = "feature-flag"
    _seed_feature(
        tmp_path,
        feature_id,
        files={
            "SPEC.md": "# spec\n",
            "state.json": json.dumps(_v1_state()) + "\n",
        },
    )

    canonical, archived = ship_feature(tmp_path, feature_id, capability, _V3_BODY)

    assert canonical.is_file()
    # Folder moved to archive at ship.
    assert archived == tmp_path / ".forge" / "features" / "archive" / feature_id
    assert archived.is_dir()
    assert not (tmp_path / ".forge" / "features" / feature_id).exists()


def test_v3_ship_publishes_canonical_spec_normally(tmp_path: Path) -> None:
    """Canonical spec publishing runs for v3 just like v1."""
    feature_id = "2026-05-09-toggle-add"
    capability = "feature-flag"
    _seed_feature(
        tmp_path,
        feature_id,
        files={
            "SPEC.md": "# spec\n",
            "state.json": json.dumps(_v1_state() | {"flow_version": 3}) + "\n",
        },
    )

    ship_feature(tmp_path, feature_id, capability, _V3_BODY)

    canonical = tmp_path / ".forge" / "specs" / capability / "SPEC.md"
    assert canonical.is_file()
    assert canonical.read_text(encoding="utf-8") == _V3_BODY


def test_archive_feature_after_qa_v3_moves_folder(tmp_path: Path) -> None:
    """Calling archive_feature_after_qa on a qa-done v3 feature moves the folder."""
    feature_id = "2026-05-09-toggle-add"
    _seed_feature(
        tmp_path,
        feature_id,
        files={
            "SPEC.md": "# spec\n",
            "QA.md": "# qa\n",
            "state.json": json.dumps(_v3_state(qa_status="done")) + "\n",
        },
    )

    result = archive_feature_after_qa(tmp_path, feature_id)

    target = tmp_path / ".forge" / "features" / "archive" / feature_id
    assert target.is_dir()
    assert (target / "SPEC.md").read_text(encoding="utf-8") == "# spec\n"
    assert (target / "QA.md").read_text(encoding="utf-8") == "# qa\n"
    assert not (tmp_path / ".forge" / "features" / feature_id).exists()
    # Result reports the destination.
    assert result == target


def test_archive_feature_after_qa_blocks_if_qa_not_done(tmp_path: Path) -> None:
    """Refuses to archive when phases.qa.status is not done."""
    feature_id = "2026-05-09-toggle-add"
    _seed_feature(
        tmp_path,
        feature_id,
        files={
            "SPEC.md": "# spec\n",
            "state.json": json.dumps(_v3_state(qa_status="pending")) + "\n",
        },
    )

    with pytest.raises(ArchiveError, match="qa"):
        archive_feature_after_qa(tmp_path, feature_id)

    # Folder still in active.
    assert (tmp_path / ".forge" / "features" / feature_id).is_dir()


def test_archive_feature_after_qa_blocks_if_v1_feature(tmp_path: Path) -> None:
    """Refuses to archive a v1 (no flow_version) feature even if qa state is fabricated."""
    feature_id = "2026-05-09-toggle-add"
    payload = _v1_state()
    # Even with fabricated qa-done, a v1 feature is out of scope for this helper.
    phases = payload["phases"]
    assert isinstance(phases, dict)
    phases["qa"] = {"status": "done"}
    _seed_feature(
        tmp_path,
        feature_id,
        files={
            "SPEC.md": "# spec\n",
            "state.json": json.dumps(payload) + "\n",
        },
    )

    with pytest.raises(ArchiveError, match="v3"):
        archive_feature_after_qa(tmp_path, feature_id)

    assert (tmp_path / ".forge" / "features" / feature_id).is_dir()


def test_archive_feature_after_qa_idempotent_when_already_archived(tmp_path: Path) -> None:
    """Calling twice succeeds: second call is a silent no-op when only the archive exists."""
    feature_id = "2026-05-09-toggle-add"
    _seed_feature(
        tmp_path,
        feature_id,
        files={
            "SPEC.md": "# spec\n",
            "state.json": json.dumps(_v3_state(qa_status="done")) + "\n",
        },
    )

    first = archive_feature_after_qa(tmp_path, feature_id)
    second = archive_feature_after_qa(tmp_path, feature_id)

    target = tmp_path / ".forge" / "features" / "archive" / feature_id
    assert first == target
    assert second == target
    assert target.is_dir()
    assert not (tmp_path / ".forge" / "features" / feature_id).exists()


def test_archive_feature_after_qa_collision_raises(tmp_path: Path) -> None:
    """When BOTH source and archive exist, refuse to clobber."""
    feature_id = "2026-05-09-toggle-add"
    _seed_feature(
        tmp_path,
        feature_id,
        files={
            "SPEC.md": "# spec\n",
            "state.json": json.dumps(_v3_state(qa_status="done")) + "\n",
        },
    )
    # Manually place a colliding archive folder.
    archive_target = tmp_path / ".forge" / "features" / "archive" / feature_id
    archive_target.mkdir(parents=True)
    (archive_target / "SPEC.md").write_text("# old\n", encoding="utf-8")

    with pytest.raises(ArchiveError, match="collision"):
        archive_feature_after_qa(tmp_path, feature_id)

    # Both still present; nothing destroyed.
    assert (tmp_path / ".forge" / "features" / feature_id).is_dir()
    assert archive_target.is_dir()
