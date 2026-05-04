"""Archival + canonical-spec write — pure file ops with explicit failure modes."""
from __future__ import annotations

from pathlib import Path

import pytest

from tools.archive import (
    ArchiveError,
    archive_feature,
    canonical_spec_path,
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
    assert canonical_spec_path(tmp_path, "feature-flag") == tmp_path / ".idd" / "specs" / "feature-flag" / "SPEC.md"


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
