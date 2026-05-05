"""Tests for validate_delta structural checks."""

from __future__ import annotations

from pathlib import Path

from tools import validate

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "_validate"


def test_pass_delta_returns_no_findings() -> None:
    findings = validate.validate_delta(FIXTURES / "delta_pass.md")
    assert findings == []


def test_no_frontmatter_blocks() -> None:
    findings = validate.validate_delta(FIXTURES / "delta_no_frontmatter.md")
    assert any(f.severity == "BLOCK" and "frontmatter" in f.message.lower() for f in findings)


def test_bad_status_enum_blocks() -> None:
    findings = validate.validate_delta(FIXTURES / "delta_bad_status_enum.md")
    assert any(f.severity == "BLOCK" and "status" in f.message.lower() for f in findings)


def test_missing_affects_blocks() -> None:
    findings = validate.validate_delta(FIXTURES / "delta_missing_affects.md")
    assert any(f.severity == "BLOCK" and "affects" in f.message.lower() for f in findings)


def test_missing_delta_section_blocks() -> None:
    findings = validate.validate_delta(FIXTURES / "delta_missing_delta_section.md")
    assert any(f.severity == "BLOCK" and "delta" in f.message.lower() for f in findings)


def test_no_op_markers_blocks() -> None:
    findings = validate.validate_delta(FIXTURES / "delta_no_op_markers.md")
    assert any(
        f.severity == "BLOCK"
        and ("ADD" in f.message or "REMOVE" in f.message or "MODIFY" in f.message)
        for f in findings
    )


def test_missing_file_returns_block_finding(tmp_path: Path) -> None:
    findings = validate.validate_delta(tmp_path / "absent.md")
    assert any(f.severity == "BLOCK" and "not found" in f.message.lower() for f in findings)


def test_path_traversal_in_affects_capability_blocks(tmp_path: Path) -> None:
    """`affects_capability: ../escape` would later be joined into a Path by
    health-scan's canonical lookup. Reject it at the schema layer so a
    bad delta cannot reach the path-join boundary."""
    proposal = tmp_path / "proposal.md"
    proposal.write_text(
        "---\n"
        "id: 2026-05-04-traversal\n"
        "affects_capability: ../escape\n"
        "status: draft\n"
        "created: 2026-05-04\n"
        "---\n\n"
        "## Affects\n- spec: x\n\n## Delta\n+ ADD: x\n",
        encoding="utf-8",
    )

    findings = validate.validate_delta(proposal)

    assert any(f.severity == "BLOCK" and "affects_capability" in f.message for f in findings), (
        findings
    )


def test_op_markers_only_in_rationale_blocks() -> None:
    """Operator markers (`+ ADD:` etc.) appearing in `## Rationale` must not
    satisfy the `## Delta` op-marker check. P5 will rely on this validator
    before merging deltas."""
    findings = validate.validate_delta(FIXTURES / "delta_op_markers_in_rationale_only.md")
    assert any(
        f.severity == "BLOCK" and "no operator markers" in f.message.lower() for f in findings
    ), findings


def test_loose_section_headings_block() -> None:
    """`## Affects on consumers` and `## Delta-2 foo` must NOT pass section
    presence — only exact `## Affects` / `## Delta` headings count."""
    findings = validate.validate_delta(FIXTURES / "delta_loose_heading.md")
    block_msgs = [f.message.lower() for f in findings if f.severity == "BLOCK"]
    assert any("affects" in m for m in block_msgs), findings
    assert any("delta" in m for m in block_msgs), findings


def test_invalid_yaml_frontmatter_returns_block_not_traceback() -> None:
    """Malformed YAML must surface as a structured BLOCK finding."""
    findings = validate.validate_delta(FIXTURES / "delta_invalid_yaml.md")
    assert any(f.severity == "BLOCK" and "invalid yaml" in f.message.lower() for f in findings), (
        findings
    )
