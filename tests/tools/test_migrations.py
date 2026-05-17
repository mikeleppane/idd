"""Focused tests for schema-version migration behavior."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
import yaml

from tools.lint_frontmatter import validate_file
from tools.migrations import registry as migration_registry
from tools.migrations import v1_noop
from tools.migrations.registry import Migration, apply_pending, register

_V1_ANCHOR = v1_noop


@pytest.fixture(autouse=True)
def _restore_registry() -> Generator[None]:
    original = dict(migration_registry.REGISTRY)
    try:
        yield
    finally:
        migration_registry.REGISTRY.clear()
        migration_registry.REGISTRY.update(original)


def _spec_frontmatter(schema_version: int | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": "2026-05-17-migration-tests",
        "status": "draft",
        "tier": "focused",
        "created": "2026-05-17",
        "capability": "migration-tests",
    }
    if schema_version is not None:
        payload["schema_version"] = schema_version
    return payload


def _write_markdown(path: Path, frontmatter: dict[str, Any], body: str = "# Title\n") -> None:
    path.write_text(
        f"---\n{yaml.safe_dump(frontmatter, sort_keys=False)}---\n{body}",
        encoding="utf-8",
    )


def _write_active_feature(repo_root: Path) -> Path:
    feature_id = "2026-05-17-migration-tests"
    feature_folder = repo_root / ".forge" / "features" / feature_id
    feature_folder.mkdir(parents=True)
    state = {
        "feature_id": feature_id,
        "tier": "focused",
        "current_phase": "spec",
        "phases": {"spec": {"status": "in_progress"}},
        "skipped": [],
        "deviations": [],
        "commits": [],
    }
    (feature_folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    return feature_folder


def _run_migrate(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path.cwd())
    return subprocess.run(
        [sys.executable, "-m", "tools.state_cli", "--repo-root", str(repo_root), "migrate", *args],
        check=False,
        cwd=Path.cwd(),
        env=env,
        capture_output=True,
        text=True,
    )


def test_v1_identity_idempotent() -> None:
    doc = _spec_frontmatter(schema_version=1)

    once = apply_pending("spec", doc)
    twice = apply_pending("spec", once)

    assert once == doc
    assert twice == once


def test_registry_chain_walks_versions() -> None:
    migration_registry.REGISTRY.clear()
    register(
        Migration(
            file_kind="synthetic",
            from_version=1,
            to_version=2,
            transform=lambda doc: {**doc, "seen": [*doc.get("seen", []), "v2"]},
        )
    )
    register(
        Migration(
            file_kind="synthetic",
            from_version=2,
            to_version=3,
            transform=lambda doc: {**doc, "seen": [*doc.get("seen", []), "v3"]},
        )
    )

    migrated = apply_pending("synthetic", {"schema_version": 1, "seen": []})

    assert migrated == {"schema_version": 3, "seen": ["v2", "v3"]}


def test_invertibility_when_declared() -> None:
    migration = Migration(
        file_kind="synthetic",
        from_version=1,
        to_version=2,
        transform=lambda doc: {
            **doc,
            "schema_version": 2,
            "renamed": doc["original"],
        },
        inverse=lambda doc: {
            **{key: value for key, value in doc.items() if key != "renamed"},
            "schema_version": 1,
            "original": doc["renamed"],
        },
    )
    doc = {"schema_version": 1, "original": "value"}

    forward = migration.transform(doc)
    assert migration.inverse is not None
    backward = migration.inverse(forward)

    assert backward == doc


def test_phase_a_missing_warns(tmp_path: Path) -> None:
    path = tmp_path / "SPEC.md"
    schema_path = Path("schemas/spec-frontmatter.schema.json")
    _write_markdown(path, _spec_frontmatter())

    with pytest.warns(DeprecationWarning, match="schema_version missing"):
        validate_file(path, schema_path)


def test_phase_a_missing_still_loads(tmp_path: Path) -> None:
    path = tmp_path / "SPEC.md"
    schema_path = Path("schemas/spec-frontmatter.schema.json")
    _write_markdown(path, _spec_frontmatter())

    with pytest.warns(DeprecationWarning):
        errors = validate_file(path, schema_path)

    assert errors == []


def test_phase_b_simulation_blocks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "SPEC.md"
    schema_path = Path("schemas/spec-frontmatter.schema.json")
    _write_markdown(path, _spec_frontmatter())
    monkeypatch.setenv("FORGE_SCHEMA_VERSION_REQUIRED", "1")

    errors = validate_file(path, schema_path)

    assert len(errors) == 1
    assert "schema_version missing" in errors[0]


def test_higher_than_known_refused(tmp_path: Path) -> None:
    path = tmp_path / "SPEC.md"
    schema_path = Path("schemas/spec-frontmatter.schema.json")
    _write_markdown(path, _spec_frontmatter(schema_version=9))

    errors = validate_file(path, schema_path)

    assert len(errors) == 1
    assert "schema_version 9 is newer than latest registered version 1" in errors[0]


def test_forge_state_migrate_dry_run(tmp_path: Path) -> None:
    feature_folder = _write_active_feature(tmp_path)
    spec_path = feature_folder / "SPEC.md"
    _write_markdown(spec_path, _spec_frontmatter())
    before = spec_path.read_text(encoding="utf-8")

    result = _run_migrate(tmp_path, "--dry-run")

    assert result.returncode == 0
    assert spec_path.read_text(encoding="utf-8") == before
    assert "dry-run: would migrate SPEC.md: spec schema_version implicit 1 -> 1" in result.stdout
    assert "ok: migrate feature=2026-05-17-migration-tests changed=1 dry_run=true" in result.stdout


def test_forge_state_migrate_writes(tmp_path: Path) -> None:
    feature_folder = _write_active_feature(tmp_path)
    spec_path = feature_folder / "SPEC.md"
    plan_path = feature_folder / "PLAN.md"
    _write_markdown(spec_path, _spec_frontmatter())
    _write_markdown(plan_path, {"id": "plan"})

    result = _run_migrate(tmp_path)

    spec_frontmatter = yaml.safe_load(spec_path.read_text(encoding="utf-8").split("---")[1])
    plan_frontmatter = yaml.safe_load(plan_path.read_text(encoding="utf-8").split("---")[1])

    assert result.returncode == 0
    assert spec_frontmatter["schema_version"] == 1
    assert plan_frontmatter["schema_version"] == 1
    assert "migrated: SPEC.md: spec schema_version implicit 1 -> 1" in result.stdout
    assert "migrated: PLAN.md: plan schema_version implicit 1 -> 1" in result.stdout
    assert "ok: migrate feature=2026-05-17-migration-tests changed=2" in result.stdout
