"""Archival + canonical-spec write — pure file ops with explicit failure modes."""

from __future__ import annotations

from pathlib import Path

import pytest

import tools.archive as archive_mod
from tools.archive import (
    ArchiveError,
    archive_feature,
    canonical_spec_path,
    ship_feature,
    write_canonical_spec,
)


def _seed_feature(repo_root: Path, feature_id: str, *, files: dict[str, str]) -> Path:
    feature_dir = repo_root / ".idd" / "features" / feature_id
    feature_dir.mkdir(parents=True)
    for name, body in files.items():
        (feature_dir / name).write_text(body, encoding="utf-8")
    return feature_dir


def test_archive_feature_moves_folder_under_features_archive(tmp_path: Path) -> None:
    feature_id = "2026-05-04-toggle-add"
    _seed_feature(tmp_path, feature_id, files={"SPEC.md": "# spec\n", "state.json": "{}\n"})

    archived = archive_feature(tmp_path, feature_id)

    assert archived == tmp_path / ".idd" / "features" / "archive" / feature_id
    assert archived.is_dir()
    assert (archived / "SPEC.md").read_text(encoding="utf-8") == "# spec\n"
    assert not (tmp_path / ".idd" / "features" / feature_id).exists()


def test_archive_feature_refuses_when_target_exists(tmp_path: Path) -> None:
    feature_id = "2026-05-04-toggle-add"
    _seed_feature(tmp_path, feature_id, files={"SPEC.md": "# spec\n"})
    (tmp_path / ".idd" / "features" / "archive" / feature_id).mkdir(parents=True)

    with pytest.raises(ArchiveError, match="already archived"):
        archive_feature(tmp_path, feature_id)


def test_archive_feature_missing_source_raises(tmp_path: Path) -> None:
    with pytest.raises(ArchiveError, match="not found"):
        archive_feature(tmp_path, "2026-05-04-missing")


def test_canonical_spec_path_uses_specs_capability_spec(tmp_path: Path) -> None:
    assert (
        canonical_spec_path(tmp_path, "feature-flag")
        == tmp_path / ".idd" / "specs" / "feature-flag" / "SPEC.md"
    )


def test_write_canonical_spec_creates_folder_and_file(tmp_path: Path) -> None:
    body = "---\ncapability: feature-flag\nstatus: shipped\n---\n# Feature Flag\n"

    written = write_canonical_spec(tmp_path, "feature-flag", body)

    assert written == tmp_path / ".idd" / "specs" / "feature-flag" / "SPEC.md"
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


@pytest.mark.parametrize("bad", ["", "bad-id", "2026-13-99-x", "../escape"])
def test_invalid_feature_id_rejected(tmp_path: Path, bad: str) -> None:
    with pytest.raises(ArchiveError, match="invalid feature id"):
        archive_feature(tmp_path, bad)


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

    assert canonical == tmp_path / ".idd" / "specs" / capability / "SPEC.md"
    assert canonical.read_text(encoding="utf-8") == body
    assert archive_path == tmp_path / ".idd" / "features" / "archive" / feature_id
    assert (archive_path / "SPEC.md").read_text(encoding="utf-8") == "# spec\n"
    assert not (tmp_path / ".idd" / "features" / feature_id).exists()


def test_ship_feature_refuses_when_canonical_already_exists(tmp_path: Path) -> None:
    feature_id = "2026-05-04-toggle-add"
    capability = "feature-flag"
    _seed_feature(tmp_path, feature_id, files={"SPEC.md": "# spec\n"})
    canonical = tmp_path / ".idd" / "specs" / capability / "SPEC.md"
    canonical.parent.mkdir(parents=True)
    canonical.write_text("existing\n", encoding="utf-8")

    with pytest.raises(ArchiveError, match=r"already exists|already shipped"):
        ship_feature(tmp_path, feature_id, capability, "---\n---\n")

    # Source untouched
    assert (tmp_path / ".idd" / "features" / feature_id / "SPEC.md").exists()
    # Canonical untouched (still says "existing")
    assert canonical.read_text(encoding="utf-8") == "existing\n"
    # No archive created
    assert not (tmp_path / ".idd" / "features" / "archive" / feature_id).exists()


def test_ship_feature_refuses_when_archive_target_exists(tmp_path: Path) -> None:
    feature_id = "2026-05-04-toggle-add"
    capability = "feature-flag"
    _seed_feature(tmp_path, feature_id, files={"SPEC.md": "# spec\n"})
    (tmp_path / ".idd" / "features" / "archive" / feature_id).mkdir(parents=True)

    with pytest.raises(ArchiveError, match="already archived"):
        ship_feature(tmp_path, feature_id, capability, "---\n---\n")

    # No canonical written
    assert not (tmp_path / ".idd" / "specs" / capability / "SPEC.md").exists()
    # Source untouched
    assert (tmp_path / ".idd" / "features" / feature_id / "SPEC.md").exists()


def test_ship_feature_refuses_when_source_missing(tmp_path: Path) -> None:
    with pytest.raises(ArchiveError, match="not found"):
        ship_feature(tmp_path, "2026-05-04-missing", "feature-flag", "---\n---\n")
    # No canonical written even though it would have been writable
    assert not (tmp_path / ".idd" / "specs" / "feature-flag" / "SPEC.md").exists()


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
    canonical = tmp_path / ".idd" / "specs" / capability / "SPEC.md"
    assert not canonical.exists(), "canonical spec should be rolled back when archive fails"
    # Source still present
    assert (tmp_path / ".idd" / "features" / feature_id / "SPEC.md").exists()


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

    assert captured == [tmp_path / ".idd" / "features" / feature_id]
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

    canonical = tmp_path / ".idd" / "specs" / capability / "SPEC.md"
    assert not canonical.exists(), "canonical rolled back on hook failure"
    assert (tmp_path / ".idd" / "features" / feature_id / "SPEC.md").exists()
    assert not (tmp_path / ".idd" / "features" / "archive" / feature_id).exists()
