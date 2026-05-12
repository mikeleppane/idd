"""Tests for tools.state — feature state.json read/write/transition."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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


@pytest.mark.parametrize(
    "bad_feature_id",
    [
        "2026-13-01-foo",  # month 13 is impossible
        "2026-00-01-foo",  # month 00 is impossible
        "2026-01-32-foo",  # day 32 is impossible
        "2026-01-00-foo",  # day 00 is impossible
        "2026-99-15-foo",  # month 99 is impossible
    ],
)
def test_read_state_rejects_feature_id_with_impossible_calendar_segment(
    tmp_path: Path,
    schemas_dir: Path,
    bad_feature_id: str,
) -> None:
    target = tmp_path / "state.json"
    payload = {
        "feature_id": bad_feature_id,
        "tier": "focused",
        "current_phase": "spec",
        "phases": {"spec": {"status": "in_progress"}},
        "skipped": [],
        "deviations": [],
        "commits": [],
    }
    target.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(state.StateError, match="schema"):
        state.read_state(target, schema_path=schemas_dir / "state.schema.json")


@pytest.mark.parametrize(
    "good_feature_id",
    [
        "2026-01-01-foo",
        "2026-12-31-bar-baz",
    ],
)
def test_read_state_accepts_feature_id_with_valid_calendar_segment(
    tmp_path: Path,
    schemas_dir: Path,
    good_feature_id: str,
) -> None:
    target = tmp_path / "state.json"
    payload = {
        "feature_id": good_feature_id,
        "tier": "focused",
        "current_phase": "spec",
        "phases": {"spec": {"status": "in_progress"}},
        "skipped": [],
        "deviations": [],
        "commits": [],
    }
    target.write_text(json.dumps(payload), encoding="utf-8")

    result = state.read_state(target, schema_path=schemas_dir / "state.schema.json")

    assert result["feature_id"] == good_feature_id


def test_read_state_rejects_top_level_list(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    target.write_text("[]", encoding="utf-8")

    with pytest.raises(
        state.StateError, match="expected JSON object at top level, got list"
    ) as exc:
        state.read_state(target, schema_path=state.NO_VALIDATE)
    assert str(target) in str(exc.value)


def test_read_state_rejects_top_level_string(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    target.write_text('"hello"', encoding="utf-8")

    with pytest.raises(state.StateError, match="expected JSON object at top level, got str") as exc:
        state.read_state(target, schema_path=state.NO_VALIDATE)
    assert str(target) in str(exc.value)


def test_read_state_rejects_top_level_number(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    target.write_text("42", encoding="utf-8")

    with pytest.raises(state.StateError, match="expected JSON object at top level, got int") as exc:
        state.read_state(target, schema_path=state.NO_VALIDATE)
    assert str(target) in str(exc.value)


def test_read_state_rejects_top_level_null(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    target.write_text("null", encoding="utf-8")

    with pytest.raises(
        state.StateError, match="expected JSON object at top level, got NoneType"
    ) as exc:
        state.read_state(target, schema_path=state.NO_VALIDATE)
    assert str(target) in str(exc.value)


def test_read_state_accepts_empty_dict_when_no_schema(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    target.write_text("{}", encoding="utf-8")

    result = state.read_state(target, schema_path=state.NO_VALIDATE)

    assert result == {}


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


def test_complete_phase_ship_sets_shipped_at(tmp_path: Path, schemas_dir: Path) -> None:
    """Completing the ship phase must stamp top-level shipped_at with the timestamp."""
    target = tmp_path / "state.json"
    initial = {
        "feature_id": "2026-05-09-shipped-at-fix",
        "tier": "standard",
        "current_phase": "ship",
        "phases": {
            "ship": {
                "status": "in_progress",
                "started_at": "2026-05-09T10:00:00Z",
            }
        },
        "skipped": [],
        "deviations": [],
        "commits": [],
    }
    state.write_state(target, initial, schema_path=schemas_dir / "state.schema.json")

    result = state.complete_phase(
        target,
        phase="ship",
        schema_path=schemas_dir / "state.schema.json",
        now="2026-05-09T11:00:00Z",
    )

    assert result["phases"]["ship"]["status"] == "done"
    assert result["phases"]["ship"]["completed_at"] == "2026-05-09T11:00:00Z"
    assert result["shipped_at"] == "2026-05-09T11:00:00Z"

    # Persisted to disk.
    on_disk = json.loads(target.read_text(encoding="utf-8"))
    assert on_disk["shipped_at"] == "2026-05-09T11:00:00Z"


def test_complete_phase_non_ship_does_not_set_shipped_at(tmp_path: Path, schemas_dir: Path) -> None:
    """Only ship completion stamps shipped_at; other phases leave it absent."""
    target = tmp_path / "state.json"
    initial = {
        "feature_id": "2026-05-09-no-shipped-at",
        "tier": "focused",
        "current_phase": "verify",
        "phases": {
            "verify": {
                "status": "in_progress",
                "started_at": "2026-05-09T10:00:00Z",
            }
        },
        "skipped": [],
        "deviations": [],
        "commits": [],
    }
    state.write_state(target, initial, schema_path=schemas_dir / "state.schema.json")

    result = state.complete_phase(
        target,
        phase="verify",
        schema_path=schemas_dir / "state.schema.json",
        now="2026-05-09T11:00:00Z",
    )

    assert result["phases"]["verify"]["status"] == "done"
    assert "shipped_at" not in result


def test_complete_phase_ship_idempotent_does_not_overwrite_shipped_at(
    tmp_path: Path, schemas_dir: Path
) -> None:
    """If shipped_at is already set, complete_phase preserves the original timestamp."""
    target = tmp_path / "state.json"
    original_shipped = "2026-05-09T09:00:00Z"
    initial = {
        "feature_id": "2026-05-09-idempotent-ship",
        "tier": "standard",
        "current_phase": "ship",
        "shipped_at": original_shipped,
        "phases": {
            "ship": {
                "status": "in_progress",
                "started_at": "2026-05-09T10:00:00Z",
            }
        },
        "skipped": [],
        "deviations": [],
        "commits": [],
    }
    state.write_state(target, initial, schema_path=schemas_dir / "state.schema.json")

    result = state.complete_phase(
        target,
        phase="ship",
        schema_path=schemas_dir / "state.schema.json",
        now="2026-05-09T12:00:00Z",
    )

    # Original shipped_at preserved; completed_at uses the new now value.
    assert result["shipped_at"] == original_shipped
    assert result["phases"]["ship"]["completed_at"] == "2026-05-09T12:00:00Z"


def test_start_phase_marks_next_in_progress(tmp_path: Path, schemas_dir: Path) -> None:
    target = tmp_path / "state.json"
    initial = {
        "feature_id": "2026-05-03-demo-feature",
        "tier": "focused",
        "current_phase": "spec",
        "phases": {
            "spec": {
                "status": "done",
                "started_at": "2026-05-03T10:00:00Z",
                "completed_at": "2026-05-03T11:30:00Z",
            }
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
        now="2026-05-03T11:35:00Z",
    )

    assert result["current_phase"] == "execute"
    assert result["phases"]["execute"] == {
        "status": "in_progress",
        "started_at": "2026-05-03T11:35:00Z",
    }


def test_start_phase_resets_existing_entry(tmp_path: Path, schemas_dir: Path) -> None:
    """Re-entering a done phase via force=True must replace the entry, not preserve stale fields.

    Re-starting a phase that already finished is a recovery path, not a
    normal lifecycle transition. Production callers go through
    tools.recovery.recover_force_start_phase to land an audited ADR;
    this test pins the bare reset semantics that recovery relies on.
    """
    target = tmp_path / "state.json"
    initial = {
        "feature_id": "2026-05-03-demo-feature",
        "tier": "focused",
        "current_phase": "execute",
        "phases": {
            "spec": {"status": "done"},
            "execute": {
                "status": "done",
                "current_slice": 3,
                "started_at": "2026-05-03T11:30:00Z",
                "completed_at": "2026-05-03T12:00:00Z",
            },
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
        force=True,
    )

    assert result["phases"]["execute"] == {
        "status": "in_progress",
        "started_at": "2026-05-03T13:00:00Z",
    }
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


# ---------------------------------------------------------------------------
# start_phase enforces tier-allowed phase set
# ---------------------------------------------------------------------------


def _seed_state_for_tier(target: Path, schemas_dir: Path, tier: str) -> None:
    initial = {
        "feature_id": "2026-05-03-demo-feature",
        "tier": tier,
        "current_phase": "spec",
        "phases": {"spec": {"status": "in_progress"}},
        "skipped": [],
        "deviations": [],
        "commits": [],
    }
    state.write_state(target, initial, schema_path=schemas_dir / "state.schema.json")


def test_start_phase_rejects_refine_on_focused_tier(tmp_path: Path, schemas_dir: Path) -> None:
    """A focused-tier feature has no refine slot; start_phase must refuse."""
    target = tmp_path / "state.json"
    _seed_state_for_tier(target, schemas_dir, "focused")
    with pytest.raises(state.StateError, match=r"phase 'refine' not allowed on tier 'focused'"):
        state.start_phase(target, phase="refine", schema_path=schemas_dir / "state.schema.json")


def test_start_phase_rejects_refine_on_standard_tier(tmp_path: Path, schemas_dir: Path) -> None:
    """Standard tier never enters refine — it's full-tier only."""
    target = tmp_path / "state.json"
    _seed_state_for_tier(target, schemas_dir, "standard")
    with pytest.raises(state.StateError, match=r"phase 'refine' not allowed on tier 'standard'"):
        state.start_phase(target, phase="refine", schema_path=schemas_dir / "state.schema.json")


def test_start_phase_rejects_domain_on_focused_tier(tmp_path: Path, schemas_dir: Path) -> None:
    """Focused tier skips domain — it's full-tier-only."""
    target = tmp_path / "state.json"
    _seed_state_for_tier(target, schemas_dir, "focused")
    with pytest.raises(state.StateError, match=r"phase 'domain' not allowed on tier 'focused'"):
        state.start_phase(target, phase="domain", schema_path=schemas_dir / "state.schema.json")


def test_start_phase_rejects_scenarios_on_focused_tier(tmp_path: Path, schemas_dir: Path) -> None:
    """Focused tier collapses scenarios; it's standard/full only."""
    target = tmp_path / "state.json"
    _seed_state_for_tier(target, schemas_dir, "focused")
    with pytest.raises(state.StateError, match=r"phase 'scenarios' not allowed on tier 'focused'"):
        state.start_phase(target, phase="scenarios", schema_path=schemas_dir / "state.schema.json")


def test_start_phase_accepts_refine_on_full_tier(tmp_path: Path, schemas_dir: Path) -> None:
    """Full tier still permits refine (it's the canonical full-tier entry phase)."""
    target = tmp_path / "state.json"
    _seed_state_for_tier(target, schemas_dir, "full")
    result = state.start_phase(
        target,
        phase="refine",
        schema_path=schemas_dir / "state.schema.json",
    )
    assert result["current_phase"] == "refine"


def test_start_phase_accepts_execute_on_focused_tier(tmp_path: Path, schemas_dir: Path) -> None:
    """Focused-tier execute is allowed (it's in _FOCUSED_NEXT)."""
    target = tmp_path / "state.json"
    _seed_state_for_tier(target, schemas_dir, "focused")
    result = state.start_phase(
        target,
        phase="execute",
        schema_path=schemas_dir / "state.schema.json",
    )
    assert result["current_phase"] == "execute"


def test_complete_phase_rejects_when_not_current_phase(tmp_path: Path, schemas_dir: Path) -> None:
    target = tmp_path / "state.json"
    initial = {
        "feature_id": "2026-05-03-demo-feature",
        "tier": "focused",
        "current_phase": "execute",
        "phases": {
            "spec": {"status": "done"},
            "execute": {"status": "in_progress"},
        },
        "skipped": [],
        "deviations": [],
        "commits": [],
    }
    state.write_state(target, initial, schema_path=schemas_dir / "state.schema.json")

    with pytest.raises(state.StateError, match="current_phase is 'execute'"):
        state.complete_phase(target, phase="spec", schema_path=schemas_dir / "state.schema.json")


def test_complete_phase_rejects_when_status_not_in_progress(
    tmp_path: Path, schemas_dir: Path
) -> None:
    target = tmp_path / "state.json"
    initial = {
        "feature_id": "2026-05-03-demo-feature",
        "tier": "focused",
        "current_phase": "spec",
        "phases": {"spec": {"status": "pending"}},
        "skipped": [],
        "deviations": [],
        "commits": [],
    }
    state.write_state(target, initial, schema_path=schemas_dir / "state.schema.json")

    with pytest.raises(state.StateError, match="status is 'pending'"):
        state.complete_phase(target, phase="spec", schema_path=schemas_dir / "state.schema.json")


def test_complete_phase_raises_state_error_when_phases_missing(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    target.write_text(
        json.dumps({"feature_id": "x", "current_phase": "spec"}),
        encoding="utf-8",
    )

    with pytest.raises(state.StateError, match="`phases` mapping"):
        state.complete_phase(target, phase="spec")


def test_start_phase_raises_state_error_when_phases_missing(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    target.write_text(
        json.dumps({"feature_id": "x", "current_phase": "spec"}),
        encoding="utf-8",
    )

    with pytest.raises(state.StateError, match="`phases` mapping"):
        state.start_phase(target, phase="spec")


def _seed_feature_with_spec_in_progress(
    repo_root: Path,
    schemas_dir: Path,
    *,
    feature_id: str,
    spec_body: str,
) -> Path:
    """Build a tmp ``.forge/features/<id>/`` carrying state.json + SPEC.md."""
    feature = repo_root / ".forge" / "features" / feature_id
    feature.mkdir(parents=True)
    initial = {
        "feature_id": feature_id,
        "tier": "focused",
        "current_phase": "spec",
        "phases": {"spec": {"status": "in_progress", "started_at": "2026-05-12T10:00:00Z"}},
        "skipped": [],
        "deviations": [],
        "commits": [],
    }
    target = feature / "state.json"
    state.write_state(target, initial, schema_path=schemas_dir / "state.schema.json")
    (feature / "SPEC.md").write_text(spec_body, encoding="utf-8")
    return target


def test_complete_phase_refuses_spec_exit_with_high_anchor_findings(
    tmp_path: Path, schemas_dir: Path
) -> None:
    """Completing the ``spec`` phase must run the spec-semantic validators
    against the feature's SPEC.md and refuse the transition when any
    finding has severity ``HIGH`` or ``BLOCK``. The mechanical gate
    replaces the prose-only enforcement in the forge-spec skill — a
    missing-anchor SPEC must not be able to exit spec via
    ``complete_phase``."""
    repo = tmp_path / "repo"
    repo.mkdir()
    target = _seed_feature_with_spec_in_progress(
        repo,
        schemas_dir,
        feature_id="2026-05-12-anchored",
        spec_body=(
            "# Codebase Anchors\n"
            "- `src/does_not_exist.py:bar`\n"
            "# Scenarios\n"
            "Scenario: 1 demo\n"
            "# Acceptance Criteria\n"
            "1. crit-1 done\n"
        ),
    )

    on_disk_before = target.read_text(encoding="utf-8")

    with pytest.raises(state.StateError, match=r"HIGH|BLOCK") as exc:
        state.complete_phase(
            target,
            phase="spec",
            schema_path=schemas_dir / "state.schema.json",
        )

    assert "SPEC.md" in str(exc.value)
    assert target.read_text(encoding="utf-8") == on_disk_before, (
        "state.json must not be mutated when the gate refuses"
    )


def test_complete_phase_allows_spec_exit_when_anchors_resolve(
    tmp_path: Path, schemas_dir: Path
) -> None:
    """When SPEC.md's Codebase Anchors all resolve under the discovered
    repo root, ``complete_phase("spec")`` transitions normally."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "src" / "foo.py").write_text("def bar() -> None:\n    pass\n", encoding="utf-8")
    target = _seed_feature_with_spec_in_progress(
        repo,
        schemas_dir,
        feature_id="2026-05-12-clean",
        spec_body=(
            "# Codebase Anchors\n"
            "- `src/foo.py:bar`\n"
            "# Scenarios\n"
            "Scenario: 1 demo crit-1\n"
            "# Acceptance Criteria\n"
            "1. crit-1 done\n"
        ),
    )

    result = state.complete_phase(
        target,
        phase="spec",
        schema_path=schemas_dir / "state.schema.json",
        now="2026-05-12T12:00:00Z",
    )

    assert result["phases"]["spec"]["status"] == "done"
    assert result["phases"]["spec"]["completed_at"] == "2026-05-12T12:00:00Z"


def test_complete_phase_scenarios_isolation_anchor_findings_do_not_fire(
    tmp_path: Path, schemas_dir: Path
) -> None:
    """The anchor validator is scoped to the ``spec`` phase only. Completing
    ``scenarios`` must not run ``validate_anchors`` — features routinely
    amend SPEC after the spec phase closes, and a stale anchor mid-feature
    must not block scenarios exit. The scenarios gate runs only
    ``validate_scenarios``; this fixture's SPEC is scenarios-clean (every
    AC has a matching scenario) but anchor-dirty (path does not resolve),
    so the gate must let it through."""
    repo = tmp_path / "repo"
    feature = repo / ".forge" / "features" / "2026-05-12-non-spec"
    feature.mkdir(parents=True)
    initial = {
        "feature_id": "2026-05-12-non-spec",
        "tier": "standard",
        "current_phase": "scenarios",
        "phases": {
            "spec": {"status": "done"},
            "scenarios": {
                "status": "in_progress",
                "started_at": "2026-05-12T10:00:00Z",
            },
        },
        "skipped": [],
        "deviations": [],
        "commits": [],
    }
    target = feature / "state.json"
    state.write_state(target, initial, schema_path=schemas_dir / "state.schema.json")
    # SPEC is scenarios-clean (Scenario 1 maps to crit-1, no orphans, no
    # weasel words) but anchor-dirty (path does not exist; would be HIGH
    # if ``validate_anchors`` were in scope here).
    (feature / "SPEC.md").write_text(
        "# Codebase Anchors\n"
        "- `src/missing.py:bar`\n"
        "# Scenarios\n"
        "Scenario: 1 demo crit-1\n"
        "# Acceptance Criteria\n"
        "1. crit-1 done\n",
        encoding="utf-8",
    )

    result = state.complete_phase(
        target,
        phase="scenarios",
        schema_path=schemas_dir / "state.schema.json",
        now="2026-05-12T12:00:00Z",
    )

    assert result["phases"]["scenarios"]["status"] == "done"


def _seed_feature_with_scenarios_in_progress(
    repo_root: Path,
    schemas_dir: Path,
    *,
    feature_id: str,
    spec_body: str,
) -> Path:
    """Build a tmp ``.forge/features/<id>/`` carrying state.json + SPEC.md
    with ``current_phase=scenarios`` / ``status=in_progress``."""
    feature = repo_root / ".forge" / "features" / feature_id
    feature.mkdir(parents=True)
    initial = {
        "feature_id": feature_id,
        "tier": "standard",
        "current_phase": "scenarios",
        "phases": {
            "spec": {"status": "done"},
            "scenarios": {
                "status": "in_progress",
                "started_at": "2026-05-12T10:00:00Z",
            },
        },
        "skipped": [],
        "deviations": [],
        "commits": [],
    }
    target = feature / "state.json"
    state.write_state(target, initial, schema_path=schemas_dir / "state.schema.json")
    (feature / "SPEC.md").write_text(spec_body, encoding="utf-8")
    return target


def test_complete_phase_refuses_scenarios_exit_with_high_scenario_findings(
    tmp_path: Path, schemas_dir: Path
) -> None:
    """Completing the ``scenarios`` phase must run ``validate_scenarios``
    against SPEC.md and refuse the transition when any finding has severity
    ``HIGH`` or ``BLOCK``. The mechanical gate replaces the prose-only
    enforcement in the forge-scenarios skill — an orphan scenario or an
    AC with no matching scenario must not be able to exit scenarios via
    ``complete_phase``."""
    repo = tmp_path / "repo"
    repo.mkdir()
    target = _seed_feature_with_scenarios_in_progress(
        repo,
        schemas_dir,
        feature_id="2026-05-12-scen-bad",
        # AC 1 has no matching scenario (Scenario title carries no crit-1 /
        # Scenario 1 token), and the scenario itself is therefore an orphan.
        # Both produce HIGH findings.
        spec_body=(
            "# Scenarios\n"
            "Scenario: demo without ac reference\n"
            "# Acceptance Criteria\n"
            "1. crit-1 done\n"
        ),
    )

    on_disk_before = target.read_text(encoding="utf-8")

    with pytest.raises(state.StateError, match=r"HIGH|BLOCK") as exc:
        state.complete_phase(
            target,
            phase="scenarios",
            schema_path=schemas_dir / "state.schema.json",
        )

    assert "SPEC.md" in str(exc.value)
    assert target.read_text(encoding="utf-8") == on_disk_before, (
        "state.json must not be mutated when the scenarios gate refuses"
    )


def test_complete_phase_allows_scenarios_exit_when_mapping_is_clean(
    tmp_path: Path, schemas_dir: Path
) -> None:
    """When SPEC's scenarios↔acceptance mapping is clean (every AC has a
    referencing scenario, no orphans, no weasel words),
    ``complete_phase("scenarios")`` transitions normally."""
    repo = tmp_path / "repo"
    repo.mkdir()
    target = _seed_feature_with_scenarios_in_progress(
        repo,
        schemas_dir,
        feature_id="2026-05-12-scen-ok",
        spec_body=("# Scenarios\nScenario: 1 demo crit-1\n# Acceptance Criteria\n1. crit-1 done\n"),
    )

    result = state.complete_phase(
        target,
        phase="scenarios",
        schema_path=schemas_dir / "state.schema.json",
        now="2026-05-12T12:00:00Z",
    )

    assert result["phases"]["scenarios"]["status"] == "done"
    assert result["phases"]["scenarios"]["completed_at"] == "2026-05-12T12:00:00Z"


def _seed_feature_with_plan_in_progress(
    repo_root: Path,
    schemas_dir: Path,
    *,
    feature_id: str,
    spec_body: str,
    plan_body: str,
) -> Path:
    """Build a tmp ``.forge/features/<id>/`` carrying state.json + SPEC.md +
    PLAN.md with ``current_phase=plan`` / ``status=in_progress``."""
    feature = repo_root / ".forge" / "features" / feature_id
    feature.mkdir(parents=True)
    initial = {
        "feature_id": feature_id,
        "tier": "standard",
        "current_phase": "plan",
        "phases": {
            "spec": {"status": "done"},
            "scenarios": {"status": "done"},
            "plan": {
                "status": "in_progress",
                "started_at": "2026-05-12T10:00:00Z",
            },
        },
        "skipped": [],
        "deviations": [],
        "commits": [],
    }
    target = feature / "state.json"
    state.write_state(target, initial, schema_path=schemas_dir / "state.schema.json")
    (feature / "SPEC.md").write_text(spec_body, encoding="utf-8")
    (feature / "PLAN.md").write_text(plan_body, encoding="utf-8")
    return target


def test_complete_phase_refuses_plan_exit_with_high_plan_task_findings(
    tmp_path: Path, schemas_dir: Path
) -> None:
    """Completing the ``plan`` phase must run ``validate_plan_tasks``
    against PLAN.md (paired with SPEC.md) and refuse the transition when
    any finding has severity ``HIGH`` or ``BLOCK``. The mechanical gate
    replaces the prose-only enforcement in the forge-plan skill — a
    plan with an unblocked AC must not be able to exit plan via
    ``complete_phase``."""
    repo = tmp_path / "repo"
    repo.mkdir()
    target = _seed_feature_with_plan_in_progress(
        repo,
        schemas_dir,
        feature_id="2026-05-12-plan-bad",
        spec_body=("# Acceptance Criteria\n1. crit-1 done\n"),
        # PLAN has a slice that does not unblock crit-1 (`validate_plan_tasks`
        # emits HIGH "AC 1 unblocked by zero slices").
        plan_body=(
            "# Slice 1: setup\n**Files in scope:** `src/foo.py`\n**Acceptance:** unrelated text\n"
        ),
    )

    on_disk_before = target.read_text(encoding="utf-8")

    with pytest.raises(state.StateError, match=r"HIGH|BLOCK") as exc:
        state.complete_phase(
            target,
            phase="plan",
            schema_path=schemas_dir / "state.schema.json",
        )

    assert "PLAN.md" in str(exc.value)
    assert target.read_text(encoding="utf-8") == on_disk_before, (
        "state.json must not be mutated when the plan gate refuses"
    )


def test_complete_phase_refuses_plan_exit_with_high_verified_deps_findings(
    tmp_path: Path, schemas_dir: Path
) -> None:
    """A Verified Dependencies row missing required cells must also refuse
    the plan exit. The mechanical gate runs BOTH ``validate_plan_tasks``
    and ``validate_verified_deps``; either firing is enough to block."""
    repo = tmp_path / "repo"
    repo.mkdir()
    target = _seed_feature_with_plan_in_progress(
        repo,
        schemas_dir,
        feature_id="2026-05-12-plan-deps-bad",
        spec_body=("# Acceptance Criteria\n1. crit-1 done\n"),
        # Plan-tasks is clean (Slice 1 unblocks crit-1) so only the
        # Verified Dependencies table trips a HIGH (wildcard `*` version
        # is forbidden by master design §7.3).
        plan_body=(
            "# Slice 1: setup\n"
            "**Files in scope:** `src/foo.py`\n"
            "**Acceptance:** crit-1 met\n"
            "\n"
            "## Verified Dependencies\n"
            "| Package | Version / range | Registry | Source checked | Key APIs used | Notes |\n"
            "| --- | --- | --- | --- | --- | --- |\n"
            "| requests | * | pypi | https://pypi.org | get | hello |\n"
        ),
    )

    on_disk_before = target.read_text(encoding="utf-8")

    with pytest.raises(state.StateError, match=r"HIGH|BLOCK") as exc:
        state.complete_phase(
            target,
            phase="plan",
            schema_path=schemas_dir / "state.schema.json",
        )

    assert "PLAN.md" in str(exc.value)
    assert "verified-deps" in str(exc.value)
    assert target.read_text(encoding="utf-8") == on_disk_before


def test_complete_phase_allows_plan_exit_when_plan_artifacts_are_clean(
    tmp_path: Path, schemas_dir: Path
) -> None:
    """When PLAN.md slices unblock every AC and any Verified Dependencies
    table is well-formed, ``complete_phase("plan")`` transitions normally."""
    repo = tmp_path / "repo"
    repo.mkdir()
    target = _seed_feature_with_plan_in_progress(
        repo,
        schemas_dir,
        feature_id="2026-05-12-plan-ok",
        spec_body=("# Acceptance Criteria\n1. crit-1 done\n"),
        plan_body=(
            "# Slice 1: setup\n**Files in scope:** `src/foo.py`\n**Acceptance:** crit-1 met\n"
        ),
    )

    result = state.complete_phase(
        target,
        phase="plan",
        schema_path=schemas_dir / "state.schema.json",
        now="2026-05-12T12:00:00Z",
    )

    assert result["phases"]["plan"]["status"] == "done"
    assert result["phases"]["plan"]["completed_at"] == "2026-05-12T12:00:00Z"


def test_complete_phase_plan_isolation_scenario_findings_do_not_fire_on_plan(
    tmp_path: Path, schemas_dir: Path
) -> None:
    """The scenarios validator is scoped to the ``scenarios`` (and
    ``spec``) phase only. Completing ``plan`` must not run
    ``validate_scenarios`` — features routinely amend SPEC scenarios
    mid-feature and a mismatch must not block plan exit. This fixture's
    SPEC trips ``validate_scenarios`` HIGH (orphan scenario) but the plan
    gate must let the transition through because the PLAN-side checks
    are clean."""
    repo = tmp_path / "repo"
    repo.mkdir()
    target = _seed_feature_with_plan_in_progress(
        repo,
        schemas_dir,
        feature_id="2026-05-12-plan-isol",
        # SPEC has a scenario that does not map to any AC (orphan HIGH if
        # ``validate_scenarios`` were in scope here).
        spec_body=(
            "# Scenarios\n"
            "Scenario: orphan demo with no ac token\n"
            "# Acceptance Criteria\n"
            "1. crit-1 done\n"
        ),
        plan_body=(
            "# Slice 1: setup\n**Files in scope:** `src/foo.py`\n**Acceptance:** crit-1 met\n"
        ),
    )

    result = state.complete_phase(
        target,
        phase="plan",
        schema_path=schemas_dir / "state.schema.json",
        now="2026-05-12T12:00:00Z",
    )

    assert result["phases"]["plan"]["status"] == "done"


def _seed_feature_with_execute_in_progress(
    repo_root: Path,
    schemas_dir: Path,
    *,
    feature_id: str,
    deviations: list[dict[str, str]] | None = None,
    commits: list[dict[str, str]] | None = None,
    spec_body: str = "",
    decisions_body: str = "",
    slice_summary: str | None = None,
) -> Path:
    """Build a tmp ``.forge/features/<id>/`` carrying state.json with
    ``current_phase=execute`` / ``status=in_progress``.

    Optional artifacts (SPEC.md / decisions.md / slice-1.summary) are written
    only when the body string is supplied; absence is the common case for
    fixtures that exercise the deviations or tdd_evidence shape directly.
    """
    feature = repo_root / ".forge" / "features" / feature_id
    feature.mkdir(parents=True)
    initial: dict[str, Any] = {
        "feature_id": feature_id,
        "tier": "focused",
        "current_phase": "execute",
        "phases": {
            "spec": {"status": "done"},
            "execute": {
                "status": "in_progress",
                "started_at": "2026-05-12T10:00:00Z",
            },
        },
        "skipped": [],
        "deviations": deviations or [],
        "commits": commits or [],
    }
    target = feature / "state.json"
    state.write_state(target, initial, schema_path=schemas_dir / "state.schema.json")
    if spec_body:
        (feature / "SPEC.md").write_text(spec_body, encoding="utf-8")
    if decisions_body:
        (feature / "decisions.md").write_text(decisions_body, encoding="utf-8")
    if slice_summary is not None:
        (feature / "slice-1.summary").write_text(slice_summary, encoding="utf-8")
    return target


def test_complete_phase_refuses_execute_exit_with_high_deviation_findings(
    tmp_path: Path, schemas_dir: Path
) -> None:
    """Completing the ``execute`` phase must run ``validate_deviations``
    against the feature folder and refuse the transition when any
    finding has severity ``HIGH`` or ``BLOCK``. The mechanical gate
    replaces the prose-only enforcement in the forge-execute skill — a
    deviation whose cause is not recorded in decisions.md must not be
    able to exit execute via ``complete_phase``."""
    repo = tmp_path / "repo"
    repo.mkdir()
    target = _seed_feature_with_execute_in_progress(
        repo,
        schemas_dir,
        feature_id="2026-05-12-dev-bad",
        deviations=[
            {
                "phase": "execute",
                "cause": "missing toolchain on CI runner",
                "resolution": "pinned alternate image",
                "logged_at": "2026-05-12T11:00:00Z",
            }
        ],
        # decisions.md is non-empty but does NOT mention the deviation
        # cause, so ``validate_deviations`` emits HIGH.
        decisions_body=(
            "# Decisions Log\n\n## 2026-05-12 — unrelated decision\n**Context:** other\n"
        ),
    )

    on_disk_before = target.read_text(encoding="utf-8")

    with pytest.raises(state.StateError, match=r"HIGH|BLOCK") as exc:
        state.complete_phase(
            target,
            phase="execute",
            schema_path=schemas_dir / "state.schema.json",
        )

    assert "deviations" in str(exc.value)
    assert target.read_text(encoding="utf-8") == on_disk_before, (
        "state.json must not be mutated when the execute gate refuses"
    )


def test_complete_phase_refuses_execute_exit_with_block_tdd_evidence_findings(
    tmp_path: Path, schemas_dir: Path
) -> None:
    """An impl commit recorded in ``state.commits`` without a paired
    preceding test commit (and without a TDD Exception ADR) trips
    ``validate_tdd_evidence`` with a ``BLOCK`` finding. The execute gate
    must refuse the transition; ``BLOCK`` is the only severity the
    tdd_evidence check blocks on (per the SKILL prose)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    target = _seed_feature_with_execute_in_progress(
        repo,
        schemas_dir,
        feature_id="2026-05-12-tdd-bad",
        # Single impl commit, no preceding test commit recorded in
        # ``state.commits``; the slice summary maps AC-1 to that SHA, so
        # the validator can pair them up and emit
        # ``tdd_evidence:missing_test_pair`` at BLOCK severity.
        commits=[
            {
                "sha": "abcdef0",
                "phase": "execute",
                "subject": "feat(api): AC-1 implement login",
                "logged_at": "2026-05-12T11:00:00Z",
            }
        ],
        spec_body="# Acceptance Criteria\n1. crit-1 login works\n",
        slice_summary="AC-1: abcdef0\n",
    )

    on_disk_before = target.read_text(encoding="utf-8")

    with pytest.raises(state.StateError, match="BLOCK") as exc:
        state.complete_phase(
            target,
            phase="execute",
            schema_path=schemas_dir / "state.schema.json",
        )

    assert "tdd_evidence" in str(exc.value)
    assert target.read_text(encoding="utf-8") == on_disk_before


def test_complete_phase_allows_execute_exit_when_deviations_and_tdd_evidence_clean(
    tmp_path: Path, schemas_dir: Path
) -> None:
    """When ``state.deviations`` is empty and ``state.commits`` has a
    matched test+impl pair for every AC, ``complete_phase("execute")``
    transitions normally."""
    repo = tmp_path / "repo"
    repo.mkdir()
    target = _seed_feature_with_execute_in_progress(
        repo,
        schemas_dir,
        feature_id="2026-05-12-exec-ok",
        # Test commit FIRST, then impl, both mapped to AC-1 via the
        # slice summary; ``validate_tdd_evidence`` accepts the pair.
        commits=[
            {
                "sha": "aaaaaaa",
                "phase": "execute",
                "subject": "test(api): AC-1 login fixtures",
                "logged_at": "2026-05-12T10:30:00Z",
            },
            {
                "sha": "bbbbbbb",
                "phase": "execute",
                "subject": "feat(api): AC-1 implement login",
                "logged_at": "2026-05-12T10:31:00Z",
            },
        ],
        spec_body="# Acceptance Criteria\n1. crit-1 login works\n",
        slice_summary="AC-1: aaaaaaa\nAC-1: bbbbbbb\n",
    )

    result = state.complete_phase(
        target,
        phase="execute",
        schema_path=schemas_dir / "state.schema.json",
        now="2026-05-12T12:00:00Z",
    )

    assert result["phases"]["execute"]["status"] == "done"
    assert result["phases"]["execute"]["completed_at"] == "2026-05-12T12:00:00Z"


def test_complete_phase_execute_tdd_evidence_low_severity_does_not_block(
    tmp_path: Path, schemas_dir: Path
) -> None:
    """The execute gate's ``tdd_evidence`` check refuses ONLY on ``BLOCK``
    (per the forge-execute SKILL prose — ``LOW`` and ``INFO`` are
    advisory). A non-BLOCK tdd_evidence finding must NOT refuse the
    transition. This fixture has an AC whose only execute-phase commit
    is a docs commit; ``validate_tdd_evidence`` emits
    ``no_impl_commits`` at ``INFO`` severity. The phase must complete."""
    repo = tmp_path / "repo"
    repo.mkdir()
    target = _seed_feature_with_execute_in_progress(
        repo,
        schemas_dir,
        feature_id="2026-05-12-exec-info",
        commits=[
            {
                "sha": "ccccccc",
                "phase": "execute",
                "subject": "docs(api): AC-1 wording polish",
                "logged_at": "2026-05-12T10:30:00Z",
            }
        ],
        spec_body="# Acceptance Criteria\n1. crit-1 login works\n",
        slice_summary="AC-1: ccccccc\n",
    )

    result = state.complete_phase(
        target,
        phase="execute",
        schema_path=schemas_dir / "state.schema.json",
        now="2026-05-12T12:00:00Z",
    )

    assert result["phases"]["execute"]["status"] == "done"


def test_finish_feature_sets_current_phase_done_without_phases_entry(
    tmp_path: Path, schemas_dir: Path
) -> None:
    target = tmp_path / "state.json"
    initial = {
        "feature_id": "2026-05-03-demo-feature",
        "tier": "focused",
        "current_phase": "verify",
        "phases": {
            "spec": {"status": "done"},
            "execute": {"status": "done"},
            "verify": {"status": "done"},
        },
        "skipped": [],
        "deviations": [],
        "commits": [],
    }
    state.write_state(target, initial, schema_path=schemas_dir / "state.schema.json")

    result = state.finish_feature(target, schema_path=schemas_dir / "state.schema.json")

    assert result["current_phase"] == "done"
    assert "done" not in result["phases"]


def test_start_phase_coerces_string_path(tmp_path: Path, schemas_dir: Path) -> None:
    """A ``str`` ``path`` must transition identically to the ``Path`` form.

    Agent callers improvising on the call shape pass a ``str`` state.json
    path; the helper calls ``state_lock(path)`` immediately, whose first
    line calls ``state_path.exists()`` and ``state_path.with_name(...)`` —
    both ``Path`` methods that trip a cryptic ``AttributeError`` deep
    inside the lock-acquisition chain when no boundary coercion sits at
    the entry. The string form must transition the phase identically to
    the ``Path`` form for the same inputs.
    """
    target = tmp_path / "state.json"
    initial = {
        "feature_id": "2026-05-12-coerce-start",
        "tier": "focused",
        "current_phase": "spec",
        "phases": {
            "spec": {
                "status": "done",
                "started_at": "2026-05-12T10:00:00Z",
                "completed_at": "2026-05-12T11:30:00Z",
            }
        },
        "skipped": [],
        "deviations": [],
        "commits": [],
    }
    schema_path = schemas_dir / "state.schema.json"
    state.write_state(target, initial, schema_path=schema_path)

    result = state.start_phase(
        str(target),
        phase="execute",
        schema_path=schema_path,
        now="2026-05-12T11:35:00Z",
    )

    assert result["current_phase"] == "execute"
    assert result["phases"]["execute"] == {
        "status": "in_progress",
        "started_at": "2026-05-12T11:35:00Z",
    }


def test_complete_phase_coerces_string_path(tmp_path: Path, schemas_dir: Path) -> None:
    """A ``str`` ``path`` must complete the phase identically to the ``Path`` form.

    Agent callers improvising on the call shape pass a ``str`` state.json
    path; the helper calls ``state_lock(path)`` immediately which invokes
    ``path.exists()`` and ``path.with_name(...)``, both ``Path`` methods,
    tripping a cryptic ``AttributeError`` deep inside the lock-acquisition
    chain when no boundary coercion sits at the entry. The string form must
    transition the phase identically to the ``Path`` form for the same
    inputs.
    """
    target = tmp_path / "state.json"
    initial = {
        "feature_id": "2026-05-12-coerce-demo",
        "tier": "focused",
        "current_phase": "spec",
        "phases": {"spec": {"status": "in_progress", "started_at": "2026-05-12T10:00:00Z"}},
        "skipped": [],
        "deviations": [],
        "commits": [],
    }
    schema_path = schemas_dir / "state.schema.json"
    state.write_state(target, initial, schema_path=schema_path)

    result = state.complete_phase(
        str(target),
        phase="spec",
        schema_path=schema_path,
        now="2026-05-12T11:30:00Z",
    )

    assert result["phases"]["spec"]["status"] == "done"
    assert result["phases"]["spec"]["completed_at"] == "2026-05-12T11:30:00Z"


def test_feature_folder_exists_coerces_string_repo_root(tmp_path: Path) -> None:
    """A ``str`` ``repo_root`` must return the same result as the ``Path`` form.

    Agent callers improvising on the call shape pass a ``str`` repo path; the
    helper composes ``repo_root / ".forge" / "features" / ...`` immediately,
    which trips a cryptic ``TypeError`` at the first ``/`` operator when no
    boundary coercion sits at the entry. The string form must return the
    same boolean as the ``Path`` form for the same inputs.
    """
    feature_id = "2026-05-12-alpha"
    (tmp_path / ".forge" / "features" / feature_id).mkdir(parents=True)

    assert state.feature_folder_exists(str(tmp_path), feature_id) is True
    assert state.feature_folder_exists(str(tmp_path), "2026-05-12-missing") is False


def test_record_refined_idea_coerces_string_path(tmp_path: Path, schemas_dir: Path) -> None:
    """A ``str`` ``path`` must persist the refined idea identically to the ``Path`` form.

    Agent callers improvising on the call shape pass a ``str`` state.json
    path; the helper calls ``state_lock(path)`` immediately which invokes
    ``path.exists()`` and ``path.with_name(...)`` — both ``Path`` methods
    that trip a cryptic ``AttributeError`` deep inside the lock-acquisition
    chain when no boundary coercion sits at the entry. The string form
    must persist the same ``refined_idea`` as the ``Path`` form.
    """
    target = tmp_path / "state.json"
    initial = {
        "feature_id": "2026-05-12-coerce-refine-idea",
        "tier": "full",
        "current_phase": "refine",
        "phases": {"refine": {"status": "in_progress", "started_at": "2026-05-12T10:00:00Z"}},
        "skipped": [],
        "deviations": [],
        "commits": [],
        "routing": {
            "idea": "demo",
            "final_tier": "full",
            "decided_at": "2026-05-12T09:00:00Z",
            "constitution_present": False,
        },
    }
    schema_path = schemas_dir / "state.schema.json"
    state.write_state(target, initial, schema_path=schema_path)

    result = state.record_refined_idea(
        str(target),
        refined="A clearly stated refined idea paragraph.",
        schema_path=schema_path,
    )

    assert result["refined_idea"] == "A clearly stated refined idea paragraph."


def test_record_routing_decision_coerces_string_path(tmp_path: Path, schemas_dir: Path) -> None:
    """A ``str`` ``path`` must record the routing block identically to the ``Path`` form.

    Agent callers improvising on the call shape pass a ``str`` state.json
    path; the helper calls ``state_lock(path)`` immediately which invokes
    ``path.exists()`` and ``path.with_name(...)`` — both ``Path`` methods
    that trip a cryptic ``AttributeError`` deep inside the lock-acquisition
    chain when no boundary coercion sits at the entry. The string form
    must persist the same ``routing`` block as the ``Path`` form for the
    same inputs.
    """
    target = tmp_path / "state.json"
    initial = {
        "feature_id": "2026-05-12-coerce-routing",
        "tier": "focused",
        "current_phase": "spec",
        "phases": {"spec": {"status": "in_progress", "started_at": "2026-05-12T10:00:00Z"}},
        "skipped": [],
        "deviations": [],
        "commits": [],
    }
    schema_path = schemas_dir / "state.schema.json"
    state.write_state(target, initial, schema_path=schema_path)

    result = state.record_routing_decision(
        str(target),
        idea="ship the thing",
        final_tier="focused",
        rationale="tiny scope",
        schema_path=schema_path,
        now="2026-05-12T11:30:00Z",
    )

    assert result["routing"]["idea"] == "ship the thing"
    assert result["routing"]["final_tier"] == "focused"
    assert result["routing"]["decided_at"] == "2026-05-12T11:30:00Z"


def test_finish_feature_coerces_string_path(tmp_path: Path, schemas_dir: Path) -> None:
    """A ``str`` ``path`` must finish the feature identically to the ``Path`` form.

    Agent callers improvising on the call shape pass a ``str`` state.json
    path; the helper calls ``state_lock(path)`` immediately which invokes
    ``path.exists()`` and ``path.with_name(...)`` — both ``Path`` methods
    that trip a cryptic ``AttributeError`` deep inside the lock-acquisition
    chain when no boundary coercion sits at the entry. The string form
    must transition ``current_phase`` to ``"done"`` identically to the
    ``Path`` form for the same inputs.
    """
    target = tmp_path / "state.json"
    initial = {
        "feature_id": "2026-05-12-coerce-finish",
        "tier": "focused",
        "current_phase": "verify",
        "phases": {
            "verify": {
                "status": "done",
                "started_at": "2026-05-12T10:00:00Z",
                "completed_at": "2026-05-12T11:30:00Z",
            }
        },
        "skipped": [],
        "deviations": [],
        "commits": [],
    }
    schema_path = schemas_dir / "state.schema.json"
    state.write_state(target, initial, schema_path=schema_path)

    result = state.finish_feature(str(target), schema_path=schema_path)

    assert result["current_phase"] == "done"


def test_write_state_coerces_string_path(tmp_path: Path, schemas_dir: Path) -> None:
    """A ``str`` ``path`` must write identically to the ``Path`` form.

    Agent callers improvising on the call shape pass a ``str`` state.json
    destination; ``write_state`` delegates to ``_atomic_write_json`` which
    calls ``path.parent`` and ``path.parent.mkdir(...)`` — both ``Path``
    attributes that trip a cryptic ``AttributeError`` deep inside the
    durable-write chain when no boundary coercion sits at the entry. The
    string form must persist the same payload as the ``Path`` form.
    """
    target = tmp_path / "state.json"
    payload = {
        "feature_id": "2026-05-12-coerce-write",
        "tier": "focused",
        "current_phase": "spec",
        "phases": {"spec": {"status": "in_progress", "started_at": "2026-05-12T10:00:00Z"}},
        "skipped": [],
        "deviations": [],
        "commits": [],
    }
    schema_path = schemas_dir / "state.schema.json"

    state.write_state(str(target), payload, schema_path=schema_path)

    assert state.read_state(target, schema_path=schema_path) == payload


def test_read_state_coerces_string_path(tmp_path: Path, schemas_dir: Path) -> None:
    """A ``str`` ``path`` must read identically to the ``Path`` form.

    Agent callers improvising on the call shape pass a ``str`` state.json
    path; ``read_state`` calls ``path.exists()`` and ``path.read_text(...)``
    immediately, both ``Path`` methods that trip a cryptic ``AttributeError``
    at the first line when no boundary coercion sits at the entry. The
    string form must return the same parsed payload as the ``Path`` form.
    """
    target = tmp_path / "state.json"
    initial = {
        "feature_id": "2026-05-12-coerce-read",
        "tier": "focused",
        "current_phase": "spec",
        "phases": {"spec": {"status": "in_progress", "started_at": "2026-05-12T10:00:00Z"}},
        "skipped": [],
        "deviations": [],
        "commits": [],
    }
    schema_path = schemas_dir / "state.schema.json"
    state.write_state(target, initial, schema_path=schema_path)

    result = state.read_state(str(target), schema_path=schema_path)

    assert result == initial


def test_record_commit_coerces_string_path(tmp_path: Path, schemas_dir: Path) -> None:
    """A ``str`` ``path`` must append the commit identically to the ``Path`` form.

    Agent callers improvising on the call shape pass a ``str`` state.json
    path; the helper calls ``state_lock(path)`` immediately, which invokes
    ``state_path.exists()`` and ``state_path.with_name(...)`` — both ``Path``
    methods that trip a cryptic ``AttributeError`` deep inside the
    lock-acquisition chain when no boundary coercion sits at the entry. The
    string form must append the commit entry identically to the ``Path``
    form for the same inputs.
    """
    target = tmp_path / "state.json"
    initial = {
        "feature_id": "2026-05-12-coerce-commit",
        "tier": "focused",
        "current_phase": "execute",
        "phases": {"execute": {"status": "in_progress", "started_at": "2026-05-12T10:00:00Z"}},
        "skipped": [],
        "deviations": [],
        "commits": [],
    }
    schema_path = schemas_dir / "state.schema.json"
    state.write_state(target, initial, schema_path=schema_path)

    result = state.record_commit(
        str(target),
        sha="abc1234",
        phase="execute",
        subject="feat(demo): add thing",
        logged_at="2026-05-12T11:30:00Z",
        schema_path=schema_path,
    )

    assert result["commits"] == [
        {
            "sha": "abc1234",
            "phase": "execute",
            "subject": "feat(demo): add thing",
            "logged_at": "2026-05-12T11:30:00Z",
        }
    ]
