"""Tests for tools.validate.harden_shape — HARDEN.md structural validator."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import validate
from tools.validate import cli as validate_cli
from tools.validate.harden_shape import TARGET, validate_harden_shape

FEATURE_ID = "2026-05-09-demo"


def _seed_feature_dir(repo_root: Path) -> Path:
    feature_dir = repo_root / ".forge" / "features" / FEATURE_ID
    feature_dir.mkdir(parents=True, exist_ok=True)
    return feature_dir


def _write_state(
    feature_dir: Path,
    *,
    flow_version: int = 3,
    harden_status: str = "pending",
) -> None:
    payload: dict[str, object] = {
        "feature_id": FEATURE_ID,
        "tier": "standard",
        "current_phase": "harden" if harden_status != "done" else "done",
        "flow_version": flow_version,
        "phases": {"harden": {"status": harden_status}},
        "skipped": [],
        "deviations": [],
        "commits": [],
    }
    (feature_dir / "state.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def _harden_md(
    *,
    confidence: str = "high",
    flow_version: int = 3,
    contract_status: str = "pass",
    uat_status: str = "pass",
    adversarial_status: str = "pass",
    soak_status: str = "pass",
    nr_status: str = "pass",
    contract_evidence: str = "abc1234",
    uat_evidence: str = "VERIFICATION.md#sec",
    adversarial_evidence: str = "abc5678",
    soak_evidence: str = "deadbee",
    nr_evidence: str = "feedface",
    section_order: tuple[str, ...] = (
        "Contract",
        "UAT Replay",
        "Adversarial",
        "Soak",
        "NR Regrep",
    ),
    drop_section: str | None = None,
    extra_keys: dict[str, str] | None = None,
    omit_keys: tuple[str, ...] = (),
) -> str:
    base_keys: dict[str, str] = {
        "feature_id": FEATURE_ID,
        "shipped_at": "2026-05-09T10:00:00Z",
        "hardened_at": "2026-05-09T11:00:00Z",
        "confidence": confidence,
        "flow_version": str(flow_version),
    }
    if extra_keys:
        base_keys.update(extra_keys)
    for key in omit_keys:
        base_keys.pop(key, None)

    fm_lines = ["---"]
    for k, v in base_keys.items():
        fm_lines.append(f"{k}: {v}")
    fm_lines.append("---")
    frontmatter = "\n".join(fm_lines)

    section_bodies: dict[str, str] = {
        "Contract": (
            f"# Contract\n\n"
            f"- **Status:** {contract_status}\n"
            f"- **Evidence:** {contract_evidence}\n"
        ),
        "UAT Replay": (
            f"# UAT Replay\n\n"
            f"- **Status:** {uat_status}\n"
            f"- **Evidence:** {uat_evidence}\n"
        ),
        "Adversarial": (
            f"# Adversarial\n\n"
            f"- **Status:** {adversarial_status}\n"
            f"- **Evidence:** {adversarial_evidence}\n"
        ),
        "Soak": (
            f"# Soak\n\n"
            f"- **Status:** {soak_status}\n"
            f"- **Evidence:** {soak_evidence}\n"
        ),
        "NR Regrep": (
            f"# NR Regrep\n\n"
            f"- **Status:** {nr_status}\n"
            f"- **Evidence:** {nr_evidence}\n"
        ),
    }

    body_parts: list[str] = []
    for name in section_order:
        if drop_section is not None and name == drop_section:
            continue
        body_parts.append(section_bodies[name])

    return frontmatter + "\n\n# Hardening Record\n\n" + "\n".join(body_parts)


def test_harden_shape_clean_pass_no_findings(tmp_path: Path) -> None:
    feature_dir = _seed_feature_dir(tmp_path)
    _write_state(feature_dir, harden_status="done")
    (feature_dir / "HARDEN.md").write_text(_harden_md(), encoding="utf-8")

    findings = validate_harden_shape(tmp_path, FEATURE_ID)

    assert findings == []


def test_harden_shape_missing_section_blocks(tmp_path: Path) -> None:
    feature_dir = _seed_feature_dir(tmp_path)
    _write_state(feature_dir, harden_status="done")
    (feature_dir / "HARDEN.md").write_text(
        _harden_md(drop_section="Soak"), encoding="utf-8"
    )

    findings = validate_harden_shape(tmp_path, FEATURE_ID)

    assert any(
        f.severity == "BLOCK"
        and f.target == TARGET
        and "section_missing" in f.message
        and "Soak" in f.message
        for f in findings
    ), findings


def test_harden_shape_invalid_confidence_value_blocks(tmp_path: Path) -> None:
    feature_dir = _seed_feature_dir(tmp_path)
    _write_state(feature_dir, harden_status="done")
    (feature_dir / "HARDEN.md").write_text(
        _harden_md(confidence="medium"), encoding="utf-8"
    )

    findings = validate_harden_shape(tmp_path, FEATURE_ID)

    assert any(
        f.severity == "BLOCK"
        and "invalid_confidence_value" in f.message
        and "medium" in f.message
        for f in findings
    ), findings


def test_harden_shape_aggregation_mismatch_blocks(tmp_path: Path) -> None:
    feature_dir = _seed_feature_dir(tmp_path)
    _write_state(feature_dir, harden_status="done")
    # Statuses imply 'low' (one fail) but frontmatter declares 'high'.
    (feature_dir / "HARDEN.md").write_text(
        _harden_md(confidence="high", contract_status="fail"),
        encoding="utf-8",
    )

    findings = validate_harden_shape(tmp_path, FEATURE_ID)

    assert any(
        f.severity == "BLOCK"
        and "confidence_aggregation_mismatch" in f.message
        and "high" in f.message
        and "low" in f.message
        for f in findings
    ), findings


def test_harden_shape_invalid_section_status_blocks(tmp_path: Path) -> None:
    feature_dir = _seed_feature_dir(tmp_path)
    _write_state(feature_dir, harden_status="done")
    (feature_dir / "HARDEN.md").write_text(
        _harden_md(contract_status="pending"), encoding="utf-8"
    )

    findings = validate_harden_shape(tmp_path, FEATURE_ID)

    assert any(
        f.severity == "BLOCK"
        and "invalid_section_status" in f.message
        and "pending" in f.message
        for f in findings
    ), findings


def test_harden_shape_evidence_path_missing_low(tmp_path: Path) -> None:
    feature_dir = _seed_feature_dir(tmp_path)
    _write_state(feature_dir, harden_status="done")
    # Soak set to skipped so high aggregation does not break (confidence stays high).
    (feature_dir / "HARDEN.md").write_text(
        _harden_md(
            uat_evidence="./does/not/exist.log",
        ),
        encoding="utf-8",
    )

    findings = validate_harden_shape(tmp_path, FEATURE_ID)

    assert any(
        f.severity == "LOW"
        and "evidence_path_missing" in f.message
        and "does/not/exist.log" in f.message
        for f in findings
    ), findings
    assert all(f.severity != "BLOCK" for f in findings), findings


def test_harden_shape_harden_md_absent_pre_harden_phase_returns_empty(
    tmp_path: Path,
) -> None:
    feature_dir = _seed_feature_dir(tmp_path)
    _write_state(feature_dir, harden_status="pending")

    findings = validate_harden_shape(tmp_path, FEATURE_ID)

    assert findings == []


def test_harden_shape_harden_md_absent_post_harden_done_blocks(
    tmp_path: Path,
) -> None:
    feature_dir = _seed_feature_dir(tmp_path)
    _write_state(feature_dir, harden_status="done")

    findings = validate_harden_shape(tmp_path, FEATURE_ID)

    assert any(
        f.severity == "BLOCK"
        and "harden_md_missing" in f.message
        for f in findings
    ), findings


def test_harden_shape_section_out_of_order_medium(tmp_path: Path) -> None:
    feature_dir = _seed_feature_dir(tmp_path)
    _write_state(feature_dir, harden_status="done")
    (feature_dir / "HARDEN.md").write_text(
        _harden_md(
            section_order=(
                "Contract",
                "Adversarial",
                "UAT Replay",
                "Soak",
                "NR Regrep",
            ),
        ),
        encoding="utf-8",
    )

    findings = validate_harden_shape(tmp_path, FEATURE_ID)

    assert any(
        f.severity == "MEDIUM"
        and "section_out_of_order" in f.message
        for f in findings
    ), findings


def test_harden_shape_frontmatter_missing_key_blocks(tmp_path: Path) -> None:
    feature_dir = _seed_feature_dir(tmp_path)
    _write_state(feature_dir, harden_status="done")
    (feature_dir / "HARDEN.md").write_text(
        _harden_md(omit_keys=("hardened_at",)), encoding="utf-8"
    )

    findings = validate_harden_shape(tmp_path, FEATURE_ID)

    assert any(
        f.severity == "BLOCK"
        and "frontmatter_missing_key" in f.message
        and "hardened_at" in f.message
        for f in findings
    ), findings


def test_harden_shape_wrong_flow_version_blocks(tmp_path: Path) -> None:
    feature_dir = _seed_feature_dir(tmp_path)
    _write_state(feature_dir, harden_status="done")
    (feature_dir / "HARDEN.md").write_text(
        _harden_md(flow_version=2), encoding="utf-8"
    )

    findings = validate_harden_shape(tmp_path, FEATURE_ID)

    assert any(
        f.severity == "BLOCK" and "wrong_flow_version" in f.message
        for f in findings
    ), findings


def test_harden_shape_partial_aggregation_passes(tmp_path: Path) -> None:
    """Exactly one section partial → declared `partial` is consistent."""
    feature_dir = _seed_feature_dir(tmp_path)
    _write_state(feature_dir, harden_status="done")
    (feature_dir / "HARDEN.md").write_text(
        _harden_md(confidence="partial", uat_status="partial"),
        encoding="utf-8",
    )

    findings = validate_harden_shape(tmp_path, FEATURE_ID)

    assert all(f.severity != "BLOCK" for f in findings), findings


def test_harden_shape_soak_skipped_still_high(tmp_path: Path) -> None:
    """Soak `skipped` is permitted for `high` aggregation (libraries)."""
    feature_dir = _seed_feature_dir(tmp_path)
    _write_state(feature_dir, harden_status="done")
    (feature_dir / "HARDEN.md").write_text(
        _harden_md(confidence="high", soak_status="skipped"),
        encoding="utf-8",
    )

    findings = validate_harden_shape(tmp_path, FEATURE_ID)

    assert all(f.severity != "BLOCK" for f in findings), findings


def test_harden_shape_evidence_resolved_path_no_finding(tmp_path: Path) -> None:
    feature_dir = _seed_feature_dir(tmp_path)
    _write_state(feature_dir, harden_status="done")
    transcript = feature_dir / "soak.log"
    transcript.write_text("ok\n", encoding="utf-8")
    rel_evidence = f"./.forge/features/{FEATURE_ID}/soak.log"
    (feature_dir / "HARDEN.md").write_text(
        _harden_md(soak_evidence=rel_evidence),
        encoding="utf-8",
    )

    findings = validate_harden_shape(tmp_path, FEATURE_ID)

    assert all("evidence_path_missing" not in f.message for f in findings), findings


def test_harden_shape_cli_target_registered(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    feature_dir = _seed_feature_dir(tmp_path)
    _write_state(feature_dir, harden_status="done")
    (feature_dir / "HARDEN.md").write_text(_harden_md(), encoding="utf-8")

    rc = validate.main(
        [
            "--target",
            "harden_shape",
            "--repo-root",
            str(tmp_path),
            str(feature_dir),
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == 0, payload
    assert payload["target"] == "harden_shape"
    assert payload["findings"] == []


def test_harden_shape_cli_target_all_includes_harden_shape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`--target all` must dispatch to validate_harden_shape too."""
    sentinel_calls: list[str] = []

    def make_sentinel(name: str) -> object:
        def fn(*args: object, **kwargs: object) -> list[object]:
            sentinel_calls.append(name)
            return []

        return fn

    monkeypatch.setattr(
        validate_cli, "validate_harden_shape", make_sentinel("harden_shape")
    )

    feat = tmp_path / ".forge" / "features" / FEATURE_ID
    feat.mkdir(parents=True)
    (feat / "state.json").write_text('{"deviations": []}', encoding="utf-8")

    rc = validate.main(["--target", "all", "--repo-root", str(tmp_path)])
    capsys.readouterr()
    assert rc in (0, 1)
    assert "harden_shape" in sentinel_calls
