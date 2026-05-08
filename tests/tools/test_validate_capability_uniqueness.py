"""Tests for validate_capability_uniqueness across .forge/specs and .forge/features."""

from __future__ import annotations

import json
from pathlib import Path

from tools import validate


def _make_canonical_spec(repo_root: Path, capability: str) -> None:
    folder = repo_root / ".forge" / "specs" / capability
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SPEC.md").write_text(
        f"---\nid: 2026-01-01-{capability}\nstatus: shipped\ntier: standard\n"
        f"created: 2026-01-01\ncapability: {capability}\n---\n# Intent\nshipped.\n",
        encoding="utf-8",
    )


def _make_feature(repo_root: Path, feature_id: str, capability: str) -> None:
    folder = repo_root / ".forge" / "features" / feature_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SPEC.md").write_text(
        f"---\nid: {feature_id}\nstatus: draft\ntier: focused\n"
        f"created: 2026-05-04\ncapability: {capability}\n---\n# Intent\nx.\n",
        encoding="utf-8",
    )
    (folder / "state.json").write_text(
        json.dumps(
            {
                "feature_id": feature_id,
                "tier": "focused",
                "current_phase": "spec",
                "phases": {"spec": {"status": "in_progress"}},
                "skipped": [],
                "deviations": [],
                "commits": [],
            }
        ),
        encoding="utf-8",
    )


def test_no_collision_returns_no_findings(tmp_path: Path) -> None:
    _make_canonical_spec(tmp_path, "auth")
    _make_feature(tmp_path, "2026-05-04-coupons", "coupons")

    findings = validate.validate_capability_uniqueness(tmp_path)

    assert findings == []


def test_feature_collides_with_canonical_high(tmp_path: Path) -> None:
    _make_canonical_spec(tmp_path, "auth")
    _make_feature(tmp_path, "2026-05-04-auth-rewrite", "auth")

    findings = validate.validate_capability_uniqueness(tmp_path)

    assert any(f.severity == "HIGH" and "auth" in f.message for f in findings)


def test_two_active_features_same_capability_high(tmp_path: Path) -> None:
    _make_feature(tmp_path, "2026-05-04-coupons-v1", "coupons")
    _make_feature(tmp_path, "2026-05-04-coupons-v2", "coupons")

    findings = validate.validate_capability_uniqueness(tmp_path)

    assert any(f.severity == "HIGH" and "coupons" in f.message for f in findings)


def test_active_collides_with_archived_high(tmp_path: Path) -> None:
    """Slug reuse across active and archived must surface (HIGH).
    The user should run /forge:change rather than reuse a previously-shipped slug."""
    _make_feature(tmp_path, "2026-05-04-coupons-redo", "coupons")
    archived = tmp_path / ".forge" / "features" / "archive" / "2026-04-01-coupons"
    archived.mkdir(parents=True, exist_ok=True)
    (archived / "SPEC.md").write_text(
        "---\nid: 2026-04-01-coupons\nstatus: shipped\ntier: focused\n"
        "created: 2026-04-01\ncapability: coupons\n---\n# Intent\narchived.\n",
        encoding="utf-8",
    )

    findings = validate.validate_capability_uniqueness(tmp_path)

    assert any(f.severity == "HIGH" and "coupons" in f.message for f in findings)


def test_canonical_plus_archived_only_is_normal(tmp_path: Path) -> None:
    """A canonical spec for `auth` and its source archived feature share the
    slug by design (canonical's `evidence:` points at the archive). Must NOT
    flag — that's the post-ship steady state, not a collision."""
    _make_canonical_spec(tmp_path, "auth")
    archived = tmp_path / ".forge" / "features" / "archive" / "2026-04-01-auth"
    archived.mkdir(parents=True, exist_ok=True)
    (archived / "SPEC.md").write_text(
        "---\nid: 2026-04-01-auth\nstatus: shipped\ntier: focused\n"
        "created: 2026-04-01\ncapability: auth\n---\n# Intent\narchived.\n",
        encoding="utf-8",
    )

    findings = validate.validate_capability_uniqueness(tmp_path)

    assert findings == []


def test_two_canonical_specs_same_capability_high(tmp_path: Path) -> None:
    """Should be impossible after /forge:ship, but surface it if it ever happens."""
    _make_canonical_spec(tmp_path, "auth")
    second = tmp_path / ".forge" / "specs" / "auth-duplicate"
    second.mkdir(parents=True, exist_ok=True)
    (second / "SPEC.md").write_text(
        "---\nid: 2026-01-02-auth-dup\nstatus: shipped\ntier: standard\n"
        "created: 2026-01-02\ncapability: auth\n---\n# Intent\nshipped.\n",
        encoding="utf-8",
    )

    findings = validate.validate_capability_uniqueness(tmp_path)

    assert any(f.severity == "HIGH" and "auth" in f.message for f in findings)


def test_missing_forge_root_returns_no_findings(tmp_path: Path) -> None:
    findings = validate.validate_capability_uniqueness(tmp_path)
    assert findings == []
