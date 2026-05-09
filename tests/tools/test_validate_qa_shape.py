"""Tests for ``tools.validate.qa_shape``.

The validator asserts that ``QA.md`` for a feature has the four required
sections in order, valid frontmatter, a verdict that matches the
``# Acceptance`` Status, a confidence value that aggregates correctly
across all four sections, and resolvable evidence pointers when they look
like file paths.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import validate
from tools.validate import cli as validate_cli
from tools.validate.qa_shape import validate_qa_shape

FEATURE_ID = "2026-05-09-qa-fixture"


def _make_feature_dir(tmp_path: Path) -> Path:
    feature_dir = tmp_path / ".forge" / "features" / FEATURE_ID
    feature_dir.mkdir(parents=True)
    return feature_dir


def _write_state(
    feature_dir: Path,
    *,
    flow_version: int = 3,
    qa_status: str = "in_progress",
) -> None:
    payload: dict[str, object] = {
        "feature_id": FEATURE_ID,
        "tier": "focused",
        "current_phase": "qa",
        "flow_version": flow_version,
        "phases": {"qa": {"status": qa_status}},
        "skipped": [],
        "deviations": [],
        "commits": [],
    }
    (feature_dir / "state.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _qa_md(
    *,
    feature_id: str = FEATURE_ID,
    verdict: str = "delivers",
    confidence: str = "high",
    flow_version: int = 3,
    acceptance_status: str = "delivers",
    edge_status: str = "pass",
    adversarial_status: str = "pass",
    nr_status: str = "pass",
    section_order: tuple[str, str, str, str] = (
        "Acceptance",
        "Edge Probing",
        "Adversarial",
        "NR Regrep",
    ),
    drop_section: str | None = None,
    acceptance_evidence: str = "transcript-001",
    edge_evidence: str = "transcript-002",
    adversarial_evidence: str = "transcript-003",
    nr_evidence: str = "abc1234",
    extra_frontmatter_drop: str | None = None,
) -> str:
    fm_keys = {
        "feature_id": feature_id,
        "shipped_at": "2026-05-08T12:00:00Z",
        "qa_at": "2026-05-09T12:00:00Z",
        "verdict": verdict,
        "confidence": confidence,
        "flow_version": flow_version,
    }
    if extra_frontmatter_drop is not None and extra_frontmatter_drop in fm_keys:
        fm_keys.pop(extra_frontmatter_drop)
    fm_lines = [f"{k}: {v}" for k, v in fm_keys.items()]

    sections: dict[str, str] = {
        "Acceptance": (
            "# Acceptance\n\n"
            f"- **Status:** {acceptance_status}\n"
            "- **Spec promises checked:** 3\n"
            "- **Promises met:** 3\n"
            "- **Findings:**\n"
            "  - none\n"
            f"- **Evidence:** {acceptance_evidence}\n"
        ),
        "Edge Probing": (
            "# Edge Probing\n\n"
            f"- **Status:** {edge_status}\n"
            "- **Edges probed:** 4\n"
            "- **Failures observed:** 0\n"
            "- **Findings:**\n"
            "  - none\n"
            f"- **Evidence:** {edge_evidence}\n"
        ),
        "Adversarial": (
            "# Adversarial\n\n"
            f"- **Status:** {adversarial_status}\n"
            "- **Walltime budget:** 5\n"
            "- **Attempts:** 12\n"
            "- **Breakages found:** 0\n"
            "- **Findings:**\n"
            "  - none\n"
            f"- **Evidence:** {adversarial_evidence}\n"
        ),
        "NR Regrep": (
            "# NR Regrep\n\n"
            f"- **Status:** {nr_status}\n"
            "- **Negative Requirements scanned:** 7\n"
            "- **Violations re-introduced:** 0\n"
            "- **Findings:**\n"
            "  - none\n"
            f"- **Evidence:** {nr_evidence}\n"
        ),
    }
    body_parts: list[str] = []
    for name in section_order:
        if drop_section is not None and name == drop_section:
            continue
        body_parts.append(sections[name])
    body = "\n".join(body_parts)

    return "---\n" + "\n".join(fm_lines) + "\n---\n\n# QA Acceptance Record\n\n" + body


def _write_qa(feature_dir: Path, contents: str) -> Path:
    qa_path = feature_dir / "QA.md"
    qa_path.write_text(contents, encoding="utf-8")
    return qa_path


def test_qa_shape_clean_pass_no_findings(tmp_path: Path) -> None:
    feature_dir = _make_feature_dir(tmp_path)
    _write_state(feature_dir)
    _write_qa(feature_dir, _qa_md())
    findings = validate_qa_shape(tmp_path, FEATURE_ID)
    assert findings == [], findings


def test_qa_shape_missing_section_blocks(tmp_path: Path) -> None:
    feature_dir = _make_feature_dir(tmp_path)
    _write_state(feature_dir)
    _write_qa(feature_dir, _qa_md(drop_section="NR Regrep"))
    findings = validate_qa_shape(tmp_path, FEATURE_ID)
    assert any(
        f.severity == "BLOCK" and "qa_shape:section_missing" in f.message for f in findings
    ), findings


def test_qa_shape_invalid_verdict_blocks(tmp_path: Path) -> None:
    feature_dir = _make_feature_dir(tmp_path)
    _write_state(feature_dir)
    # acceptance_status must remain a real value so we hit the verdict-value
    # check (not the section-status check) first.
    _write_qa(feature_dir, _qa_md(verdict="maybe"))
    findings = validate_qa_shape(tmp_path, FEATURE_ID)
    assert any(
        f.severity == "BLOCK" and "qa_shape:invalid_verdict_value" in f.message for f in findings
    ), findings


def test_qa_shape_invalid_confidence_blocks(tmp_path: Path) -> None:
    feature_dir = _make_feature_dir(tmp_path)
    _write_state(feature_dir)
    _write_qa(feature_dir, _qa_md(confidence="medium"))
    findings = validate_qa_shape(tmp_path, FEATURE_ID)
    assert any(
        f.severity == "BLOCK" and "qa_shape:invalid_confidence_value" in f.message for f in findings
    ), findings


def test_qa_shape_verdict_mismatch_blocks(tmp_path: Path) -> None:
    feature_dir = _make_feature_dir(tmp_path)
    _write_state(feature_dir)
    _write_qa(
        feature_dir,
        _qa_md(verdict="delivers", acceptance_status="partial", confidence="partial"),
    )
    findings = validate_qa_shape(tmp_path, FEATURE_ID)
    assert any(
        f.severity == "BLOCK" and "qa_shape:verdict_mismatch" in f.message for f in findings
    ), findings


def test_qa_shape_confidence_aggregation_mismatch_blocks(tmp_path: Path) -> None:
    feature_dir = _make_feature_dir(tmp_path)
    _write_state(feature_dir)
    # Acceptance partial + Adversarial fail → computed confidence is `low`,
    # but frontmatter declares `high` → mismatch.
    _write_qa(
        feature_dir,
        _qa_md(
            verdict="partial",
            acceptance_status="partial",
            adversarial_status="fail",
            confidence="high",
        ),
    )
    findings = validate_qa_shape(tmp_path, FEATURE_ID)
    assert any(
        f.severity == "BLOCK" and "qa_shape:confidence_aggregation_mismatch" in f.message
        for f in findings
    ), findings


def test_qa_shape_invalid_section_status_blocks(tmp_path: Path) -> None:
    feature_dir = _make_feature_dir(tmp_path)
    _write_state(feature_dir)
    _write_qa(feature_dir, _qa_md(adversarial_status="pending"))
    findings = validate_qa_shape(tmp_path, FEATURE_ID)
    assert any(
        f.severity == "BLOCK" and "qa_shape:invalid_section_status" in f.message for f in findings
    ), findings


def test_qa_shape_evidence_path_missing_low(tmp_path: Path) -> None:
    feature_dir = _make_feature_dir(tmp_path)
    _write_state(feature_dir)
    _write_qa(
        feature_dir,
        _qa_md(adversarial_evidence="./does/not/exist.log"),
    )
    findings = validate_qa_shape(tmp_path, FEATURE_ID)
    assert any(
        f.severity == "LOW" and "qa_shape:evidence_path_missing" in f.message for f in findings
    ), findings


def test_qa_shape_qa_md_absent_pre_qa_phase_returns_empty(tmp_path: Path) -> None:
    feature_dir = _make_feature_dir(tmp_path)
    _write_state(feature_dir, qa_status="pending")
    findings = validate_qa_shape(tmp_path, FEATURE_ID)
    assert findings == []


def test_qa_shape_qa_md_absent_post_qa_done_blocks(tmp_path: Path) -> None:
    feature_dir = _make_feature_dir(tmp_path)
    _write_state(feature_dir, qa_status="done")
    findings = validate_qa_shape(tmp_path, FEATURE_ID)
    assert any(f.severity == "BLOCK" and "qa_shape:qa_md_missing" in f.message for f in findings), (
        findings
    )


def test_qa_shape_section_out_of_order_medium(tmp_path: Path) -> None:
    feature_dir = _make_feature_dir(tmp_path)
    _write_state(feature_dir)
    _write_qa(
        feature_dir,
        _qa_md(
            section_order=("Acceptance", "Adversarial", "Edge Probing", "NR Regrep"),
        ),
    )
    findings = validate_qa_shape(tmp_path, FEATURE_ID)
    assert any(
        f.severity == "MEDIUM" and "qa_shape:section_out_of_order" in f.message for f in findings
    ), findings


def test_qa_shape_frontmatter_missing_key_blocks(tmp_path: Path) -> None:
    feature_dir = _make_feature_dir(tmp_path)
    _write_state(feature_dir)
    _write_qa(feature_dir, _qa_md(extra_frontmatter_drop="qa_at"))
    findings = validate_qa_shape(tmp_path, FEATURE_ID)
    assert any(
        f.severity == "BLOCK" and "qa_shape:frontmatter_missing_key" in f.message for f in findings
    ), findings


def test_qa_shape_wrong_flow_version_blocks(tmp_path: Path) -> None:
    feature_dir = _make_feature_dir(tmp_path)
    _write_state(feature_dir)
    _write_qa(feature_dir, _qa_md(flow_version=2))
    findings = validate_qa_shape(tmp_path, FEATURE_ID)
    assert any(
        f.severity == "BLOCK" and "qa_shape:wrong_flow_version" in f.message for f in findings
    ), findings


def test_qa_shape_evidence_resolves_when_path(tmp_path: Path) -> None:
    """When the evidence pointer is a real path under repo_root, no LOW
    finding is emitted."""
    feature_dir = _make_feature_dir(tmp_path)
    _write_state(feature_dir)
    evidence_file = tmp_path / "transcripts" / "qa.log"
    evidence_file.parent.mkdir(parents=True)
    evidence_file.write_text("ok", encoding="utf-8")
    _write_qa(
        feature_dir,
        _qa_md(adversarial_evidence="./transcripts/qa.log"),
    )
    findings = validate_qa_shape(tmp_path, FEATURE_ID)
    assert all("qa_shape:evidence_path_missing" not in f.message for f in findings), findings


def test_qa_shape_cli_target_registered(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    feature_dir = _make_feature_dir(tmp_path)
    _write_state(feature_dir)
    _write_qa(feature_dir, _qa_md())
    rc = validate.main(["--target", "qa_shape", "--repo-root", str(tmp_path), str(feature_dir)])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == 0, captured
    assert payload["target"] == "qa_shape"
    assert payload["findings"] == []


def test_qa_shape_cli_target_all_includes_qa_shape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--target all`` fans out qa_shape across feature folders."""
    sentinel: list[str] = []

    def fake_qa_shape(repo_root: Path, feature_id: str) -> list[validate.Finding]:
        sentinel.append(feature_id)
        return []

    monkeypatch.setattr(validate_cli, "validate_qa_shape", fake_qa_shape)

    feature_dir = _make_feature_dir(tmp_path)
    _write_state(feature_dir)
    _write_qa(feature_dir, _qa_md())

    rc = validate.main(["--target", "all", "--repo-root", str(tmp_path)])
    capsys.readouterr()
    assert rc in (0, 1)
    assert FEATURE_ID in sentinel
