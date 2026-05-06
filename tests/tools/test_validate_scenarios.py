"""Tests for tools.validate.validate_scenarios (M3 §5.3.6 D-8 scenarios↔acceptance)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools import validate

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "_validate"


def test_scenarios_pass_inline_gherkin_returns_no_findings() -> None:
    findings = validate.validate_scenarios(FIX / "spec_scenarios_pass.md")
    assert findings == []


def test_scenarios_template_shaped_gherkin_fence_returns_no_findings() -> None:
    findings = validate.validate_scenarios(FIX / "spec_scenarios_template_shaped.md")
    assert findings == []


def test_scenarios_measurable_ac_is_exempt_from_mapping() -> None:
    findings = validate.validate_scenarios(FIX / "spec_scenarios_measurable_ac.md")
    assert findings == []


def test_scenarios_missing_section_blocks() -> None:
    findings = validate.validate_scenarios(FIX / "spec_scenarios_no_scenarios_section.md")
    assert any(f.severity == "BLOCK" for f in findings)
    assert any("scenarios" in f.message.lower() for f in findings)


def test_scenarios_orphan_scenario_high() -> None:
    findings = validate.validate_scenarios(FIX / "spec_scenarios_orphan_scenario.md")
    assert any(f.severity == "HIGH" and "orphan" in f.message.lower() for f in findings)


def test_scenarios_unmapped_ac_high() -> None:
    findings = validate.validate_scenarios(FIX / "spec_scenarios_unmapped_ac.md")
    assert any(f.severity == "HIGH" and "no scenario" in f.message.lower() for f in findings)


def test_scenarios_weasel_word_in_title_medium() -> None:
    findings = validate.validate_scenarios(FIX / "spec_scenarios_weasel_word_in_title.md")
    assert any(f.severity == "MEDIUM" and "should" in f.message for f in findings)


def test_scenarios_weasel_word_in_body_medium() -> None:
    findings = validate.validate_scenarios(FIX / "spec_scenarios_weasel_word_in_body.md")
    assert any(f.severity == "MEDIUM" and "might" in f.message for f in findings)
    assert any(f.severity == "MEDIUM" and "TBD" in f.message for f in findings)


def test_scenarios_bare_digit_does_not_match_ac_index() -> None:
    findings = validate.validate_scenarios(FIX / "spec_scenarios_bare_digit_no_match.md")
    # Bare digit "OAuth 2 login" must NOT count as a reference to AC 2 — only
    # explicit tokens (`crit-N`, `criterion-N`, `criterion: N`, `Scenario N`)
    # map. AC 2 must therefore be flagged unmapped HIGH.
    assert any(
        f.severity == "HIGH" and "AC 2" in f.message and "no scenario" in f.message.lower()
        for f in findings
    )


def test_scenarios_open_questions_numbered_list_is_not_parsed_as_acs() -> None:
    findings = validate.validate_scenarios(FIX / "spec_scenarios_open_questions_numbered.md")
    # Open Questions has 3 numbered items; if they were treated as ACs we'd
    # see "AC 2"/"AC 3" unmapped findings. Slice-bounded AC parsing prevents
    # that — the SPEC is fully covered.
    assert findings == []


def test_scenarios_missing_file_blocks(tmp_path: Path) -> None:
    findings = validate.validate_scenarios(tmp_path / "does_not_exist.md")
    assert any(f.severity == "BLOCK" for f in findings)


@pytest.mark.parametrize(
    "fixture_name",
    [
        "spec_scenarios_weasel_word_in_title.md",
        "spec_scenarios_weasel_word_in_body.md",
    ],
)
def test_scenarios_weasel_word_scan_covers_title_and_body(fixture_name: str) -> None:
    findings = validate.validate_scenarios(FIX / fixture_name)
    assert any(f.severity == "MEDIUM" for f in findings)
