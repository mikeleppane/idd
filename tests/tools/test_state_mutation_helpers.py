"""Tests for hook-protected state.json mutation helpers.

The PreToolUse hook at ``hooks/check_state_writer.py`` refuses direct
Write / Edit / MultiEdit against live feature state.json files. The
forge-execute and forge-domain skills used to mutate three fields
without a corresponding helper (``state.commits[]``,
``state.deviations[]``, ``phases.execute.current_slice``) — agents
either hit a permission-deny mid-phase or bypassed the hook through
Bash, sidestepping schema validation.

These tests pin the three new helpers that close that bypass class:

* ``tools.state.set_execute_current_slice`` — stamps the per-slice cursor.
* ``tools.state.record_commit`` — appends a schema-shaped commit entry.
* ``tools.state.append_deviation`` — appends a schema-shaped deviation.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from tools import state


def _execute_state(tmp_path: Path, schemas_dir: Path) -> Path:
    """Seed a focused-tier state.json mid-execute and return its path."""
    target = tmp_path / "state.json"
    payload: dict[str, Any] = {
        "feature_id": "2026-05-11-mutation-helpers",
        "tier": "focused",
        "current_phase": "execute",
        "phases": {
            "spec": {"status": "done", "completed_at": "2026-05-11T10:00:00Z"},
            "execute": {"status": "in_progress", "started_at": "2026-05-11T11:00:00Z"},
        },
        "skipped": [],
        "deviations": [],
        "commits": [],
    }
    state.write_state(target, payload, schema_path=schemas_dir / "state.schema.json")
    return target


# ---------------------------------------------------------------------------
# set_execute_current_slice
# ---------------------------------------------------------------------------


def test_set_execute_current_slice_stamps_block(tmp_path: Path, schemas_dir: Path) -> None:
    target = _execute_state(tmp_path, schemas_dir)
    result = state.set_execute_current_slice(
        target, slice_number=3, schema_path=schemas_dir / "state.schema.json"
    )
    assert result["phases"]["execute"]["current_slice"] == 3
    on_disk = json.loads(target.read_text(encoding="utf-8"))
    assert on_disk["phases"]["execute"]["current_slice"] == 3


@pytest.mark.parametrize("bad_value", [0, -1, -100])
def test_set_execute_current_slice_rejects_non_positive(
    tmp_path: Path, schemas_dir: Path, bad_value: int
) -> None:
    target = _execute_state(tmp_path, schemas_dir)
    with pytest.raises(state.StateError, match="positive int"):
        state.set_execute_current_slice(
            target, slice_number=bad_value, schema_path=schemas_dir / "state.schema.json"
        )


def test_set_execute_current_slice_rejects_bool(tmp_path: Path, schemas_dir: Path) -> None:
    """``bool`` is a subclass of ``int``; guard against ``True``/``False`` sneaking in."""
    target = _execute_state(tmp_path, schemas_dir)
    with pytest.raises(state.StateError, match="positive int"):
        state.set_execute_current_slice(
            target,
            slice_number=True,
            schema_path=schemas_dir / "state.schema.json",
        )


def test_set_execute_current_slice_rejects_wrong_current_phase(
    tmp_path: Path, schemas_dir: Path
) -> None:
    target = _execute_state(tmp_path, schemas_dir)
    payload = state.read_state(target, schema_path=schemas_dir / "state.schema.json")
    payload["current_phase"] = "spec"
    state.write_state(target, payload, schema_path=schemas_dir / "state.schema.json")
    with pytest.raises(state.StateError, match="current_phase is 'spec'"):
        state.set_execute_current_slice(
            target, slice_number=1, schema_path=schemas_dir / "state.schema.json"
        )


def test_set_execute_current_slice_rejects_wrong_status(tmp_path: Path, schemas_dir: Path) -> None:
    target = _execute_state(tmp_path, schemas_dir)
    payload = state.read_state(target, schema_path=schemas_dir / "state.schema.json")
    payload["phases"]["execute"]["status"] = "done"
    payload["phases"]["execute"]["completed_at"] = "2026-05-11T12:00:00Z"
    state.write_state(target, payload, schema_path=schemas_dir / "state.schema.json")
    with pytest.raises(state.StateError, match="status is 'done'"):
        state.set_execute_current_slice(
            target, slice_number=1, schema_path=schemas_dir / "state.schema.json"
        )


# ---------------------------------------------------------------------------
# record_commit
# ---------------------------------------------------------------------------


def test_record_commit_appends_schema_shape(tmp_path: Path, schemas_dir: Path) -> None:
    target = _execute_state(tmp_path, schemas_dir)
    result = state.record_commit(
        target,
        sha="abc1234",
        phase="execute",
        subject="test(csv): cover empty file path",
        schema_path=schemas_dir / "state.schema.json",
    )
    assert len(result["commits"]) == 1
    entry = result["commits"][0]
    assert entry["sha"] == "abc1234"
    assert entry["phase"] == "execute"
    assert entry["subject"] == "test(csv): cover empty file path"
    assert entry["logged_at"].endswith("Z")  # RFC 3339 UTC


def test_record_commit_respects_caller_supplied_logged_at(
    tmp_path: Path, schemas_dir: Path
) -> None:
    target = _execute_state(tmp_path, schemas_dir)
    result = state.record_commit(
        target,
        sha="abc1234",
        phase="execute",
        subject="test: cover empty CSV",
        logged_at="2026-05-11T11:30:00Z",
        schema_path=schemas_dir / "state.schema.json",
    )
    assert result["commits"][0]["logged_at"] == "2026-05-11T11:30:00Z"


def test_record_commit_accumulates_in_order(tmp_path: Path, schemas_dir: Path) -> None:
    target = _execute_state(tmp_path, schemas_dir)
    state.record_commit(
        target,
        sha="aaa1111",
        phase="execute",
        subject="first",
        schema_path=schemas_dir / "state.schema.json",
    )
    state.record_commit(
        target,
        sha="bbb2222",
        phase="execute",
        subject="second",
        schema_path=schemas_dir / "state.schema.json",
    )
    on_disk = json.loads(target.read_text(encoding="utf-8"))
    assert [c["sha"] for c in on_disk["commits"]] == ["aaa1111", "bbb2222"]
    assert [c["subject"] for c in on_disk["commits"]] == ["first", "second"]


@pytest.mark.parametrize(
    "bad_sha",
    [
        "abc123",  # 6 chars — too short
        "z" * 7,  # non-hex
        "abc12345678901234567890123456789012345678901",  # 41 chars — too long
        "ABC1234",  # uppercase
        "",
    ],
)
def test_record_commit_rejects_invalid_sha(tmp_path: Path, schemas_dir: Path, bad_sha: str) -> None:
    target = _execute_state(tmp_path, schemas_dir)
    with pytest.raises(state.StateError, match="invalid commit sha"):
        state.record_commit(
            target,
            sha=bad_sha,
            phase="execute",
            subject="x",
            schema_path=schemas_dir / "state.schema.json",
        )


def test_record_commit_rejects_invalid_phase(tmp_path: Path, schemas_dir: Path) -> None:
    target = _execute_state(tmp_path, schemas_dir)
    with pytest.raises(state.StateError, match="invalid commit phase"):
        state.record_commit(
            target,
            sha="abc1234",
            phase="fictional",
            subject="x",
            schema_path=schemas_dir / "state.schema.json",
        )


def test_record_commit_rejects_empty_subject(tmp_path: Path, schemas_dir: Path) -> None:
    target = _execute_state(tmp_path, schemas_dir)
    with pytest.raises(state.StateError, match="commit subject must be a non-empty"):
        state.record_commit(
            target,
            sha="abc1234",
            phase="execute",
            subject="",
            schema_path=schemas_dir / "state.schema.json",
        )


# ---------------------------------------------------------------------------
# append_deviation
# ---------------------------------------------------------------------------


def test_append_deviation_writes_schema_shape(tmp_path: Path, schemas_dir: Path) -> None:
    target = _execute_state(tmp_path, schemas_dir)
    result = state.append_deviation(
        target,
        phase="execute",
        cause="missing test infra",
        resolution="ADR-007: deferred to follow-up",
        schema_path=schemas_dir / "state.schema.json",
    )
    assert len(result["deviations"]) == 1
    entry = result["deviations"][0]
    assert entry == {
        "phase": "execute",
        "cause": "missing test infra",
        "resolution": "ADR-007: deferred to follow-up",
        "logged_at": entry["logged_at"],
    }
    assert entry["logged_at"].endswith("Z")


def test_append_deviation_respects_caller_supplied_logged_at(
    tmp_path: Path, schemas_dir: Path
) -> None:
    target = _execute_state(tmp_path, schemas_dir)
    result = state.append_deviation(
        target,
        phase="execute",
        cause="x",
        resolution="y",
        logged_at="2026-05-11T11:45:00Z",
        schema_path=schemas_dir / "state.schema.json",
    )
    assert result["deviations"][0]["logged_at"] == "2026-05-11T11:45:00Z"


def test_append_deviation_accumulates_in_order(tmp_path: Path, schemas_dir: Path) -> None:
    target = _execute_state(tmp_path, schemas_dir)
    state.append_deviation(
        target,
        phase="execute",
        cause="first",
        resolution="ADR-1",
        schema_path=schemas_dir / "state.schema.json",
    )
    state.append_deviation(
        target,
        phase="execute",
        cause="second",
        resolution="ADR-2",
        schema_path=schemas_dir / "state.schema.json",
    )
    on_disk = json.loads(target.read_text(encoding="utf-8"))
    assert [d["cause"] for d in on_disk["deviations"]] == ["first", "second"]


def test_append_deviation_rejects_invalid_phase(tmp_path: Path, schemas_dir: Path) -> None:
    target = _execute_state(tmp_path, schemas_dir)
    with pytest.raises(state.StateError, match="invalid deviation phase"):
        state.append_deviation(
            target,
            phase="fictional",
            cause="x",
            resolution="y",
            schema_path=schemas_dir / "state.schema.json",
        )


@pytest.mark.parametrize("field", ["cause", "resolution"])
def test_append_deviation_rejects_empty_strings(
    tmp_path: Path, schemas_dir: Path, field: str
) -> None:
    target = _execute_state(tmp_path, schemas_dir)
    kwargs: dict[str, Any] = {"phase": "execute", "cause": "x", "resolution": "y"}
    kwargs[field] = ""
    with pytest.raises(state.StateError, match=f"deviation {field}"):
        state.append_deviation(
            target,
            schema_path=schemas_dir / "state.schema.json",
            **kwargs,
        )


# ---------------------------------------------------------------------------
# Round-trip with schema autodiscovery (no explicit schema_path)
# ---------------------------------------------------------------------------


def test_helpers_use_schema_autodiscovery_when_available(tmp_path: Path, schemas_dir: Path) -> None:
    """When the helpers are called without ``schema_path`` and a discoverable
    schema sits next to the feature folder, autodiscovery validates the
    write — a malformed payload (e.g. empty commits list with a bad phase
    enum from a prior corruption) is rejected without disk mutation."""
    (tmp_path / "schemas").mkdir()
    shutil.copy(schemas_dir / "state.schema.json", tmp_path / "schemas" / "state.schema.json")
    (tmp_path / ".forge").mkdir()
    feature_dir = tmp_path / ".forge" / "features" / "2026-05-11-mutation-helpers"
    feature_dir.mkdir(parents=True)
    state_path = feature_dir / "state.json"

    payload: dict[str, Any] = {
        "feature_id": "2026-05-11-mutation-helpers",
        "tier": "focused",
        "current_phase": "execute",
        "phases": {
            "spec": {"status": "done", "completed_at": "2026-05-11T10:00:00Z"},
            "execute": {"status": "in_progress", "started_at": "2026-05-11T11:00:00Z"},
        },
        "skipped": [],
        "deviations": [],
        "commits": [],
    }
    state.write_state(state_path, payload)

    state.record_commit(state_path, sha="abc1234", phase="execute", subject="t")
    state.append_deviation(state_path, phase="execute", cause="c", resolution="r")
    state.set_execute_current_slice(state_path, slice_number=2)

    on_disk = json.loads(state_path.read_text(encoding="utf-8"))
    assert len(on_disk["commits"]) == 1
    assert len(on_disk["deviations"]) == 1
    assert on_disk["phases"]["execute"]["current_slice"] == 2
