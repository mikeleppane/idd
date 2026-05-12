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


def test_repo_with_committed_feature_returns_no_findings(tmp_path: Path) -> None:
    """A feature folder that already has at least one commit recorded is
    NOT an orphan candidate, so the scan returns clean.

    Renamed from ``test_clean_repo_returns_no_findings``: the original name
    overstated coverage because the fixture has a commit, which is the
    inverse of "clean repo" semantics.
    See ``test_archived_done_feature_returns_no_findings`` for the
    truly-clean repo case and
    ``test_fresh_feature_with_no_commits_produces_low_orphan_finding``
    for the documented orphan-candidate behavior.
    """
    _seed_state(
        tmp_path / ".forge" / "features" / "2026-05-04-clean",
        commits=[{"sha": "abc1234", "subject": "feat: stuff", "phase": "spec"}],
    )
    _seed_spec(tmp_path / ".forge" / "features" / "2026-05-04-clean", "clean")

    findings = validate.validate_health(tmp_path)

    assert findings == []


def test_archived_done_feature_returns_no_findings(tmp_path: Path) -> None:
    """A feature archived under ``.forge/specs/<slug>/SPEC.md`` (with the
    matching ``evidence:`` link) and NO live entry under
    ``.forge/features/`` produces zero findings — the originally-intended
    "clean repo with one shipped feature" semantics.

    Restores the coverage that the prior ``test_clean_repo_returns_no_findings``
    was supposed to provide before it was weakened with a forced commit entry.
    """
    canonical = tmp_path / ".forge" / "specs" / "shipped-feature"
    canonical.mkdir(parents=True)
    (canonical / "SPEC.md").write_text(
        "---\nid: 2026-04-01-shipped-feature\nstatus: shipped\n"
        "tier: focused\ncreated: 2026-04-01\ncapability: shipped-feature\n"
        "evidence: ../../features/archive/2026-04-01-shipped-feature\n"
        "---\n# Intent\nshipped via /forge:ship.\n",
        encoding="utf-8",
    )

    findings = validate.validate_health(tmp_path)

    assert findings == [], (
        f"a fully archived feature with evidence link must produce zero findings; got {findings}"
    )


def test_fresh_feature_with_no_commits_produces_low_orphan_finding(tmp_path: Path) -> None:
    """A freshly-seeded feature (current_phase=spec, no commits, only the
    canonical seed files) MUST produce exactly one LOW orphan finding.

    Locks the contract: orphan detection fires for the /forge:do
    focused/standard pre-seed, not just the legacy refine seed.
    """
    folder = tmp_path / ".forge" / "features" / "2026-05-08-fresh"
    _seed_state(folder)  # default: current_phase=spec, in_progress, commits=[]
    _seed_spec(folder, "fresh")

    findings = validate.validate_health(tmp_path)

    orphan = [f for f in findings if f.severity == "LOW" and "orphan" in f.message.lower()]
    assert len(orphan) == 1, f"expected exactly one LOW orphan finding; got {findings}"
    assert "2026-05-08-fresh" in orphan[0].message


def test_orphan_feature_folder_low(tmp_path: Path) -> None:
    folder = tmp_path / ".forge" / "features" / "2026-05-04-orphan"
    _seed_state(folder, current_phase="refine", phases={"refine": {"status": "in_progress"}})
    _seed_spec(folder, "orphan")  # only templated files; no commits

    findings = validate.validate_health(tmp_path)

    assert any(f.severity == "LOW" and "orphan" in f.message.lower() for f in findings)


def test_feature_folder_name_mismatch_high(tmp_path: Path) -> None:
    folder = tmp_path / ".forge" / "features" / "2026-05-04-folder-name"
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
    folder = tmp_path / ".forge" / "features" / "2026-05-04-bad-schema"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(
        json.dumps({"feature_id": None, "tier": "ninja"}),
        encoding="utf-8",
    )

    findings = validate.validate_health(tmp_path)

    assert any(f.severity == "BLOCK" and "schema" in f.message.lower() for f in findings)


def test_current_phase_not_in_phases_enum_blocks(tmp_path: Path) -> None:
    """Schema-valid state.json whose current_phase doesn't appear in phases."""
    folder = tmp_path / ".forge" / "features" / "2026-05-04-bad-phase"
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
    canonical = tmp_path / ".forge" / "specs" / "auth"
    canonical.mkdir(parents=True)
    (canonical / "SPEC.md").write_text(
        "---\nid: 2026-04-01-auth\nstatus: shipped\ntier: standard\n"
        "created: 2026-04-01\ncapability: auth\n---\n# Intent\nshipped.\n",
        encoding="utf-8",
    )

    findings = validate.validate_health(tmp_path)

    assert any(f.severity == "LOW" and "evidence" in f.message.lower() for f in findings)


def test_state_json_schema_broken_block(tmp_path: Path) -> None:
    folder = tmp_path / ".forge" / "features" / "2026-05-04-broken"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text("{not json", encoding="utf-8")

    findings = validate.validate_health(tmp_path)

    assert any(f.severity == "BLOCK" and "state.json" in f.message for f in findings)


def test_state_json_as_directory_does_not_crash(tmp_path: Path) -> None:
    """A `state.json` that is a directory (typo: `mkdir state.json`) must
    surface as BLOCK, not crash the scan with IsADirectoryError."""
    folder = tmp_path / ".forge" / "features" / "2026-05-04-statedir"
    state_dir = folder / "state.json"
    state_dir.mkdir(parents=True, exist_ok=True)

    findings = validate.validate_health(tmp_path)

    assert any(f.severity == "BLOCK" and "state.json" in f.message for f in findings)


def test_feature_missing_state_json_high(tmp_path: Path) -> None:
    folder = tmp_path / ".forge" / "features" / "2026-05-04-no-state"
    folder.mkdir(parents=True, exist_ok=True)
    _seed_spec(folder, "x")

    findings = validate.validate_health(tmp_path)

    assert any(
        f.severity == "HIGH" and "state.json" in f.message and "missing" in f.message.lower()
        for f in findings
    )


def test_done_feature_not_archived_medium(tmp_path: Path) -> None:
    folder = tmp_path / ".forge" / "features" / "2026-05-04-done"
    _seed_state(folder, current_phase="done", phases={})
    _seed_spec(folder, "done")

    findings = validate.validate_health(tmp_path)

    assert any(
        f.severity == "MEDIUM" and "done" in f.message.lower() and "archive" in f.message.lower()
        for f in findings
    )


def test_capability_collision_high(tmp_path: Path) -> None:
    a = tmp_path / ".forge" / "features" / "2026-05-04-a"
    b = tmp_path / ".forge" / "features" / "2026-05-04-b"
    _seed_state(a)
    _seed_state(b)
    _seed_spec(a, "shared")
    _seed_spec(b, "shared")

    findings = validate.validate_health(tmp_path)

    assert any(f.severity == "HIGH" and "shared" in f.message for f in findings)


def test_unmerged_approved_change_medium(tmp_path: Path) -> None:
    canonical_dir = tmp_path / ".forge" / "specs" / "auth"
    canonical_dir.mkdir(parents=True)
    (canonical_dir / "SPEC.md").write_text(
        "---\nid: 2026-04-01-auth\nstatus: shipped\ntier: standard\n"
        "created: 2026-04-01\ncapability: auth\n---\n# Intent\nshipped.\n",
        encoding="utf-8",
    )
    change_dir = tmp_path / ".forge" / "changes" / "2026-05-04-auth-tweak"
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
    change_dir = tmp_path / ".forge" / "changes" / "2026-05-04-dangling"
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
    constitution = tmp_path / ".forge" / "CONSTITUTION.md"
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


def test_validate_health_warns_when_forge_dir_absent(tmp_path: Path) -> None:
    """A directory with no ``.forge/`` must surface a single WARN finding so
    users who mistype ``--repo-root`` or run from the wrong cwd see a clear
    pointer toward bootstrap instead of an empty 'all clean' report."""
    findings = validate.validate_health(tmp_path)

    assert len(findings) == 1, f"expected exactly one finding; got {findings}"
    only = findings[0]
    assert only.severity == "WARN"
    assert only.target == "health"
    assert only.file == tmp_path
    assert ".forge" in only.message
    lower = only.message.lower()
    assert "forge:do" in lower or "bootstrap" in lower, only.message


def test_validate_health_no_warn_when_forge_dir_present_but_empty(tmp_path: Path) -> None:
    """An empty ``.forge/`` directory means the repo IS a forge repo (even if
    nothing has been seeded yet) — the missing-dir warning must not fire.
    Other findings are allowed; only the missing-.forge message is forbidden."""
    (tmp_path / ".forge").mkdir()

    findings = validate.validate_health(tmp_path)

    missing_dir_warnings = [
        f
        for f in findings
        if f.severity == "WARN" and ".forge" in f.message and "bootstrap" in f.message.lower()
    ]
    assert missing_dir_warnings == [], (
        f"empty .forge/ must not emit the missing-dir WARN; got {missing_dir_warnings}"
    )
