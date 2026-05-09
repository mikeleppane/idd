"""Tests for state schema v3 bump: harden phase + flow_version + migrate_to_v3.

The v3 generation introduces:

1. ``harden`` as a new lifecycle phase appended to every phase enum in
   ``schemas/state.schema.json`` and ``schemas/budget.schema.json``.
2. An optional top-level ``flow_version`` field on state.json accepting
   ``1 | 2 | 3``. Absence is valid (legacy v1 by application convention).
3. A ``tools.state.migrate_to_v3`` helper that bumps an existing state.json
   to v3 once the feature has shipped, idempotently and atomically.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from tools import state


def _base_state() -> dict[str, Any]:
    """Minimal valid state.json payload (post-ship, no flow_version)."""
    return {
        "feature_id": "2026-05-09-demo-v3",
        "tier": "standard",
        "current_phase": "ship",
        "phases": {
            "spec": {
                "status": "done",
                "started_at": "2026-05-09T10:00:00Z",
                "completed_at": "2026-05-09T10:30:00Z",
            },
            "ship": {
                "status": "done",
                "started_at": "2026-05-09T11:00:00Z",
                "completed_at": "2026-05-09T11:15:00Z",
            },
        },
        "skipped": [],
        "deviations": [],
        "commits": [],
    }


def _validator_for(schema_path: Path) -> jsonschema.Draft202012Validator:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    return jsonschema.Draft202012Validator(
        schema,
        format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER,
    )


def _budget_validator(schema_path: Path) -> jsonschema.Draft7Validator:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    return jsonschema.Draft7Validator(schema)


def _seed_feature(repo_root: Path, payload: dict[str, Any]) -> str:
    """Materialize ``.forge/features/<feature_id>/state.json`` and return id."""
    feature_id = str(payload["feature_id"])
    feature_dir = repo_root / ".forge" / "features" / feature_id
    feature_dir.mkdir(parents=True, exist_ok=True)
    (feature_dir / "state.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return feature_id


# --- Schema-level tests --------------------------------------------------


def test_state_schema_accepts_harden_in_current_phase(schemas_dir: Path) -> None:
    """current_phase enum must include 'harden' so a state.json mid-harden validates."""
    payload = _base_state()
    payload["current_phase"] = "harden"
    payload["phases"]["harden"] = {"status": "in_progress"}
    validator = _validator_for(schemas_dir / "state.schema.json")
    errors = list(validator.iter_errors(payload))
    assert errors == [], f"validator rejected harden current_phase: {errors}"


def test_state_schema_accepts_flow_version_3(schemas_dir: Path) -> None:
    payload = _base_state()
    payload["flow_version"] = 3
    validator = _validator_for(schemas_dir / "state.schema.json")
    errors = list(validator.iter_errors(payload))
    assert errors == [], f"validator rejected flow_version=3: {errors}"


def test_state_schema_rejects_flow_version_4(schemas_dir: Path) -> None:
    payload = _base_state()
    payload["flow_version"] = 4
    validator = _validator_for(schemas_dir / "state.schema.json")
    errors = list(validator.iter_errors(payload))
    assert errors, "validator must reject flow_version outside {1,2,3}"


def test_state_schema_optional_flow_version_passes(schemas_dir: Path) -> None:
    """Backward compat: state.json without flow_version still validates."""
    payload = _base_state()
    assert "flow_version" not in payload
    validator = _validator_for(schemas_dir / "state.schema.json")
    errors = list(validator.iter_errors(payload))
    assert errors == [], f"validator must accept absent flow_version: {errors}"


def test_state_schema_phases_propertynames_includes_harden(schemas_dir: Path) -> None:
    schema = json.loads((schemas_dir / "state.schema.json").read_text(encoding="utf-8"))
    enum = schema["properties"]["phases"]["propertyNames"]["enum"]
    assert "harden" in enum


def test_state_schema_skipped_and_deviations_and_commits_accept_harden(
    schemas_dir: Path,
) -> None:
    """Every per-array phase enum must include 'harden' so post-ship audits write cleanly."""
    payload = _base_state()
    payload["skipped"].append({"phase": "harden", "reason": "library — soak skipped"})
    payload["deviations"].append(
        {
            "phase": "harden",
            "cause": "soak entrypoint missing",
            "resolution": "library — skip",
            "logged_at": "2026-05-09T12:00:00Z",
        }
    )
    payload["commits"].append(
        {
            "sha": "abcdef0",
            "phase": "harden",
            "subject": "feat(harden): record post-ship confidence",
        }
    )
    validator = _validator_for(schemas_dir / "state.schema.json")
    errors = list(validator.iter_errors(payload))
    assert errors == [], f"harden phase rejected by an array enum: {errors}"


def test_budget_schema_accepts_harden_phase(schemas_dir: Path) -> None:
    """budget.schema.json's phase enum must mirror state.schema.json + 'harden'."""
    payload = {
        "phase": "harden",
        "files_in_scope": ["docs/some.md"],
        "forbidden": ["read entire repo"],
    }
    validator = _budget_validator(schemas_dir / "budget.schema.json")
    errors = list(validator.iter_errors(payload))
    assert errors == [], f"budget schema rejected harden: {errors}"


# --- migrate_to_v3 helper tests ------------------------------------------


def test_migrate_to_v3_happy_path_post_ship(tmp_path: Path, schemas_dir: Path) -> None:
    """Feature with phases.ship done and no flow_version → bumps to v3 + adds harden pending."""
    payload = _base_state()
    feature_id = _seed_feature(tmp_path, payload)

    # Sanity: helper exists.
    assert hasattr(state, "migrate_to_v3"), "migrate_to_v3 helper missing"

    updated = state.migrate_to_v3(
        tmp_path, feature_id, schema_path=schemas_dir / "state.schema.json"
    )

    assert updated["flow_version"] == 3
    assert updated["phases"]["harden"] == {"status": "pending"}

    # Persisted to disk.
    on_disk = json.loads(
        (tmp_path / ".forge" / "features" / feature_id / "state.json").read_text(encoding="utf-8")
    )
    assert on_disk["flow_version"] == 3
    assert on_disk["phases"]["harden"] == {"status": "pending"}

    # Result still validates against the schema.
    validator = _validator_for(schemas_dir / "state.schema.json")
    errors = list(validator.iter_errors(on_disk))
    assert errors == [], f"migrated state.json fails schema: {errors}"


def test_migrate_to_v3_idempotent(tmp_path: Path, schemas_dir: Path) -> None:
    """Running migrate_to_v3 twice produces byte-identical state.json."""
    payload = _base_state()
    feature_id = _seed_feature(tmp_path, payload)
    schema_path = schemas_dir / "state.schema.json"

    state.migrate_to_v3(tmp_path, feature_id, schema_path=schema_path)
    first = (tmp_path / ".forge" / "features" / feature_id / "state.json").read_bytes()

    state.migrate_to_v3(tmp_path, feature_id, schema_path=schema_path)
    second = (tmp_path / ".forge" / "features" / feature_id / "state.json").read_bytes()

    assert first == second, "second migration must produce identical bytes"


def test_migrate_to_v3_blocks_when_not_shipped(tmp_path: Path, schemas_dir: Path) -> None:
    """migrate_to_v3 must refuse to bump a feature that has not shipped."""
    payload = _base_state()
    payload["current_phase"] = "execute"
    payload["phases"] = {"execute": {"status": "in_progress"}}
    feature_id = _seed_feature(tmp_path, payload)

    with pytest.raises(state.StateError, match="ship"):
        state.migrate_to_v3(tmp_path, feature_id, schema_path=schemas_dir / "state.schema.json")


def test_migrate_to_v3_already_v3_noop(tmp_path: Path, schemas_dir: Path) -> None:
    """Feature already at flow_version=3 returns unchanged payload, no disk rewrite."""
    payload = _base_state()
    payload["flow_version"] = 3
    payload["phases"]["harden"] = {"status": "pending"}
    feature_id = _seed_feature(tmp_path, payload)

    state_path = tmp_path / ".forge" / "features" / feature_id / "state.json"
    before = state_path.read_bytes()
    before_mtime = state_path.stat().st_mtime_ns

    result = state.migrate_to_v3(
        tmp_path, feature_id, schema_path=schemas_dir / "state.schema.json"
    )
    assert result["flow_version"] == 3

    after = state_path.read_bytes()
    after_mtime = state_path.stat().st_mtime_ns
    assert before == after, "no-op migration must not rewrite disk"
    assert before_mtime == after_mtime, "no-op migration must not touch mtime"
