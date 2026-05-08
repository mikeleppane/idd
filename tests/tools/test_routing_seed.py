"""Tests for ``tools.routing.seed_routed_feature`` (M3 P6.1 T1 contract).

The helper composes :func:`tools.archive.create_feature_folder` (T2) and
:func:`tools.state.record_routing_decision` (P1) with a post-seed cleanup
wrapper backed by :func:`tools.archive.cleanup_seeded_feature` (T0.5).  All
schema validation runs against ``schemas/state.schema.json`` BEFORE any disk
mutation; ``--full`` raises :class:`NotImplementedError` until P6.2 lands.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from tools import routing
from tools.archive import (
    ArchiveError,
    slug_from_idea,
)
from tools.archive import (
    create_feature_folder as real_create_feature_folder,
)
from tools.routing import seed_routed_feature
from tools.state import StateError
from tools.state import record_routing_decision as real_record_routing_decision

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "schemas" / "state.schema.json"

# Pinned today so feature-id assertions are stable across CI clocks.
TODAY = date(2026, 5, 8)


# ---------------------------------------------------------------------------
# Fixture helper
# ---------------------------------------------------------------------------


def _stage_repo(tmp_path: Path) -> Path:
    """Stage a repo_root under tmp_path with the real schema in place.

    ``seed_routed_feature`` resolves ``schema_path`` as
    ``repo_root / "schemas/state.schema.json"`` so we copy the real schema
    next to the seeded ``.forge/features/`` tree.
    """
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
# Happy paths
# ---------------------------------------------------------------------------


def test_seed_routed_feature_focused_happy_path(tmp_path: Path) -> None:
    """Focused tier seed: returns folder path; state.json carries routing block."""
    repo = _stage_repo(tmp_path)

    folder = seed_routed_feature(
        repo,
        idea="add OAuth login flow",
        final_tier="focused",
        proposed_tier="focused",
        rationale="single capability, no new architecture",
        constitution_present=False,
        today=TODAY,
    )

    assert folder == repo / ".forge" / "features" / "2026-05-08-add-oauth-login-flow"
    assert folder.is_dir()
    assert (folder / "state.json").is_file()
    assert (folder / "SPEC.md").is_file()
    assert (folder / "decisions.md").is_file()

    payload = _read_state(folder)
    assert payload["tier"] == "focused"
    assert payload["current_phase"] == "spec"
    assert payload["phases"]["spec"]["status"] == "in_progress"
    assert payload["routing"]["idea"] == "add OAuth login flow"
    assert payload["routing"]["final_tier"] == "focused"
    assert payload["routing"]["proposed_tier"] == "focused"
    assert payload["routing"]["rationale"] == "single capability, no new architecture"
    assert payload["routing"]["constitution_present"] is False
    assert "decided_at" in payload["routing"]


def test_seed_routed_feature_standard_happy_path(tmp_path: Path) -> None:
    """Standard tier seed: same shape with final_tier='standard'."""
    repo = _stage_repo(tmp_path)

    folder = seed_routed_feature(
        repo,
        idea="multi-tenant billing pipeline",
        final_tier="standard",
        constitution_present=True,
        today=TODAY,
    )

    payload = _read_state(folder)
    assert payload["tier"] == "standard"
    assert payload["routing"]["final_tier"] == "standard"
    assert payload["routing"]["constitution_present"] is True
    # Optional fields absent when not passed.
    assert "proposed_tier" not in payload["routing"]
    assert "rationale" not in payload["routing"]


# ---------------------------------------------------------------------------
# --full and bogus tier — both refuse BEFORE any disk mutation
# ---------------------------------------------------------------------------


def test_seed_routed_feature_full_raises_not_implemented(tmp_path: Path) -> None:
    """--full path raises NotImplementedError with a P6.2 pointer; no folder."""
    repo = _stage_repo(tmp_path)

    with pytest.raises(NotImplementedError) as excinfo:
        seed_routed_feature(
            repo,
            idea="rebuild the orchestrator from scratch",
            final_tier="full",
            today=TODAY,
        )

    assert "P6.2" in str(excinfo.value)
    # No folder should exist anywhere under .forge/features/.
    features_root = repo / ".forge" / "features"
    if features_root.exists():
        # Guard: if pytest fixtures or another test ever pre-create this dir,
        # we still want to assert nothing was seeded by THIS call.
        assert not any(features_root.iterdir()), "no folder may be seeded when --full raises"


def test_seed_routed_feature_bogus_tier_raises_value_error(tmp_path: Path) -> None:
    """Unknown tier raises ValueError; no folder created."""
    repo = _stage_repo(tmp_path)

    with pytest.raises(ValueError) as excinfo:
        seed_routed_feature(
            repo,
            idea="something different",
            final_tier="weird",
            today=TODAY,
        )

    assert "weird" in str(excinfo.value)
    features_root = repo / ".forge" / "features"
    assert not features_root.exists() or not any(features_root.iterdir())


# ---------------------------------------------------------------------------
# Slug derivation + collision
# ---------------------------------------------------------------------------


def test_seed_routed_feature_slug_derives_from_idea(tmp_path: Path) -> None:
    """feature_id ends with slug_from_idea(idea) for the given idea."""
    repo = _stage_repo(tmp_path)
    idea = "add OAuth login flow"
    expected_slug = slug_from_idea(idea)

    folder = seed_routed_feature(
        repo,
        idea=idea,
        final_tier="focused",
        today=TODAY,
    )

    assert folder.name == f"2026-05-08-{expected_slug}"


def test_seed_routed_feature_collision_raises(tmp_path: Path) -> None:
    """Pre-existing folder triggers ArchiveError before any mutation attempt."""
    repo = _stage_repo(tmp_path)
    idea = "add OAuth login flow"
    feature_id = f"2026-05-08-{slug_from_idea(idea)}"
    pre_existing = repo / ".forge" / "features" / feature_id
    pre_existing.mkdir(parents=True)
    sentinel = pre_existing / "sentinel.txt"
    sentinel.write_text("pre-existing", encoding="utf-8")

    with pytest.raises(ArchiveError) as excinfo:
        seed_routed_feature(
            repo,
            idea=idea,
            final_tier="focused",
            today=TODAY,
        )

    assert feature_id in str(excinfo.value)
    # Pre-existing folder + sentinel are untouched.
    assert sentinel.is_file()
    assert sentinel.read_text(encoding="utf-8") == "pre-existing"


# ---------------------------------------------------------------------------
# Post-seed cleanup wrapper — record_routing_decision failure path
# ---------------------------------------------------------------------------


def test_seed_routed_feature_post_seed_cleanup_on_record_routing_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """record_routing_decision failure triggers cleanup_seeded_feature; no folder left."""
    repo = _stage_repo(tmp_path)

    def _boom(*args: Any, **kwargs: Any) -> None:
        raise StateError("simulated routing-block schema rejection")

    # Patch the symbol in the routing module's namespace — that is the
    # binding seed_routed_feature actually calls.
    monkeypatch.setattr(routing, "record_routing_decision", _boom)

    with pytest.raises(StateError, match="simulated routing-block"):
        seed_routed_feature(
            repo,
            idea="will fail at routing block",
            final_tier="focused",
            today=TODAY,
        )

    # The seeded folder must be gone after cleanup_seeded_feature ran.
    features_root = repo / ".forge" / "features"
    assert not features_root.exists() or not any(features_root.iterdir())


def test_seed_routed_feature_keyboard_interrupt_during_record_routing_cleans_folder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """KeyboardInterrupt mid-record_routing also triggers cleanup; original re-raises."""
    repo = _stage_repo(tmp_path)

    def _interrupt(*args: Any, **kwargs: Any) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(routing, "record_routing_decision", _interrupt)

    with pytest.raises(KeyboardInterrupt):
        seed_routed_feature(
            repo,
            idea="cancelled mid-flight",
            final_tier="standard",
            today=TODAY,
        )

    features_root = repo / ".forge" / "features"
    assert not features_root.exists() or not any(features_root.iterdir())


def test_seed_routed_feature_cleanup_failure_suppressed_original_reraises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If cleanup_seeded_feature itself raises, the original exception still re-raises."""
    repo = _stage_repo(tmp_path)

    def _boom(*args: Any, **kwargs: Any) -> None:
        raise StateError("original routing failure")

    def _cleanup_raises(*args: Any, **kwargs: Any) -> bool:
        raise RuntimeError("cleanup blew up")

    monkeypatch.setattr(routing, "record_routing_decision", _boom)
    monkeypatch.setattr(routing, "cleanup_seeded_feature", _cleanup_raises)

    # The ORIGINAL StateError must propagate, not the RuntimeError from cleanup.
    with pytest.raises(StateError, match="original routing failure"):
        seed_routed_feature(
            repo,
            idea="cleanup will fail too",
            final_tier="focused",
            today=TODAY,
        )


def test_cleanup_failure_with_baseexception_preserves_original(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """If cleanup raises a BaseException (e.g. KeyboardInterrupt mid-rmtree),
    the ORIGINAL ``record_routing_decision`` exception must still propagate
    and a one-line WARN must hit stderr.

    Locks remediation for M3 P6.1 T7 finding p6-1-M3 — without the explicit
    BaseException catch around cleanup, a KeyboardInterrupt during rmtree
    would mask the underlying StateError.
    """
    repo = _stage_repo(tmp_path)

    def _boom(*args: Any, **kwargs: Any) -> None:
        raise StateError("original routing failure")

    def _cleanup_kbd_interrupt(*args: Any, **kwargs: Any) -> bool:
        raise KeyboardInterrupt

    monkeypatch.setattr(routing, "record_routing_decision", _boom)
    monkeypatch.setattr(routing, "cleanup_seeded_feature", _cleanup_kbd_interrupt)

    with pytest.raises(StateError, match="original routing failure"):
        seed_routed_feature(
            repo,
            idea="kbd interrupt during cleanup",
            final_tier="focused",
            today=TODAY,
        )

    captured = capsys.readouterr()
    assert "cleanup_seeded_feature raised during post-seed rollback" in captured.err, (
        "stderr must carry the WARN line so operators can correlate the "
        "rollback failure with the original record_routing_decision exception"
    )


# ---------------------------------------------------------------------------
# today injection determinism + schema_path wiring + final routing block shape
# ---------------------------------------------------------------------------


def test_seed_routed_feature_today_injection_determinism(tmp_path: Path) -> None:
    """today=date(2026,5,8) → folder path == .forge/features/2026-05-08-<slug>."""
    repo = _stage_repo(tmp_path)
    idea = "add OAuth login flow"

    folder = seed_routed_feature(
        repo,
        idea=idea,
        final_tier="focused",
        today=date(2026, 5, 8),
    )

    expected_slug = slug_from_idea(idea)
    assert folder == repo / ".forge" / "features" / f"2026-05-08-{expected_slug}"


def test_seed_routed_feature_schema_path_passed_to_both_helpers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both create_feature_folder and record_routing_decision get schema_path."""
    repo = _stage_repo(tmp_path)
    expected_schema = repo / "schemas" / "state.schema.json"
    captured: dict[str, Path | None] = {}

    def _spy_create(repo_root: Path, **kwargs: Any) -> Path:
        captured["create_schema_path"] = kwargs.get("schema_path")
        return real_create_feature_folder(repo_root, **kwargs)

    def _spy_record(state_path: Path, **kwargs: Any) -> dict[str, Any]:
        captured["record_schema_path"] = kwargs.get("schema_path")
        return real_record_routing_decision(state_path, **kwargs)

    monkeypatch.setattr(routing, "create_feature_folder", _spy_create)
    monkeypatch.setattr(routing, "record_routing_decision", _spy_record)

    seed_routed_feature(
        repo,
        idea="check both schema paths",
        final_tier="focused",
        today=TODAY,
    )

    assert captured["create_schema_path"] == expected_schema
    assert captured["record_schema_path"] == expected_schema


def test_seed_routed_feature_routing_block_complete_after_return(tmp_path: Path) -> None:
    """state.json.routing carries required + optional fields per spec."""
    repo = _stage_repo(tmp_path)

    folder = seed_routed_feature(
        repo,
        idea="capture full routing block",
        final_tier="focused",
        proposed_tier="standard",
        rationale="user downgraded",
        constitution_present=True,
        today=TODAY,
    )
    payload = _read_state(folder)
    routing_block = payload["routing"]

    # Required by schemas/state.schema.json (routing.required).
    assert "idea" in routing_block
    assert "final_tier" in routing_block
    assert "decided_at" in routing_block
    # Tracked optionals — present because we passed them.
    assert routing_block["proposed_tier"] == "standard"
    assert routing_block["rationale"] == "user downgraded"
    assert routing_block["constitution_present"] is True
