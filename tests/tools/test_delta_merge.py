"""Tests for tools/delta_merge.py — parser, applier, and _normalize_anchor.

Coverage target: >= 95% of tools/delta_merge.py.

Test numbering follows the spec in docs/plans/2026-05-08-m3-p5-change-deltas.md,
T4 "Required test cases" section.
"""

from __future__ import annotations

from typing import Literal

import pytest

from tools.delta_merge import (
    DeltaMergeError,
    DeltaOp,
    _normalize_anchor,
    apply_delta_ops,
    parse_proposal_body,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FRONTMATTER = """\
---
id: 2026-05-08-test
affects_capability: my-cap
status: approved
created: 2026-05-08
---

"""

_AFFECTS_SINGLE = """\
## Affects

sections [Scenarios]
"""

_AFFECTS_MULTI = """\
## Affects

sections [Scenarios, Acceptance Criteria]
"""

_DELTA_HEADER = "## Delta\n\n"


def _body(affects: str, delta_ops: str) -> str:
    return _FRONTMATTER + affects + "\n" + _DELTA_HEADER + delta_ops


def _single_section_body(delta_ops: str) -> str:
    return _body(_AFFECTS_SINGLE, delta_ops)


def _multi_section_body(delta_ops: str) -> str:
    return _body(_AFFECTS_MULTI, delta_ops)


# ---------------------------------------------------------------------------
# _normalize_anchor tests (27-30)
# ---------------------------------------------------------------------------


def test_normalize_anchor_lowercases() -> None:
    """Test 27: _normalize_anchor lowercases input."""
    assert _normalize_anchor("Criterion 2") == "criterion 2"


def test_normalize_anchor_strips_whitespace() -> None:
    """Test 28: _normalize_anchor strips leading/trailing whitespace."""
    assert _normalize_anchor("  criterion 2  ") == "criterion 2"


def test_normalize_anchor_collapses_internal_whitespace() -> None:
    """Test 29: _normalize_anchor collapses internal whitespace."""
    assert _normalize_anchor("criterion   2") == "criterion 2"


def test_normalize_anchor_idempotent() -> None:
    """Test 30: _normalize_anchor is idempotent on already-normalized input."""
    normalized = "criterion 2"
    assert _normalize_anchor(normalized) == normalized
    assert _normalize_anchor(_normalize_anchor(normalized)) == normalized


# ---------------------------------------------------------------------------
# Parser tests — parse_proposal_body
# ---------------------------------------------------------------------------


def test_single_section_happy_path_one_add_one_remove_one_modify() -> None:
    """Test 1: Single-section, 1 ADD + 1 REMOVE + 1 MODIFY (untagged, single line each)."""
    text = _single_section_body(
        "+ ADD: scenario new-login\n"
        "- REMOVE: scenario old-login\n"
        '~ MODIFY: criterion 1 — was "old text", now "new text"\n'
    )
    ops = parse_proposal_body(text)
    assert len(ops) == 3

    add_op = ops[0]
    assert add_op.kind == "ADD"
    assert add_op.section == "Scenarios"
    assert add_op.anchor == "scenario new-login"
    assert add_op.old_text is None

    remove_op = ops[1]
    assert remove_op.kind == "REMOVE"
    assert remove_op.section == "Scenarios"
    assert remove_op.anchor == "scenario old-login"
    assert remove_op.new_text == ""

    modify_op = ops[2]
    assert modify_op.kind == "MODIFY"
    assert modify_op.section == "Scenarios"
    assert modify_op.anchor == "criterion 1"
    assert modify_op.old_text == "old text"
    assert modify_op.new_text == "new text"


def test_multi_section_tagged_ops() -> None:
    """Test 2: Multi-section [Section]-tagged ops."""
    text = _multi_section_body(
        "+ ADD: [Scenarios] scenario new-feature\n"
        '~ MODIFY: [Acceptance Criteria] criterion 2 — was "old guard", now "new guard"\n'
    )
    ops = parse_proposal_body(text)
    assert len(ops) == 2

    add_op = ops[0]
    assert add_op.kind == "ADD"
    assert add_op.section == "Scenarios"
    assert add_op.anchor == "scenario new-feature"

    modify_op = ops[1]
    assert modify_op.kind == "MODIFY"
    assert modify_op.section == "Acceptance Criteria"
    assert modify_op.anchor == "criterion 2"
    assert modify_op.old_text == "old guard"
    assert modify_op.new_text == "new guard"


def test_untagged_op_when_multiple_sections_raises() -> None:
    """Test 3: Untagged op when >= 2 sections in Affects raises DeltaMergeError."""
    text = _multi_section_body("+ ADD: scenario foo\n")
    with pytest.raises(DeltaMergeError, match="missing required \\[Section\\] tag"):
        parse_proposal_body(text)


def test_tag_not_in_affects_raises() -> None:
    """Test 4: Tag whose section is not in ## Affects raises DeltaMergeError."""
    text = _multi_section_body("+ ADD: [Decisions] decision X\n")
    with pytest.raises(DeltaMergeError, match="not declared in ## Affects"):
        parse_proposal_body(text)


def test_multi_line_gherkin_add() -> None:
    """Test 5: Multi-line Gherkin ADD parsed as one op with multi-line new_text."""
    gherkin_block = (
        "+ ADD: scenario user-login\n"
        "  Scenario: User login\n"
        "    Given the login page is open\n"
        "    When the user enters valid credentials\n"
        "    Then the user is redirected to the dashboard\n"
    )
    text = _single_section_body(gherkin_block)
    ops = parse_proposal_body(text)
    assert len(ops) == 1
    op = ops[0]
    assert op.kind == "ADD"
    assert op.anchor == "scenario user-login"
    assert "Scenario: User login" in op.new_text
    assert "Given the login page is open" in op.new_text
    assert "When the user enters valid credentials" in op.new_text
    assert "Then the user is redirected to the dashboard" in op.new_text


def test_fenced_table_modify() -> None:
    """Test 6: Fenced-table MODIFY — fenced block joins op body even if not indented."""
    fenced_block = (
        '~ MODIFY: criterion 3 — was "old", now "new"\n'
        "```\n"
        "| col1 | col2 |\n"
        "|------|------|\n"
        "| a    | b    |\n"
        "```\n"
    )
    text = _single_section_body(fenced_block)
    ops = parse_proposal_body(text)
    assert len(ops) == 1
    op = ops[0]
    assert op.kind == "MODIFY"
    assert "col1" in op.new_text
    assert "col2" in op.new_text


def test_indented_list_add() -> None:
    """Test 7: Indented-list ADD — header + indented bullet lines parsed as one op."""
    list_block = "+ ADD: scenario bulk-upload\n  - item one\n  - item two\n  - item three\n"
    text = _single_section_body(list_block)
    ops = parse_proposal_body(text)
    assert len(ops) == 1
    op = ops[0]
    assert op.kind == "ADD"
    assert "item one" in op.new_text
    assert "item two" in op.new_text
    assert "item three" in op.new_text


def test_op_closes_on_next_op_marker_at_column_0() -> None:
    """Test 8: Body of first op stops cleanly when next op marker appears at col 0."""
    two_ops = "+ ADD: scenario a\n  line in first op\n+ ADD: scenario b\n  line in second op\n"
    text = _single_section_body(two_ops)
    ops = parse_proposal_body(text)
    assert len(ops) == 2
    assert "line in first op" in ops[0].new_text
    assert "line in second op" in ops[1].new_text
    # Cross-contamination check
    assert "line in second op" not in ops[0].new_text
    assert "line in first op" not in ops[1].new_text


def test_op_closes_on_h2() -> None:
    """Test 9: Op body ends when ## OtherSection appears."""
    text = (
        _FRONTMATTER
        + _AFFECTS_SINGLE
        + "\n"
        + _DELTA_HEADER
        + "+ ADD: scenario a\n"
        + "  body line\n"
        + "\n## Rationale\n\nSome rationale text.\n"
    )
    ops = parse_proposal_body(text)
    assert len(ops) == 1
    op = ops[0]
    assert "body line" in op.new_text
    assert "rationale" not in op.new_text.lower()


def test_op_closes_on_eof() -> None:
    """Test 10: Last op's body extends to end of the body text."""
    text = _single_section_body("+ ADD: scenario a\n  line one\n  line two\n")
    ops = parse_proposal_body(text)
    assert len(ops) == 1
    assert "line one" in ops[0].new_text
    assert "line two" in ops[0].new_text


def test_empty_delta_section_raises() -> None:
    """Test 11: Empty ## Delta section raises DeltaMergeError."""
    text = _FRONTMATTER + _AFFECTS_SINGLE + "\n## Delta\n\n## Rationale\n\nsome text\n"
    with pytest.raises(DeltaMergeError, match="missing ## Delta"):
        parse_proposal_body(text)


def test_missing_affects_section_raises() -> None:
    """Test 12: Missing ## Affects section raises DeltaMergeError."""
    text = _FRONTMATTER + "## Delta\n\n+ ADD: scenario foo\n"
    with pytest.raises(DeltaMergeError, match="missing ## Affects"):
        parse_proposal_body(text)


def test_modify_missing_was_now_form_raises() -> None:
    """Test 13: MODIFY without 'was "<old>", now "<new>"' form raises DeltaMergeError."""
    text = _single_section_body("~ MODIFY: criterion 1 just some text\n")
    with pytest.raises(DeltaMergeError, match="MODIFY missing"):
        parse_proposal_body(text)


def test_op_marker_inside_fenced_body_not_parsed_as_new_op() -> None:
    """Test 14: Op marker text inside a fenced body must NOT open a new op."""
    fenced_block = (
        "+ ADD: scenario x\n"
        "```\n"
        "+ ADD: this should NOT be a new op\n"
        "- REMOVE: neither should this\n"
        "```\n"
    )
    text = _single_section_body(fenced_block)
    ops = parse_proposal_body(text)
    assert len(ops) == 1, f"Expected 1 op, got {len(ops)}: {ops}"
    op = ops[0]
    assert "ADD: this should NOT" in op.new_text
    assert "REMOVE: neither" in op.new_text


# ---------------------------------------------------------------------------
# Applier tests — apply_delta_ops
# ---------------------------------------------------------------------------

_CANONICAL_SPEC = """\
---
capability: my-cap
---

## Intent

Some intent text.

## Scenarios

scenario a
  detail line for a

scenario b

## Acceptance Criteria

criterion 1: old text
criterion 2: stable
"""


def _make_op(
    kind: str,
    section: str,
    anchor: str,
    new_text: str,
    old_text: str | None = None,
) -> DeltaOp:
    kind_lit: Literal["ADD", "REMOVE", "MODIFY"] = kind  # type: ignore[assignment]
    return DeltaOp(
        kind=kind_lit, section=section, anchor=anchor, old_text=old_text, new_text=new_text
    )


def test_add_appends_to_declared_section_single() -> None:
    """Test 15: ADD appends to declared section, multi-line body verbatim with blank-line separator."""
    op = _make_op("ADD", "Scenarios", "scenario c", "scenario c\n  detail for c")
    result = apply_delta_ops(_CANONICAL_SPEC, [op])
    scenarios_section = result.split("## Acceptance Criteria")[0]
    assert "scenario c" in scenarios_section
    assert "detail for c" in scenarios_section
    # New text should appear after existing scenario b
    assert result.index("scenario b") < result.index("scenario c")


def test_add_with_section_tag_appends_to_that_section() -> None:
    """Test 16: ADD with [Section] tag appends to that specific section."""
    op = _make_op("ADD", "Acceptance Criteria", "criterion 3", "criterion 3: new criterion")
    result = apply_delta_ops(_CANONICAL_SPEC, [op])
    ac_section = result.split("## Acceptance Criteria")[1]
    assert "criterion 3: new criterion" in ac_section


def test_remove_strips_anchor_line_and_indented_continuation() -> None:
    """Test 17: REMOVE strips anchor line + indented continuation lines."""
    op = _make_op("REMOVE", "Scenarios", "scenario a", "")
    result = apply_delta_ops(_CANONICAL_SPEC, [op])
    assert "scenario a" not in result
    assert "detail line for a" not in result
    # Other scenarios untouched
    assert "scenario b" in result


def test_remove_strips_anchor_line_and_fenced_continuation() -> None:
    """Test 18: REMOVE strips anchor line + fenced continuation block."""
    spec_with_fence = (
        "---\ncapability: my-cap\n---\n\n"
        "## Scenarios\n\n"
        "scenario x\n"
        "```\nfenced content\n```\n\n"
        "scenario y\n"
    )
    op = _make_op("REMOVE", "Scenarios", "scenario x", "")
    result = apply_delta_ops(spec_with_fence, [op])
    assert "scenario x" not in result
    assert "fenced content" not in result
    assert "scenario y" in result


def test_remove_missing_anchor_raises() -> None:
    """Test 19: REMOVE missing anchor raises DeltaMergeError."""
    op = _make_op("REMOVE", "Scenarios", "scenario nonexistent", "")
    with pytest.raises(DeltaMergeError, match="REMOVE anchor not found"):
        apply_delta_ops(_CANONICAL_SPEC, [op])


def test_remove_ambiguous_anchor_raises_with_count() -> None:
    """Test 20: REMOVE ambiguous anchor raises with N in message."""
    spec_with_dupes = (
        "---\ncapability: my-cap\n---\n\n"
        "## Scenarios\n\n"
        "criterion 1: first occurrence\n"
        "criterion 1: second occurrence\n"
    )
    op = _make_op("REMOVE", "Scenarios", "criterion 1", "")
    with pytest.raises(DeltaMergeError, match=r"REMOVE anchor ambiguous.*2"):
        apply_delta_ops(spec_with_dupes, [op])


def test_modify_happy_path_replaces_block() -> None:
    """Test 21: MODIFY anchor matches, guard matches, block replaced (multi-line)."""
    new_body = "criterion 1: updated text\n  extra detail"
    op = _make_op("MODIFY", "Acceptance Criteria", "criterion 1", new_body, old_text="old text")
    result = apply_delta_ops(_CANONICAL_SPEC, [op])
    assert "criterion 1: updated text" in result
    assert "extra detail" in result
    assert "old text" not in result


def test_modify_anchor_missing_raises() -> None:
    """Test 22: MODIFY anchor missing raises DeltaMergeError."""
    op = _make_op("MODIFY", "Acceptance Criteria", "criterion 99", "new text", old_text="x")
    with pytest.raises(DeltaMergeError, match="MODIFY anchor not found"):
        apply_delta_ops(_CANONICAL_SPEC, [op])


def test_modify_anchor_ambiguous_raises() -> None:
    """Test 23: MODIFY anchor ambiguous raises DeltaMergeError."""
    spec_with_dupes = (
        "---\ncapability: my-cap\n---\n\n"
        "## Scenarios\n\n"
        "scenario dup: first\n"
        "scenario dup: second\n"
    )
    op = _make_op("MODIFY", "Scenarios", "scenario dup", "new text", old_text="first")
    with pytest.raises(DeltaMergeError, match="MODIFY anchor ambiguous"):
        apply_delta_ops(spec_with_dupes, [op])


def test_modify_guard_mismatch_raises() -> None:
    """Test 24: MODIFY guard mismatch raises with anchor + missing-text in message."""
    op = _make_op(
        "MODIFY",
        "Acceptance Criteria",
        "criterion 1",
        "criterion 1: replaced",
        old_text="this text is not in criterion 1",
    )
    with pytest.raises(DeltaMergeError, match="MODIFY guard mismatch"):
        apply_delta_ops(_CANONICAL_SPEC, [op])


def test_multiple_ops_same_section_apply_top_to_bottom() -> None:
    """Test 25: Multiple ops on same section apply top-to-bottom against running merged body."""
    spec = "---\ncapability: my-cap\n---\n\n## Scenarios\n\nscenario a\nscenario b\n"
    op1 = _make_op("REMOVE", "Scenarios", "scenario a", "")
    op2 = _make_op("ADD", "Scenarios", "scenario c", "scenario c")
    result = apply_delta_ops(spec, [op1, op2])
    assert "scenario a" not in result
    assert "scenario b" in result
    assert "scenario c" in result


def test_determinism_same_input_same_output() -> None:
    """Test 26: Same input produces same output on two separate calls."""
    op = _make_op("ADD", "Scenarios", "scenario new", "scenario new\n  detail")
    result1 = apply_delta_ops(_CANONICAL_SPEC, [op])
    result2 = apply_delta_ops(_CANONICAL_SPEC, [op])
    assert result1 == result2


# ---------------------------------------------------------------------------
# Additional edge-case tests for parser robustness
# ---------------------------------------------------------------------------


def test_remove_op_header_with_single_line() -> None:
    """REMOVE op with a single-line header has empty new_text."""
    text = _single_section_body("- REMOVE: scenario old\n")
    ops = parse_proposal_body(text)
    assert len(ops) == 1
    op = ops[0]
    assert op.kind == "REMOVE"
    assert op.anchor == "scenario old"
    assert op.new_text == ""


def test_remove_op_multi_line_block() -> None:
    """REMOVE op can have multi-line body (the body defines what gets removed)."""
    text = _single_section_body("- REMOVE: scenario old\n  Given context\n  When action\n")
    ops = parse_proposal_body(text)
    assert len(ops) == 1
    op = ops[0]
    assert op.kind == "REMOVE"
    assert op.anchor == "scenario old"


def test_tagged_remove_op_multi_section() -> None:
    """Tagged REMOVE op resolves section from tag in multi-section context."""
    text = _multi_section_body("- REMOVE: [Scenarios] scenario old\n")
    ops = parse_proposal_body(text)
    assert len(ops) == 1
    op = ops[0]
    assert op.kind == "REMOVE"
    assert op.section == "Scenarios"
    assert op.anchor == "scenario old"


def test_affects_sections_list_parsed_with_whitespace() -> None:
    """Sections list parsing handles spaces around comma separators."""
    text = (
        _FRONTMATTER
        + "## Affects\n\nsections [Scenarios , Acceptance Criteria]\n\n"
        + _DELTA_HEADER
        + "+ ADD: [Scenarios] scenario x\n"
    )
    ops = parse_proposal_body(text)
    assert len(ops) == 1
    assert ops[0].section == "Scenarios"


def test_blank_lines_in_multi_line_body_preserved() -> None:
    """Blank lines within a multi-line op body are preserved."""
    text = _single_section_body("+ ADD: scenario z\n  Scenario: Z\n\n    Given something\n")
    ops = parse_proposal_body(text)
    assert len(ops) == 1
    # blank line should appear in new_text (it's inside the op body)
    assert "\n" in ops[0].new_text


def test_multiple_ops_parsed_correctly_three_kinds() -> None:
    """Three ops (ADD/REMOVE/MODIFY) all extracted with correct kinds."""
    text = _single_section_body(
        '+ ADD: scenario new\n- REMOVE: scenario old\n~ MODIFY: criterion 1 — was "x", now "y"\n'
    )
    ops = parse_proposal_body(text)
    kinds = [op.kind for op in ops]
    assert kinds == ["ADD", "REMOVE", "MODIFY"]


def test_add_appends_with_blank_line_separator() -> None:
    """ADD places a blank line between existing section content and new_text."""
    spec = "---\ncapability: my-cap\n---\n\n## Scenarios\n\nscenario a\n"
    op = _make_op("ADD", "Scenarios", "scenario b", "scenario b")
    result = apply_delta_ops(spec, [op])
    # There should be a blank line between scenario a and scenario b
    assert "\nscenario a\n\nscenario b\n" in result or "scenario a\n\nscenario b" in result


def test_section_not_found_in_canonical_raises_on_apply() -> None:
    """apply_delta_ops raises DeltaMergeError if the target section does not exist."""
    spec = "---\ncapability: my-cap\n---\n\n## Scenarios\n\nscenario a\n"
    op = _make_op("ADD", "Nonexistent Section", "foo", "bar")
    with pytest.raises(DeltaMergeError, match="section not found"):
        apply_delta_ops(spec, [op])


def test_modify_replaces_multiline_block_in_canonical() -> None:
    """MODIFY replaces the entire block (header + indented continuation) of the anchor."""
    spec = (
        "---\ncapability: my-cap\n---\n\n"
        "## Acceptance Criteria\n\n"
        "criterion 1: old value\n"
        "  old detail line\n"
        "criterion 2: stable\n"
    )
    new_block = "criterion 1: new value\n  new detail line"
    op = _make_op("MODIFY", "Acceptance Criteria", "criterion 1", new_block, old_text="old value")
    result = apply_delta_ops(spec, [op])
    assert "criterion 1: new value" in result
    assert "new detail line" in result
    assert "old value" not in result
    assert "old detail line" not in result
    assert "criterion 2: stable" in result


def test_parse_proposal_body_with_no_frontmatter_separator() -> None:
    """Body without YAML frontmatter but with Affects + Delta still parsed."""
    text = "## Affects\n\nsections [Scenarios]\n\n## Delta\n\n+ ADD: scenario foo\n"
    ops = parse_proposal_body(text)
    assert len(ops) == 1
    assert ops[0].anchor == "scenario foo"


def test_affects_without_sections_list_single_op_uses_empty_section() -> None:
    """When ## Affects has no 'sections [...]' list, declared_sections is empty and section=''."""
    text = (
        "## Affects\n\nThis capability affects the core engine.\n\n"
        "## Delta\n\n+ ADD: scenario foo\n"
    )
    # With empty declared_sections, single-section path gives empty string
    ops = parse_proposal_body(text)
    assert len(ops) == 1
    assert ops[0].section == ""


def test_add_to_empty_section_body() -> None:
    """ADD to a section that has no content (blank body) still works."""
    spec = "---\ncapability: my-cap\n---\n\n## Scenarios\n\n## Acceptance Criteria\n\ncrit 1\n"
    op = _make_op("ADD", "Scenarios", "scenario x", "scenario x")
    result = apply_delta_ops(spec, [op])
    assert "scenario x" in result


def test_anchor_block_with_blank_then_indented_continuation() -> None:
    """Block spanning anchor + blank line + indented continuation stays together for REMOVE."""
    spec = (
        "---\ncapability: my-cap\n---\n\n"
        "## Scenarios\n\n"
        "scenario a\n"
        "\n"
        "  continuation after blank\n"
        "scenario b\n"
    )
    op = _make_op("REMOVE", "Scenarios", "scenario a", "")
    result = apply_delta_ops(spec, [op])
    assert "scenario a" not in result
    assert "continuation after blank" not in result
    assert "scenario b" in result


def test_modify_with_no_old_text_guard_skipped() -> None:
    """When old_text is None on a MODIFY op, the guard check is skipped."""
    # Build a DeltaOp directly with old_text=None and apply it
    spec = "---\ncapability: my-cap\n---\n\n## Scenarios\n\nscenario a: value\n"
    op = DeltaOp(
        kind="MODIFY",
        section="Scenarios",
        anchor="scenario a",
        old_text=None,
        new_text="scenario a: replaced",
    )
    result = apply_delta_ops(spec, [op])
    assert "scenario a: replaced" in result
    assert "value" not in result


def test_delta_section_missing_triggers_error() -> None:
    """Missing ## Delta section (no header at all) raises DeltaMergeError."""
    text = "## Affects\n\nsections [Scenarios]\n\n## Rationale\n\nsome text\n"
    with pytest.raises(DeltaMergeError, match="missing ## Delta"):
        parse_proposal_body(text)


# ---------------------------------------------------------------------------
# H1 — Fence-awareness in section + anchor extraction
# ---------------------------------------------------------------------------


def test_extract_section_ignores_fenced_h2_collision() -> None:
    """A fenced ## Scenarios block in the canonical body must NOT shadow
    the real ## Scenarios section.

    Without fence-awareness, REMOVE/MODIFY anchors in the real section
    silently target the fenced example and can wipe the real heading.
    """
    canonical = (
        "## Intent\n"
        "intent body\n"
        "\n"
        "```\n"
        "## Scenarios\n"
        "fake content inside the fence\n"
        "```\n"
        "\n"
        "## Scenarios\n"
        "scenario-1: works\n"
        "scenario-2: also works\n"
        "\n"
        "## Acceptance Criteria\n"
        "- criterion-1: ok\n"
    )
    op = DeltaOp(
        kind="REMOVE",
        section="Scenarios",
        anchor="scenario-1",
        old_text=None,
        new_text="",
    )
    result = apply_delta_ops(canonical, [op])
    # The real section's scenario-1 line is gone; scenario-2 remains;
    # the fenced example is untouched; ## Scenarios real heading remains.
    assert "scenario-1" not in result
    assert "scenario-2: also works" in result
    assert "## Scenarios" in result  # real heading not corrupted
    assert "fake content inside the fence" in result
    assert "## Acceptance Criteria" in result


# ---------------------------------------------------------------------------
# M2 — Reject empty MODIFY 'was' guard
# ---------------------------------------------------------------------------


def test_modify_form_rejects_empty_was_guard() -> None:
    """`was "", now "X"` is not a meaningful guard and must be rejected by the parser."""
    body = (
        "## Affects\n"
        "- spec: foo — sections [Scenarios]\n"
        "## Delta\n"
        '~ MODIFY: scenario-1 — was "", now "new value"\n'
    )
    with pytest.raises(DeltaMergeError, match="MODIFY"):
        parse_proposal_body(body)


def test_apply_modify_rejects_empty_old_text_directly() -> None:
    """Direct DeltaOp construction with empty old_text must also raise.

    Defense in depth — even if a caller bypasses parse_proposal_body,
    apply_delta_ops should reject an empty MODIFY guard."""
    op = DeltaOp(
        kind="MODIFY",
        section="Scenarios",
        anchor="scenario-1",
        old_text="",
        new_text="anything",
    )
    canonical = "## Scenarios\nscenario-1: works\n"
    with pytest.raises(DeltaMergeError, match="empty"):
        apply_delta_ops(canonical, [op])


# ---------------------------------------------------------------------------
# Reviewer-3 Critical — fence-aware ## Delta section extraction
# ---------------------------------------------------------------------------


def test_fenced_h2_inside_delta_op_does_not_truncate_section() -> None:
    """A fenced ``## ...`` line inside an op body must NOT close ``## Delta``.

    Without fence-aware section extraction, the H2 search inside the fenced
    block ends the ``## Delta`` section early; the parsed op body collapses
    to just the opening fence, and a subsequent ADD op silently disappears
    (or, worse, the merger writes a dangling fence into the canonical spec).

    Reproduces the Reviewer-3 Critical: "fenced ## lines inside a delta op
    truncate the proposal before merge".
    """
    proposal = (
        "## Affects\n"
        "\n"
        "sections [Scenarios]\n"
        "\n"
        "## Delta\n"
        "\n"
        "+ ADD: scenario doc-example\n"
        "  Scenario shows how to write a fenced H2 in docs:\n"
        "\n"
        "  ```\n"
        "  ## Inside fenced block\n"
        "  this is illustrative content, not a section header\n"
        "  ```\n"
        "\n"
        "+ ADD: scenario after-fence\n"
        "  This op exists AFTER the fenced ## line.  Without fence-aware\n"
        "  section extraction, this op disappears from the parse.\n"
        "\n"
        "## Rationale\n"
        "\n"
        "Some rationale.\n"
    )
    ops = parse_proposal_body(proposal)

    # Both ADD ops must be parsed; the second one is the canary for
    # fence-aware ## handling.
    kinds = [op.kind for op in ops]
    anchors = [op.anchor for op in ops]
    assert kinds == ["ADD", "ADD"], f"expected 2 ADD ops, got {kinds}"
    assert anchors == ["scenario doc-example", "scenario after-fence"]

    # First op's body must contain the fenced literal, including the
    # illustrative ## line — fence content is preserved verbatim.
    first = ops[0]
    assert "## Inside fenced block" in first.new_text
    assert "illustrative content" in first.new_text

    # Critically: first op's body must NOT have been truncated at the
    # fenced ##; the rationale section text must NOT appear in any op body.
    for op in ops:
        assert "Some rationale" not in op.new_text


def test_fenced_h2_in_affects_section_does_not_truncate() -> None:
    """Same fence-awareness on ## Affects extraction.

    A fenced ## line inside the Affects body must not end the Affects
    section early — otherwise the sections list is missed and downstream
    ops become un-routable.
    """
    proposal = (
        "## Affects\n"
        "\n"
        "```\n"
        "## Example heading from another spec\n"
        "```\n"
        "\n"
        "sections [Scenarios]\n"
        "\n"
        "## Delta\n"
        "\n"
        "+ ADD: scenario foo\n"
        "  body line\n"
    )
    ops = parse_proposal_body(proposal)
    assert len(ops) == 1
    assert ops[0].section == "Scenarios"
    assert ops[0].anchor == "scenario foo"
