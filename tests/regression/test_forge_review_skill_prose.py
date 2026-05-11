"""Static contract tests pinning the forge-review skill prose.

The skill body documents an optional cross-AI manual-mode branch as two
sub-steps inserted between the self-review pass (Step 4) and the heavy
subagent pass (Step 5). These tests lock the contract so the new
sub-steps cannot silently regress and the existing in-house steps are
not displaced by future edits.
"""

from __future__ import annotations

from pathlib import Path

SKILL = Path("skills/forge-review/SKILL.md")


def _skill_body() -> str:
    return SKILL.read_text(encoding="utf-8")


def test_skill_documents_cross_ai_dispatch_substep() -> None:
    body = _skill_body()
    assert "4a. **Cross-AI dispatch (manual mode, optional).**" in body, (
        "Skill must document Step 4a as the cross-AI dispatch branch "
        "between Step 4 (self-review) and Step 5 (heavy subagent pass)"
    )


def test_skill_documents_cross_ai_paste_back_substep() -> None:
    body = _skill_body()
    assert "4b. **Cross-AI paste-back (optional).**" in body, (
        "Skill must document Step 4b as the cross-AI paste-back branch immediately after Step 4a"
    )


def test_skill_references_manual_mode_helpers_by_name() -> None:
    body = _skill_body()
    for helper in (
        "write_prompt_to_disk",
        "read_paste_response",
        "extract_reviewer_id",
        "merge_findings_into_review",
        "format_disclosure_summary",
    ):
        assert helper in body, (
            f"Skill must reference manual-mode helper {helper!r} by name "
            "so the planning agent calls the correct API"
        )


def test_skill_references_redaction_config_adapter() -> None:
    body = _skill_body()
    assert "to_redaction_config" in body, (
        "Skill must reference the to_redaction_config adapter so the planning "
        "agent does not construct RedactionConfig(...) by hand and risk "
        "passing the wrong dataclass to redaction.filter()"
    )


def test_skill_writes_redacted_output_text_not_raw_prompt_body() -> None:
    body = _skill_body()
    assert "write_prompt_to_disk(redaction_result.output_text" in body, (
        "Step 4a must persist the redacted output_text, not prompt.body — "
        "the on-disk file is what the operator pipes to the external CLI"
    )


def test_existing_heavy_subagent_step_not_displaced() -> None:
    body = _skill_body()
    assert "5. **Cycle N — Heavy subagent pass.**" in body, (
        "Step 5 heading must remain verbatim — proves the cross-AI "
        "sub-steps were inserted BETWEEN Step 4 and Step 5 rather than "
        "replacing or renumbering existing in-house steps"
    )


def test_skill_documents_auto_mode_branch_header() -> None:
    body = _skill_body()
    assert "**When auto mode is selected:**" in body, (
        "Step 4a must include the auto-mode sub-block header so the "
        "planning agent recognises the auto-dispatch branch"
    )


def test_skill_references_auto_dispatch_helpers_by_name() -> None:
    body = _skill_body()
    for helper in (
        "auto_dispatch",
        "record_dispatch_approval",
        "write_response_to_disk",
    ):
        assert helper in body, (
            f"Skill must reference auto-mode helper {helper!r} by name "
            "so the planning agent calls the correct dispatch API"
        )


def test_skill_documents_skip_cost_warn_flag() -> None:
    body = _skill_body()
    assert "--skip-cost-warn" in body, (
        "Skill must document the --skip-cost-warn flag so the planning "
        "agent knows when the cost-warn gate can be bypassed"
    )


def test_skill_documents_dispatch_error_fallback() -> None:
    body = _skill_body()
    assert "DispatchError" in body, (
        "Skill must reference DispatchError so the planning agent knows "
        "to fall back to manual mode when auto dispatch fails"
    )
