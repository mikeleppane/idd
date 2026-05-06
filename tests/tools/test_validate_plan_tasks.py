"""Tests for tools.validate.validate_plan_tasks (M3 §5.3.6 D-8 plan task↔acceptance)."""

from __future__ import annotations

from pathlib import Path

from tools import validate

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "_validate"


def _pair(case: str) -> tuple[Path, Path]:
    base = FIX / case
    return base / "PLAN.md", base / "SPEC.md"


def test_plan_tasks_pass() -> None:
    plan, spec = _pair("plan_tasks_pass")
    findings = validate.validate_plan_tasks(plan, spec_path=spec)
    assert findings == []


def test_plan_tasks_unmapped_ac_high() -> None:
    plan, spec = _pair("plan_tasks_unmapped_ac")
    findings = validate.validate_plan_tasks(plan, spec_path=spec)
    assert any(
        f.severity == "HIGH" and "AC 3" in f.message and "zero" in f.message.lower()
        for f in findings
    )


def test_plan_tasks_double_mapped_high() -> None:
    plan, spec = _pair("plan_tasks_double_mapped")
    findings = validate.validate_plan_tasks(plan, spec_path=spec)
    assert any(
        f.severity == "HIGH" and "AC 1" in f.message and "multiple" in f.message.lower()
        for f in findings
    )


def test_plan_tasks_file_collision_high() -> None:
    plan, spec = _pair("plan_tasks_file_collision")
    findings = validate.validate_plan_tasks(plan, spec_path=spec)
    assert any(
        f.severity == "HIGH" and "pkg/a.py" in f.message and "multiple slices" in f.message.lower()
        for f in findings
    )


def test_plan_tasks_no_acceptance_high() -> None:
    plan, spec = _pair("plan_tasks_no_acceptance")
    findings = validate.validate_plan_tasks(plan, spec_path=spec)
    assert any(
        f.severity == "HIGH" and "Acceptance" in f.message and "missing" in f.message.lower()
        for f in findings
    )


def test_plan_tasks_backticked_files_collision_high() -> None:
    plan, spec = _pair("plan_tasks_backticked_files")
    findings = validate.validate_plan_tasks(plan, spec_path=spec)
    assert any(
        f.severity == "HIGH" and "a.py" in f.message and "multiple slices" in f.message.lower()
        for f in findings
    )


def test_plan_tasks_open_questions_numbered_list_is_not_parsed_as_acs() -> None:
    plan, spec = _pair("plan_tasks_open_questions_numbered")
    findings = validate.validate_plan_tasks(plan, spec_path=spec)
    assert findings == []


def test_plan_tasks_shared_flag_exempts_from_collision() -> None:
    plan, spec = _pair("plan_tasks_shared_file")
    findings = validate.validate_plan_tasks(plan, spec_path=spec)
    assert findings == []


def test_plan_tasks_missing_plan_file_blocks(tmp_path: Path) -> None:
    spec = FIX / "plan_tasks_pass" / "SPEC.md"
    findings = validate.validate_plan_tasks(tmp_path / "missing_PLAN.md", spec_path=spec)
    assert any(f.severity == "BLOCK" for f in findings)


def test_plan_tasks_missing_spec_file_blocks(tmp_path: Path) -> None:
    plan = FIX / "plan_tasks_pass" / "PLAN.md"
    findings = validate.validate_plan_tasks(plan, spec_path=tmp_path / "missing_SPEC.md")
    assert any(f.severity == "BLOCK" for f in findings)
