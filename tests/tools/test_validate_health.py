"""Tests for validate_health repo-wide scan."""

from __future__ import annotations

import json
from pathlib import Path

from tools import validate


def _seed_state(folder: Path, **payload_overrides: object) -> None:
    """Write a minimal valid state.json into `folder` with optional overrides."""
    folder.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "feature_id": folder.name,
        "tier": "focused",
        "current_phase": "spec",
        "phases": {"spec": {"status": "in_progress"}},
        "skipped": [],
        "deviations": [],
        "commits": [],
    }
    payload.update(payload_overrides)
    (folder / "state.json").write_text(json.dumps(payload), encoding="utf-8")


def _seed_spec(folder: Path, capability: str = "x") -> None:
    (folder / "SPEC.md").write_text(
        f"---\nid: {folder.name}\nstatus: draft\ntier: focused\n"
        f"created: 2026-05-04\ncapability: {capability}\n---\n# Intent\nx.\n",
        encoding="utf-8",
    )


def test_clean_repo_returns_no_findings(tmp_path: Path) -> None:
    _seed_state(tmp_path / ".idd" / "features" / "2026-05-04-clean")
    _seed_spec(tmp_path / ".idd" / "features" / "2026-05-04-clean", "clean")

    findings = validate.validate_health(tmp_path)

    assert findings == []


def test_orphan_feature_folder_low(tmp_path: Path) -> None:
    folder = tmp_path / ".idd" / "features" / "2026-05-04-orphan"
    _seed_state(folder, current_phase="refine", phases={"refine": {"status": "in_progress"}})
    _seed_spec(folder, "orphan")  # only templated files; no commits

    findings = validate.validate_health(tmp_path)

    assert any(f.severity == "LOW" and "orphan" in f.message.lower() for f in findings)


def test_feature_folder_name_mismatch_high(tmp_path: Path) -> None:
    folder = tmp_path / ".idd" / "features" / "2026-05-04-folder-name"
    folder.mkdir(parents=True, exist_ok=True)
    payload = {
        "feature_id": "2026-05-04-different-id",
        "tier": "focused",
        "current_phase": "spec",
        "phases": {"spec": {"status": "in_progress"}},
        "skipped": [],
        "deviations": [],
        "commits": [],
    }
    (folder / "state.json").write_text(json.dumps(payload), encoding="utf-8")
    _seed_spec(folder, "x")

    findings = validate.validate_health(tmp_path)

    assert any(f.severity == "HIGH" and "feature_id" in f.message for f in findings)


def test_state_json_schema_violation_blocks(tmp_path: Path) -> None:
    """Syntactically-valid JSON that fails state.schema.json must BLOCK.
    Earlier draft only checked parse errors; this regression case caught a
    null feature_id + bogus tier slipping through."""
    folder = tmp_path / ".idd" / "features" / "2026-05-04-bad-schema"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(
        json.dumps({"feature_id": None, "tier": "ninja"}),
        encoding="utf-8",
    )

    findings = validate.validate_health(tmp_path)

    assert any(f.severity == "BLOCK" and "schema" in f.message.lower() for f in findings)


def test_current_phase_not_in_phases_enum_blocks(tmp_path: Path) -> None:
    """Schema-valid state.json whose current_phase doesn't appear in phases."""
    folder = tmp_path / ".idd" / "features" / "2026-05-04-bad-phase"
    _seed_state(
        folder,
        current_phase="execute",
        phases={"spec": {"status": "in_progress"}},
    )
    _seed_spec(folder, "bad-phase")

    findings = validate.validate_health(tmp_path)

    assert any(
        f.severity == "BLOCK" and "current_phase" in f.message and "execute" in f.message
        for f in findings
    )


def test_canonical_spec_missing_evidence_low(tmp_path: Path) -> None:
    canonical = tmp_path / ".idd" / "specs" / "auth"
    canonical.mkdir(parents=True)
    (canonical / "SPEC.md").write_text(
        "---\nid: 2026-04-01-auth\nstatus: shipped\ntier: standard\n"
        "created: 2026-04-01\ncapability: auth\n---\n# Intent\nshipped.\n",
        encoding="utf-8",
    )

    findings = validate.validate_health(tmp_path)

    assert any(f.severity == "LOW" and "evidence" in f.message.lower() for f in findings)


def test_state_json_schema_broken_block(tmp_path: Path) -> None:
    folder = tmp_path / ".idd" / "features" / "2026-05-04-broken"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text("{not json", encoding="utf-8")

    findings = validate.validate_health(tmp_path)

    assert any(f.severity == "BLOCK" and "state.json" in f.message for f in findings)


def test_state_json_as_directory_does_not_crash(tmp_path: Path) -> None:
    """A `state.json` that is a directory (typo: `mkdir state.json`) must
    surface as BLOCK, not crash the scan with IsADirectoryError."""
    folder = tmp_path / ".idd" / "features" / "2026-05-04-statedir"
    state_dir = folder / "state.json"
    state_dir.mkdir(parents=True, exist_ok=True)

    findings = validate.validate_health(tmp_path)

    assert any(f.severity == "BLOCK" and "state.json" in f.message for f in findings)


def test_feature_missing_state_json_high(tmp_path: Path) -> None:
    folder = tmp_path / ".idd" / "features" / "2026-05-04-no-state"
    folder.mkdir(parents=True, exist_ok=True)
    _seed_spec(folder, "x")

    findings = validate.validate_health(tmp_path)

    assert any(
        f.severity == "HIGH" and "state.json" in f.message and "missing" in f.message.lower()
        for f in findings
    )


def test_done_feature_not_archived_medium(tmp_path: Path) -> None:
    folder = tmp_path / ".idd" / "features" / "2026-05-04-done"
    _seed_state(folder, current_phase="done", phases={})
    _seed_spec(folder, "done")

    findings = validate.validate_health(tmp_path)

    assert any(
        f.severity == "MEDIUM" and "done" in f.message.lower() and "archive" in f.message.lower()
        for f in findings
    )


def test_capability_collision_high(tmp_path: Path) -> None:
    a = tmp_path / ".idd" / "features" / "2026-05-04-a"
    b = tmp_path / ".idd" / "features" / "2026-05-04-b"
    _seed_state(a)
    _seed_state(b)
    _seed_spec(a, "shared")
    _seed_spec(b, "shared")

    findings = validate.validate_health(tmp_path)

    assert any(f.severity == "HIGH" and "shared" in f.message for f in findings)


def test_unmerged_approved_change_medium(tmp_path: Path) -> None:
    canonical_dir = tmp_path / ".idd" / "specs" / "auth"
    canonical_dir.mkdir(parents=True)
    (canonical_dir / "SPEC.md").write_text(
        "---\nid: 2026-04-01-auth\nstatus: shipped\ntier: standard\n"
        "created: 2026-04-01\ncapability: auth\n---\n# Intent\nshipped.\n",
        encoding="utf-8",
    )
    change_dir = tmp_path / ".idd" / "changes" / "2026-05-04-auth-tweak"
    change_dir.mkdir(parents=True)
    (change_dir / "proposal.md").write_text(
        "---\nid: 2026-05-04-auth-tweak\naffects_capability: auth\n"
        "status: approved\ncreated: 2026-05-04\n---\n"
        "# Change: tweak\n## Affects\n- spec: auth\n## Delta\n+ ADD: scenario y\n"
        "## Rationale\nok.\n",
        encoding="utf-8",
    )

    findings = validate.validate_health(tmp_path)

    assert any(f.severity == "MEDIUM" and "approved" in f.message.lower() for f in findings)


def test_change_dangles_against_missing_canonical_high(tmp_path: Path) -> None:
    change_dir = tmp_path / ".idd" / "changes" / "2026-05-04-dangling"
    change_dir.mkdir(parents=True)
    (change_dir / "proposal.md").write_text(
        "---\nid: 2026-05-04-dangling\naffects_capability: ghost\n"
        "status: draft\ncreated: 2026-05-04\n---\n"
        "# Change: dangling\n## Affects\n- spec: ghost\n## Delta\n+ ADD: x\n"
        "## Rationale\nok.\n",
        encoding="utf-8",
    )

    findings = validate.validate_health(tmp_path)

    assert any(
        f.severity == "HIGH" and "ghost" in f.message and "canonical" in f.message.lower()
        for f in findings
    )


def test_constitution_article_count_warn(tmp_path: Path) -> None:
    """13 articles trips WARN at >= 12 per validate_constitution caps."""
    constitution = tmp_path / ".idd" / "CONSTITUTION.md"
    constitution.parent.mkdir(parents=True, exist_ok=True)
    article_blocks = "\n".join(
        f"## Article {n} — Article {n} title [SHOULD]\n**Rule:** R.\n**Exception:** None."
        for n in range(1, 14)
    )
    constitution.write_text(
        f"---\nversion: 0.1.0\ncreated: 2026-01-01\n---\n\n# Project Constitution\n\n{article_blocks}\n",
        encoding="utf-8",
    )

    findings = validate.validate_health(tmp_path)

    assert any(f.severity == "WARN" and "article count" in f.message.lower() for f in findings)


def test_missing_idd_root_returns_no_findings(tmp_path: Path) -> None:
    findings = validate.validate_health(tmp_path)
    assert findings == []
