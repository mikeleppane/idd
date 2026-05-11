"""Tests for tools.state autodiscovery + NO_VALIDATE sentinel on read/write.

When ``schema_path`` is left as ``None``, ``read_state`` and ``write_state``
walk up from the target path looking for ``schemas/state.schema.json`` and
validate against it when found. Callers that genuinely need to skip
validation pass the ``NO_VALIDATE`` sentinel explicitly.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from tools import state
from tools.state import NO_VALIDATE


def _valid_payload() -> dict[str, Any]:
    return {
        "feature_id": "2026-05-10-demo-feature",
        "tier": "focused",
        "current_phase": "spec",
        "phases": {"spec": {"status": "in_progress", "started_at": "2026-05-10T10:00:00Z"}},
        "skipped": [],
        "deviations": [],
        "commits": [],
    }


def _seed_repo_tree(tmp_path: Path, schemas_dir: Path) -> Path:
    """Build a tmp_path that mimics a FORGE repo with `schemas/` next to `.forge/`.

    Returns the path where state.json should live (under .forge/features/<id>/).
    """
    (tmp_path / "schemas").mkdir()
    shutil.copy(schemas_dir / "state.schema.json", tmp_path / "schemas" / "state.schema.json")
    (tmp_path / ".forge").mkdir()
    feature_dir = tmp_path / ".forge" / "features" / "2026-05-10-demo-feature"
    feature_dir.mkdir(parents=True)
    return feature_dir / "state.json"


# ---------------------------------------------------------------------------
# read_state autodiscovery behaviour
# ---------------------------------------------------------------------------


def test_read_state_autodiscovers_schema_and_rejects_malformed_payload(
    tmp_path: Path, schemas_dir: Path
) -> None:
    state_path = _seed_repo_tree(tmp_path, schemas_dir)
    payload = _valid_payload()
    payload["phases"]["spec"]["status"] = "completed"  # not in enum
    state_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(state.StateError, match="schema"):
        state.read_state(state_path)


def test_read_state_with_no_schema_tree_skips_validation_silently(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    target.write_text(json.dumps({"random": "blob"}), encoding="utf-8")

    result = state.read_state(target)

    assert result == {"random": "blob"}


def test_read_state_no_validate_sentinel_skips_even_when_schema_discoverable(
    tmp_path: Path, schemas_dir: Path
) -> None:
    state_path = _seed_repo_tree(tmp_path, schemas_dir)
    payload = _valid_payload()
    payload["phases"]["spec"]["status"] = "completed"  # malformed
    state_path.write_text(json.dumps(payload), encoding="utf-8")

    # NO_VALIDATE overrides autodiscovery: a schema would be found above but
    # we explicitly skip it.
    result = state.read_state(state_path, schema_path=NO_VALIDATE)

    assert result["phases"]["spec"]["status"] == "completed"


# ---------------------------------------------------------------------------
# write_state autodiscovery behaviour
# ---------------------------------------------------------------------------


def test_write_state_autodiscovers_schema_and_rejects_malformed_payload(
    tmp_path: Path, schemas_dir: Path
) -> None:
    state_path = _seed_repo_tree(tmp_path, schemas_dir)
    payload = _valid_payload()
    payload["phases"]["spec"]["status"] = "completed"

    with pytest.raises(state.StateError, match="schema"):
        state.write_state(state_path, payload)

    assert not state_path.exists()


def test_write_state_with_no_schema_tree_writes_without_validation(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    state.write_state(target, {"junk": "ok"})

    assert json.loads(target.read_text(encoding="utf-8")) == {"junk": "ok"}


def test_write_state_no_validate_sentinel_writes_malformed_payload(
    tmp_path: Path, schemas_dir: Path
) -> None:
    state_path = _seed_repo_tree(tmp_path, schemas_dir)
    payload = _valid_payload()
    payload["phases"]["spec"]["status"] = "completed"

    state.write_state(state_path, payload, schema_path=NO_VALIDATE)

    on_disk = json.loads(state_path.read_text(encoding="utf-8"))
    assert on_disk["phases"]["spec"]["status"] == "completed"


def test_write_state_autodiscovers_schema_and_accepts_valid_payload(
    tmp_path: Path, schemas_dir: Path
) -> None:
    state_path = _seed_repo_tree(tmp_path, schemas_dir)
    state.write_state(state_path, _valid_payload())

    on_disk = json.loads(state_path.read_text(encoding="utf-8"))
    assert on_disk["feature_id"] == "2026-05-10-demo-feature"


# ---------------------------------------------------------------------------
# _autodiscover_state_schema unit coverage
# ---------------------------------------------------------------------------


def test_autodiscover_finds_schema_one_level_up(tmp_path: Path, schemas_dir: Path) -> None:
    (tmp_path / "schemas").mkdir()
    shutil.copy(schemas_dir / "state.schema.json", tmp_path / "schemas" / "state.schema.json")
    inner = tmp_path / "child"
    inner.mkdir()

    found = state._autodiscover_state_schema(inner / "state.json")

    assert found == tmp_path / "schemas" / "state.schema.json"


def test_autodiscover_walks_up_multiple_levels(tmp_path: Path, schemas_dir: Path) -> None:
    (tmp_path / "schemas").mkdir()
    shutil.copy(schemas_dir / "state.schema.json", tmp_path / "schemas" / "state.schema.json")
    deep = tmp_path / "a" / "b" / "c" / "d"
    deep.mkdir(parents=True)

    found = state._autodiscover_state_schema(deep / "state.json")

    assert found == tmp_path / "schemas" / "state.schema.json"


def test_autodiscover_stops_at_git_boundary(tmp_path: Path, schemas_dir: Path) -> None:
    # `.git/` directory marks a repo boundary; a schema above it must NOT be
    # discovered because the walker stops at the boundary.
    (tmp_path / "schemas").mkdir()
    shutil.copy(schemas_dir / "state.schema.json", tmp_path / "schemas" / "state.schema.json")
    inner_repo = tmp_path / "nested-repo"
    inner_repo.mkdir()
    (inner_repo / ".git").mkdir()
    leaf = inner_repo / "deep"
    leaf.mkdir()

    found = state._autodiscover_state_schema(leaf / "state.json")

    assert found is None


def test_autodiscover_returns_none_when_no_schema_anywhere(tmp_path: Path) -> None:
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)

    found = state._autodiscover_state_schema(nested / "state.json")

    assert found is None


def test_autodiscover_respects_depth_cap(tmp_path: Path, schemas_dir: Path) -> None:
    # Build a path more than 12 levels below the schema. Autodiscovery must
    # stop walking before reaching the schema.
    (tmp_path / "schemas").mkdir()
    shutil.copy(schemas_dir / "state.schema.json", tmp_path / "schemas" / "state.schema.json")
    deep = tmp_path
    for level in range(15):
        deep = deep / f"level-{level}"
    deep.mkdir(parents=True)

    found = state._autodiscover_state_schema(deep / "state.json")

    assert found is None


# ---------------------------------------------------------------------------
# Sentinel identity, repr, typing
# ---------------------------------------------------------------------------


def test_no_validate_is_singleton() -> None:
    assert state.NO_VALIDATE is state.NO_VALIDATE
    assert state.NO_VALIDATE is NO_VALIDATE


def test_no_validate_repr_is_stable() -> None:
    assert repr(NO_VALIDATE) == "NO_VALIDATE"


def test_no_validate_typing_round_trip() -> None:
    # Bind the sentinel to a type-annotated local using the private alias.
    # If the exported sentinel ever disagrees with its declared type, mypy
    # surfaces the mismatch here at test-collect time.
    sentinel: state._NoValidate = NO_VALIDATE
    assert sentinel is NO_VALIDATE
    assert isinstance(sentinel, state._NoValidate)
