"""Tests for tools.state — feature state.json read/write/transition."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import state


def test_read_state_returns_parsed_dict_for_valid_file(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    payload = {
        "feature_id": "2026-05-03-demo-feature",
        "tier": "focused",
        "current_phase": "spec",
        "phases": {"spec": {"status": "in_progress"}},
        "skipped": [],
        "deviations": [],
        "commits": [],
    }
    target.write_text(json.dumps(payload), encoding="utf-8")

    result = state.read_state(target)

    assert result == payload


def test_read_state_raises_on_missing_file(tmp_path: Path) -> None:
    with pytest.raises(state.StateError, match="not found"):
        state.read_state(tmp_path / "missing.json")


def test_read_state_raises_on_invalid_json(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    target.write_text("{not json", encoding="utf-8")

    with pytest.raises(state.StateError, match="invalid JSON"):
        state.read_state(target)


def test_read_state_validates_against_schema(tmp_path: Path, schemas_dir: Path) -> None:
    target = tmp_path / "state.json"
    target.write_text(json.dumps({"feature_id": "BAD ID with spaces"}), encoding="utf-8")

    with pytest.raises(state.StateError, match="schema"):
        state.read_state(target, schema_path=schemas_dir / "state.schema.json")


def test_read_state_passes_when_schema_satisfied(tmp_path: Path, schemas_dir: Path) -> None:
    target = tmp_path / "state.json"
    payload = {
        "feature_id": "2026-05-03-demo-feature",
        "tier": "focused",
        "current_phase": "spec",
        "phases": {"spec": {"status": "in_progress", "started_at": "2026-05-03T10:00:00Z"}},
        "skipped": [],
        "deviations": [],
        "commits": [],
    }
    target.write_text(json.dumps(payload), encoding="utf-8")

    result = state.read_state(target, schema_path=schemas_dir / "state.schema.json")

    assert result["feature_id"] == "2026-05-03-demo-feature"


def test_read_state_rejects_malformed_date_time(tmp_path: Path, schemas_dir: Path) -> None:
    target = tmp_path / "state.json"
    payload = {
        "feature_id": "2026-05-03-demo-feature",
        "tier": "focused",
        "current_phase": "spec",
        "phases": {"spec": {"status": "in_progress", "started_at": "yesterday"}},
        "skipped": [],
        "deviations": [],
        "commits": [],
    }
    target.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(state.StateError, match="date-time"):
        state.read_state(target, schema_path=schemas_dir / "state.schema.json")


def test_read_state_rejects_unknown_phase_key(tmp_path: Path, schemas_dir: Path) -> None:
    target = tmp_path / "state.json"
    payload = {
        "feature_id": "2026-05-03-demo-feature",
        "tier": "focused",
        "current_phase": "spec",
        "phases": {"not-a-phase": {"status": "in_progress"}},
        "skipped": [],
        "deviations": [],
        "commits": [],
    }
    target.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(state.StateError, match="schema"):
        state.read_state(target, schema_path=schemas_dir / "state.schema.json")


def test_write_state_creates_file_with_pretty_json(tmp_path: Path, schemas_dir: Path) -> None:
    target = tmp_path / "state.json"
    payload = {
        "feature_id": "2026-05-03-demo-feature",
        "tier": "focused",
        "current_phase": "spec",
        "phases": {"spec": {"status": "in_progress"}},
        "skipped": [],
        "deviations": [],
        "commits": [],
    }

    state.write_state(target, payload, schema_path=schemas_dir / "state.schema.json")

    text = target.read_text(encoding="utf-8")
    assert text.startswith("{\n")
    assert json.loads(text) == payload


def test_write_state_rejects_invalid_payload(tmp_path: Path, schemas_dir: Path) -> None:
    target = tmp_path / "state.json"

    with pytest.raises(state.StateError, match="schema"):
        state.write_state(
            target,
            {"feature_id": "BAD ID"},
            schema_path=schemas_dir / "state.schema.json",
        )

    assert not target.exists(), "must not write a file that fails schema validation"


def test_complete_phase_marks_current_done(tmp_path: Path, schemas_dir: Path) -> None:
    target = tmp_path / "state.json"
    initial = {
        "feature_id": "2026-05-03-demo-feature",
        "tier": "focused",
        "current_phase": "spec",
        "phases": {"spec": {"status": "in_progress", "started_at": "2026-05-03T10:00:00Z"}},
        "skipped": [],
        "deviations": [],
        "commits": [],
    }
    state.write_state(target, initial, schema_path=schemas_dir / "state.schema.json")

    result = state.complete_phase(
        target,
        phase="spec",
        schema_path=schemas_dir / "state.schema.json",
        now="2026-05-03T11:30:00Z",
    )

    assert result["phases"]["spec"]["status"] == "done"
    assert result["phases"]["spec"]["completed_at"] == "2026-05-03T11:30:00Z"
    assert result["current_phase"] == "spec"


def test_start_phase_marks_next_in_progress(tmp_path: Path, schemas_dir: Path) -> None:
    target = tmp_path / "state.json"
    initial = {
        "feature_id": "2026-05-03-demo-feature",
        "tier": "focused",
        "current_phase": "spec",
        "phases": {"spec": {"status": "done", "started_at": "2026-05-03T10:00:00Z", "completed_at": "2026-05-03T11:30:00Z"}},
        "skipped": [],
        "deviations": [],
        "commits": [],
    }
    state.write_state(target, initial, schema_path=schemas_dir / "state.schema.json")

    result = state.start_phase(
        target,
        phase="execute",
        schema_path=schemas_dir / "state.schema.json",
        now="2026-05-03T11:35:00Z",
    )

    assert result["current_phase"] == "execute"
    assert result["phases"]["execute"] == {"status": "in_progress", "started_at": "2026-05-03T11:35:00Z"}


def test_start_phase_resets_existing_entry(tmp_path: Path, schemas_dir: Path) -> None:
    """Re-entering a phase must replace the entry, not preserve stale fields."""
    target = tmp_path / "state.json"
    initial = {
        "feature_id": "2026-05-03-demo-feature",
        "tier": "focused",
        "current_phase": "execute",
        "phases": {
            "spec":    {"status": "done"},
            "execute": {"status": "done", "current_slice": 3, "started_at": "2026-05-03T11:30:00Z", "completed_at": "2026-05-03T12:00:00Z"},
        },
        "skipped": [],
        "deviations": [],
        "commits": [],
    }
    state.write_state(target, initial, schema_path=schemas_dir / "state.schema.json")

    result = state.start_phase(
        target,
        phase="execute",
        schema_path=schemas_dir / "state.schema.json",
        now="2026-05-03T13:00:00Z",
    )

    assert result["phases"]["execute"] == {"status": "in_progress", "started_at": "2026-05-03T13:00:00Z"}
    assert "completed_at" not in result["phases"]["execute"]
    assert "current_slice" not in result["phases"]["execute"]


def test_start_phase_rejects_unknown_phase(tmp_path: Path, schemas_dir: Path) -> None:
    target = tmp_path / "state.json"
    initial = {
        "feature_id": "2026-05-03-demo-feature",
        "tier": "focused",
        "current_phase": "spec",
        "phases": {"spec": {"status": "in_progress"}},
        "skipped": [],
        "deviations": [],
        "commits": [],
    }
    state.write_state(target, initial, schema_path=schemas_dir / "state.schema.json")

    with pytest.raises(state.StateError, match="unknown phase"):
        state.start_phase(
            target,
            phase="not-a-phase",
            schema_path=schemas_dir / "state.schema.json",
        )


def test_finish_feature_sets_current_phase_done_without_phases_entry(
    tmp_path: Path, schemas_dir: Path
) -> None:
    target = tmp_path / "state.json"
    initial = {
        "feature_id": "2026-05-03-demo-feature",
        "tier": "focused",
        "current_phase": "verify",
        "phases": {
            "spec":    {"status": "done"},
            "execute": {"status": "done"},
            "verify":  {"status": "done"},
        },
        "skipped": [],
        "deviations": [],
        "commits": [],
    }
    state.write_state(target, initial, schema_path=schemas_dir / "state.schema.json")

    result = state.finish_feature(target, schema_path=schemas_dir / "state.schema.json")

    assert result["current_phase"] == "done"
    assert "done" not in result["phases"]
