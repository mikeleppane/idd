"""Tests for create_feature_folder.

Composes existing templates/feature/ files (state.json, SPEC.md,
decisions.md) into a fresh .forge/features/<feature_id>/ folder.  Per-file
write is atomic via tempfile + os.replace; the multi-file folder seed is
best-effort, not transactional — on any per-file failure, shutil.rmtree
removes the partial folder before re-raising.

State body shape locked by spec §5.3.2 step 6:
  - feature_id, tier
  - current_phase = "spec"
  - phases.spec = {status: "in_progress", started_at: <utc-iso>}
  - skipped = [{phase: "research", reason: "research deferred; manual research acceptable"}]
  - deviations = []
  - commits = []
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from tools import archive as archive_mod
from tools import constitution_amend
from tools.archive import ArchiveError, create_feature_folder
from tools.state import StateError

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "schemas" / "state.schema.json"
TEMPLATE_DECISIONS = REPO_ROOT / "templates" / "feature" / "decisions.md"

ISO8601_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|\+00:00)$")


def _read_state(folder: Path) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads((folder / "state.json").read_text(encoding="utf-8"))
    return payload


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_create_feature_folder_focused_success(tmp_path: Path) -> None:
    """Focused tier seed: folder + 3 files exist; state validates against schema."""
    feature_id = "2026-05-08-focused-happy"

    folder = create_feature_folder(
        tmp_path,
        feature_id=feature_id,
        tier="focused",
        schema_path=SCHEMA_PATH,
    )

    assert folder == tmp_path / ".forge" / "features" / feature_id
    assert folder.is_dir()
    assert (folder / "state.json").is_file()
    assert (folder / "SPEC.md").is_file()
    assert (folder / "decisions.md").is_file()

    payload = _read_state(folder)
    assert payload["feature_id"] == feature_id
    assert payload["tier"] == "focused"
    assert payload["current_phase"] == "spec"


def test_create_feature_folder_standard_success(tmp_path: Path) -> None:
    """Standard tier seed: same shape with tier='standard'."""
    feature_id = "2026-05-08-standard-happy"

    folder = create_feature_folder(
        tmp_path,
        feature_id=feature_id,
        tier="standard",
        schema_path=SCHEMA_PATH,
    )

    assert folder.is_dir()
    payload = _read_state(folder)
    assert payload["tier"] == "standard"
    assert payload["current_phase"] == "spec"


# ---------------------------------------------------------------------------
# Refusal paths
# ---------------------------------------------------------------------------


def test_create_feature_folder_collision_raises(tmp_path: Path) -> None:
    """Pre-create the folder → ArchiveError."""
    feature_id = "2026-05-08-collision"
    existing = tmp_path / ".forge" / "features" / feature_id
    existing.mkdir(parents=True)

    with pytest.raises(ArchiveError, match="feature folder already exists"):
        create_feature_folder(
            tmp_path,
            feature_id=feature_id,
            tier="focused",
            schema_path=SCHEMA_PATH,
        )


def test_create_feature_folder_toctou_race_wraps_file_exists_as_archive_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """L3: a folder created between feature_folder_exists() and mkdir()
    triggers FileExistsError. The helper must wrap it as ArchiveError so
    callers see a consistent exception type, not a raw FileExistsError.
    """
    feature_id = "2026-05-08-toctou-race"

    # Simulate the race: collision check returns False (folder doesn't
    # exist), then a concurrent process creates it before our mkdir runs.
    def _fake_exists(_repo_root: Path, _fid: str) -> bool:
        return False

    monkeypatch.setattr(archive_mod, "feature_folder_exists", _fake_exists)

    # Pre-create the folder so the actual mkdir fails with FileExistsError.
    (tmp_path / ".forge" / "features" / feature_id).mkdir(parents=True)

    with pytest.raises(ArchiveError, match=r"race detected"):
        create_feature_folder(
            tmp_path,
            feature_id=feature_id,
            tier="focused",
            schema_path=SCHEMA_PATH,
        )


def test_create_feature_folder_invalid_tier_raises(tmp_path: Path) -> None:
    """Bogus tier → ArchiveError; no folder created."""
    feature_id = "2026-05-08-bad-tier"
    with pytest.raises(ArchiveError, match="invalid tier"):
        create_feature_folder(
            tmp_path,
            feature_id=feature_id,
            tier="weird",
            schema_path=SCHEMA_PATH,
        )
    assert not (tmp_path / ".forge" / "features" / feature_id).exists()


def test_create_feature_folder_invalid_feature_id_raises(tmp_path: Path) -> None:
    """Bad slug → ArchiveError from _validate_feature_id."""
    with pytest.raises(ArchiveError, match="invalid feature id"):
        create_feature_folder(
            tmp_path,
            feature_id="2026-13-99-bad-date",  # invalid month/day
            tier="focused",
            schema_path=SCHEMA_PATH,
        )


def test_create_feature_folder_partial_failure_rmtrees(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SPEC.md write fails mid-way → folder removed; original exception propagates."""
    feature_id = "2026-05-08-partial-fail"

    real_atomic_replace = constitution_amend.atomic_replace
    calls: dict[str, int] = {"count": 0}

    def flaky_atomic_replace(target: Path, body: str) -> None:
        calls["count"] += 1
        if target.name == "SPEC.md":
            raise OSError("simulated mid-write failure")
        real_atomic_replace(target, body)

    # Patch the binding inside tools.archive (where it's `from ... import`-ed)
    # so create_feature_folder picks up the flaky version without disturbing
    # the original tools.constitution_amend.atomic_replace.  setattr-by-string
    # avoids mypy's strict "explicit-export" complaint about
    # ``archive.atomic_replace`` access.
    monkeypatch.setattr("tools.archive.atomic_replace", flaky_atomic_replace, raising=True)

    with pytest.raises(OSError, match="simulated mid-write failure"):
        create_feature_folder(
            tmp_path,
            feature_id=feature_id,
            tier="focused",
            schema_path=SCHEMA_PATH,
        )

    folder = tmp_path / ".forge" / "features" / feature_id
    assert not folder.exists(), "partial folder must be rmtree'd on per-file failure"


# ---------------------------------------------------------------------------
# Seed-body shape
# ---------------------------------------------------------------------------


def test_create_feature_folder_research_skipped_entry_present(tmp_path: Path) -> None:
    """skipped contains the research entry verbatim."""
    feature_id = "2026-05-08-research-skip"
    create_feature_folder(
        tmp_path,
        feature_id=feature_id,
        tier="focused",
        schema_path=SCHEMA_PATH,
    )
    payload = _read_state(tmp_path / ".forge" / "features" / feature_id)
    assert payload["skipped"] == [
        {"phase": "research", "reason": "research deferred; manual research acceptable"}
    ]


def test_create_feature_folder_phase_status_in_progress(tmp_path: Path) -> None:
    """phases.spec.status == 'in_progress'."""
    feature_id = "2026-05-08-phase-status"
    create_feature_folder(
        tmp_path,
        feature_id=feature_id,
        tier="focused",
        schema_path=SCHEMA_PATH,
    )
    payload = _read_state(tmp_path / ".forge" / "features" / feature_id)
    assert payload["phases"]["spec"]["status"] == "in_progress"


def test_create_feature_folder_started_at_iso_8601_utc(tmp_path: Path) -> None:
    """phases.spec.started_at is ISO 8601 UTC (matches state schema date-time)."""
    feature_id = "2026-05-08-iso-stamp"
    create_feature_folder(
        tmp_path,
        feature_id=feature_id,
        tier="focused",
        schema_path=SCHEMA_PATH,
    )
    payload = _read_state(tmp_path / ".forge" / "features" / feature_id)
    started_at = payload["phases"]["spec"]["started_at"]
    assert isinstance(started_at, str)
    assert ISO8601_UTC_RE.match(started_at), f"not ISO 8601 UTC: {started_at!r}"


# ---------------------------------------------------------------------------
# Template substitution + return value
# ---------------------------------------------------------------------------


def test_create_feature_folder_spec_md_substitutions(tmp_path: Path) -> None:
    """SPEC.md frontmatter has feature_id/tier/created/capability with no placeholders left."""
    feature_id = "2026-05-08-spec-subst"
    create_feature_folder(
        tmp_path,
        feature_id=feature_id,
        tier="standard",
        schema_path=SCHEMA_PATH,
    )
    spec_text = (tmp_path / ".forge" / "features" / feature_id / "SPEC.md").read_text(
        encoding="utf-8"
    )
    assert f"id: {feature_id}" in spec_text
    assert "tier: standard" in spec_text
    assert "created: 2026-05-08" in spec_text
    assert "capability: spec-subst" in spec_text
    # No raw template placeholders remain in the frontmatter block.
    frontmatter_end = spec_text.find("\n---\n", 4)
    assert frontmatter_end > 0
    frontmatter = spec_text[:frontmatter_end]
    assert "<YYYY-MM-DD-slug>" not in frontmatter
    assert "<focused|standard|full>" not in frontmatter
    assert "<YYYY-MM-DD>" not in frontmatter
    assert "<stable-capability-handle>" not in frontmatter


def test_create_feature_folder_returns_folder_path(tmp_path: Path) -> None:
    """Return value matches repo_root/.forge/features/<feature_id>."""
    feature_id = "2026-05-08-return-path"
    folder = create_feature_folder(
        tmp_path,
        feature_id=feature_id,
        tier="focused",
        schema_path=SCHEMA_PATH,
    )
    assert folder == tmp_path / ".forge" / "features" / feature_id
    assert folder.is_dir()


def test_create_feature_folder_schema_path_refuses_invalid_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Forced bogus timestamp → write_state raises StateError; folder removed."""
    feature_id = "2026-05-08-bad-stamp"

    monkeypatch.setattr("tools.archive._utc_now_iso", lambda: "not-a-date-time")

    with pytest.raises(StateError, match="fails schema"):
        create_feature_folder(
            tmp_path,
            feature_id=feature_id,
            tier="focused",
            schema_path=SCHEMA_PATH,
        )

    folder = tmp_path / ".forge" / "features" / feature_id
    assert not folder.exists(), "schema refusal must leave no folder behind"


def test_create_feature_folder_decisions_md_copied_byte_for_byte(tmp_path: Path) -> None:
    """decisions.md matches templates/feature/decisions.md byte-for-byte."""
    feature_id = "2026-05-08-decisions-copy"
    create_feature_folder(
        tmp_path,
        feature_id=feature_id,
        tier="focused",
        schema_path=SCHEMA_PATH,
    )
    written = (tmp_path / ".forge" / "features" / feature_id / "decisions.md").read_bytes()
    expected = TEMPLATE_DECISIONS.read_bytes()
    assert written == expected


# ---------------------------------------------------------------------------
# current_phase kwarg — full-tier refine seed
# ---------------------------------------------------------------------------


def test_create_feature_folder_full_tier_refine_seed_success(tmp_path: Path) -> None:
    """Full tier + current_phase='refine' → seed validates; refine block in_progress."""
    feature_id = "2026-05-08-full-refine"

    folder = create_feature_folder(
        tmp_path,
        feature_id=feature_id,
        tier="full",
        current_phase="refine",
        schema_path=SCHEMA_PATH,
    )

    assert folder.is_dir()
    payload = _read_state(folder)
    assert payload["current_phase"] == "refine"
    assert payload["tier"] == "full"
    assert payload["phases"]["refine"]["status"] == "in_progress"
    started_at = payload["phases"]["refine"]["started_at"]
    assert isinstance(started_at, str)
    assert ISO8601_UTC_RE.match(started_at), f"not ISO 8601 UTC: {started_at!r}"


def test_create_feature_folder_refine_with_focused_tier_raises(tmp_path: Path) -> None:
    """current_phase='refine' + tier='focused' → ArchiveError; no folder."""
    feature_id = "2026-05-08-refine-focused"
    with pytest.raises(ArchiveError, match="current_phase 'refine' requires tier 'full'"):
        create_feature_folder(
            tmp_path,
            feature_id=feature_id,
            tier="focused",
            current_phase="refine",
            schema_path=SCHEMA_PATH,
        )
    assert not (tmp_path / ".forge" / "features" / feature_id).exists()


def test_create_feature_folder_refine_with_standard_tier_raises(tmp_path: Path) -> None:
    """current_phase='refine' + tier='standard' → ArchiveError; no folder."""
    feature_id = "2026-05-08-refine-standard"
    with pytest.raises(ArchiveError, match="current_phase 'refine' requires tier 'full'"):
        create_feature_folder(
            tmp_path,
            feature_id=feature_id,
            tier="standard",
            current_phase="refine",
            schema_path=SCHEMA_PATH,
        )
    assert not (tmp_path / ".forge" / "features" / feature_id).exists()


@pytest.mark.parametrize("bad_phase", ["plan", "execute", ""])
def test_create_feature_folder_invalid_current_phase_raises(tmp_path: Path, bad_phase: str) -> None:
    """current_phase outside {'spec','refine'} → ArchiveError; no folder."""
    feature_id = f"2026-05-08-bad-phase-{bad_phase or 'empty'}"
    with pytest.raises(ArchiveError, match="invalid current_phase"):
        create_feature_folder(
            tmp_path,
            feature_id=feature_id,
            tier="full",
            current_phase=bad_phase,
            schema_path=SCHEMA_PATH,
        )
    assert not (tmp_path / ".forge" / "features" / feature_id).exists()


def test_create_feature_folder_default_current_phase_is_spec(tmp_path: Path) -> None:
    """Calling without current_phase= defaults to spec entry: phases.spec block."""
    feature_id = "2026-05-08-default-spec"
    create_feature_folder(
        tmp_path,
        feature_id=feature_id,
        tier="focused",
        schema_path=SCHEMA_PATH,
    )
    payload = _read_state(tmp_path / ".forge" / "features" / feature_id)
    assert payload["current_phase"] == "spec"
    assert "spec" in payload["phases"]
    assert "refine" not in payload["phases"]


def test_create_feature_folder_full_tier_refine_phase_block_only_has_refine(
    tmp_path: Path,
) -> None:
    """Full-tier refine seed: phases dict has exactly {'refine'} — no leaked spec."""
    feature_id = "2026-05-08-refine-only"
    create_feature_folder(
        tmp_path,
        feature_id=feature_id,
        tier="full",
        current_phase="refine",
        schema_path=SCHEMA_PATH,
    )
    payload = _read_state(tmp_path / ".forge" / "features" / feature_id)
    assert set(payload["phases"].keys()) == {"refine"}


def test_create_feature_folder_appends_gitignore_managed_block(tmp_path: Path) -> None:
    """First feature seed appends a FORGE-managed block to existing .gitignore."""
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text(".venv/\n", encoding="utf-8")
    create_feature_folder(
        tmp_path,
        feature_id="2026-05-12-gitignore-append",
        tier="focused",
        schema_path=SCHEMA_PATH,
    )
    body = gitignore.read_text(encoding="utf-8")
    assert "# === BEGIN FORGE managed ===" in body
    assert ".forge/**/*.lock" in body
    assert ".forge/state/*.log" in body
    assert ".venv/" in body  # existing content preserved


def test_create_feature_folder_gitignore_append_is_idempotent(tmp_path: Path) -> None:
    """Re-seeding does not re-append the managed block."""
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text(".venv/\n", encoding="utf-8")
    create_feature_folder(
        tmp_path,
        feature_id="2026-05-12-first-seed",
        tier="focused",
        schema_path=SCHEMA_PATH,
    )
    first = gitignore.read_text(encoding="utf-8")
    create_feature_folder(
        tmp_path,
        feature_id="2026-05-12-second-seed",
        tier="focused",
        schema_path=SCHEMA_PATH,
    )
    second = gitignore.read_text(encoding="utf-8")
    assert first == second
    assert second.count("# === BEGIN FORGE managed ===") == 1


def test_create_feature_folder_gitignore_absent_is_noop(tmp_path: Path) -> None:
    """No .gitignore on disk: helper does not create one (user opted out of git)."""
    create_feature_folder(
        tmp_path,
        feature_id="2026-05-12-no-gitignore",
        tier="focused",
        schema_path=SCHEMA_PATH,
    )
    assert not (tmp_path / ".gitignore").exists()


def test_create_feature_folder_gitignore_with_forge_wildcard_skips_managed_block(
    tmp_path: Path,
) -> None:
    """Repos that already ignore .forge/ wholesale do not need the managed block."""
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("/.forge/*\n", encoding="utf-8")
    create_feature_folder(
        tmp_path,
        feature_id="2026-05-12-wildcard-ignore",
        tier="focused",
        schema_path=SCHEMA_PATH,
    )
    body = gitignore.read_text(encoding="utf-8")
    assert "# === BEGIN FORGE managed ===" not in body
    assert "/.forge/*" in body


def test_create_feature_folder_coerces_string_repo_root(tmp_path: Path) -> None:
    """A string repo_root must not trip ``TypeError`` on ``str / ".forge"``.

    Mirrors the pattern locked into ``tools.bdd_detect.detect`` — the
    ``/forge:do`` seed entry point sees agent-improvised call shapes
    where ``repo_root`` is occasionally a string. A cryptic operator
    failure four frames deep is worse than a clean ``Path`` coercion at
    the entry boundary.
    """
    feature_dir = create_feature_folder(
        str(tmp_path),
        feature_id="2026-05-12-string-coercion",
        tier="focused",
        schema_path=SCHEMA_PATH,
    )

    assert isinstance(feature_dir, Path)
    assert feature_dir.is_dir()
    assert (feature_dir / "state.json").is_file()
