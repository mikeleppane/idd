"""Tests for tools.validate.validate_deviations (M3 §5.3.6 D-8 deviation cross-ref)."""

from __future__ import annotations

from pathlib import Path

from tools import validate

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "_validate"


def test_deviations_pass() -> None:
    findings = validate.validate_deviations(FIX / "deviations_pass")
    assert findings == []


def test_deviations_unrecorded_high() -> None:
    findings = validate.validate_deviations(FIX / "deviations_unrecorded")
    assert any(
        f.severity == "HIGH" and f.target == "deviations" and "not recorded" in f.message.lower()
        for f in findings
    )


def test_deviations_orphan_decision_info() -> None:
    findings = validate.validate_deviations(FIX / "deviations_orphan_decision")
    # The deviation is recorded, so no HIGH; but the decisions.md mentions
    # phase=research that the state never declared → INFO drift.
    assert all(f.severity != "HIGH" for f in findings)
    assert any(
        f.severity == "INFO" and f.target == "deviations" and "research" in f.message.lower()
        for f in findings
    )


def test_deviations_unparseable_state_block() -> None:
    findings = validate.validate_deviations(FIX / "deviations_unparseable_state")
    assert len(findings) == 1
    assert findings[0].severity == "BLOCK"
    assert findings[0].target == "deviations"
    assert "state.json" in findings[0].message.lower()


def test_deviations_endash_separator_passes() -> None:
    findings = validate.validate_deviations(FIX / "deviations_endash_decision")
    assert all(f.severity != "HIGH" for f in findings)


def test_deviations_hyphen_separator_passes() -> None:
    findings = validate.validate_deviations(FIX / "deviations_hyphen_decision")
    assert all(f.severity != "HIGH" for f in findings)


def test_deviations_missing_decisions_md_block() -> None:
    findings = validate.validate_deviations(FIX / "deviations_missing_decisions_md")
    assert len(findings) == 1
    assert findings[0].severity == "BLOCK"
    assert "missing or empty" in findings[0].message.lower()


def test_deviations_missing_state_file_block(tmp_path: Path) -> None:
    # Empty dir → no state.json → BLOCK.
    findings = validate.validate_deviations(tmp_path)
    assert len(findings) == 1
    assert findings[0].severity == "BLOCK"


def test_deviations_empty_list_passes(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    state.write_text(
        '{"feature_id":"2026-05-05-x","tier":"standard","current_phase":"execute",'
        '"phases":{},"skipped":[],"deviations":[],"commits":[]}',
        encoding="utf-8",
    )
    findings = validate.validate_deviations(tmp_path)
    assert findings == []


def test_deviations_empty_cause_high(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    state.write_text(
        '{"feature_id":"2026-05-05-x","tier":"standard","current_phase":"execute",'
        '"phases":{},"skipped":[],"deviations":[{"phase":"execute","cause":"   ",'
        '"resolution":"none"}],"commits":[]}',
        encoding="utf-8",
    )
    decisions = tmp_path / "decisions.md"
    decisions.write_text(
        "# Decisions Log\n\n## 2026-05-05 — placeholder\n\n**Context:** none.\n",
        encoding="utf-8",
    )
    findings = validate.validate_deviations(tmp_path)
    assert any(f.severity == "HIGH" and "empty cause" in f.message.lower() for f in findings)


def test_deviations_non_dict_state_block(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    state.write_text("[]", encoding="utf-8")
    findings = validate.validate_deviations(tmp_path)
    assert len(findings) == 1
    assert findings[0].severity == "BLOCK"


def test_deviations_non_list_deviations_block(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    state.write_text('{"deviations": "not-a-list"}', encoding="utf-8")
    findings = validate.validate_deviations(tmp_path)
    assert len(findings) == 1
    assert findings[0].severity == "BLOCK"


def test_deviations_non_dict_entry_blocks(tmp_path: Path) -> None:
    """A deviations[] list with a non-object entry is malformed; pre-fix the
    helper silently dropped such entries and the validator returned []. Now
    /forge:execute delegates to this validator, so a passing run on malformed
    state.json would let the phase exit unreviewed.
    """
    state = tmp_path / "state.json"
    state.write_text('{"deviations": ["bad"]}', encoding="utf-8")
    findings = validate.validate_deviations(tmp_path)
    assert len(findings) == 1
    assert findings[0].severity == "BLOCK"
    assert "deviations[0]" in findings[0].message


def test_deviations_mixed_valid_and_non_dict_blocks(tmp_path: Path) -> None:
    """Even one non-dict entry in an otherwise valid list aborts cross-ref."""
    state = tmp_path / "state.json"
    state.write_text(
        '{"feature_id":"x","deviations":[{"phase":"execute","cause":"a","resolution":"b"},42]}',
        encoding="utf-8",
    )
    findings = validate.validate_deviations(tmp_path)
    assert len(findings) == 1
    assert findings[0].severity == "BLOCK"
    assert "deviations[1]" in findings[0].message
