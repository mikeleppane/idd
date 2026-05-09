"""Tests for validate_capability_spec_sections structural check."""

from __future__ import annotations

from pathlib import Path

from tools.validate.spec_structural import validate_capability_spec_sections

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ALL_SECTIONS = """\
## Intent

One paragraph: WHY this capability exists.

## Scope

In Scope, Out of Scope.

## Domain

Glossary table.

## Scenarios

Gherkin scenarios.

## Acceptance Criteria

Falsifiable criteria.

## Negative Requirements

MUST-NOT statements.

## Decisions

Append-only links.
"""


def _write_spec(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "SPEC.md"
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


def test_all_sections_present_returns_no_findings(tmp_path: Path) -> None:
    """All 7 required H2 sections present → empty findings list."""
    path = _write_spec(tmp_path, _ALL_SECTIONS)
    findings = validate_capability_spec_sections(path)
    assert findings == []


def test_missing_intent_returns_one_block_finding(tmp_path: Path) -> None:
    """Missing '## Intent' → exactly 1 BLOCK finding, message contains 'Intent'."""
    content = _ALL_SECTIONS.replace("## Intent\n", "")
    path = _write_spec(tmp_path, content)
    findings = validate_capability_spec_sections(path)
    assert len(findings) == 1
    assert findings[0].severity == "BLOCK"
    assert "Intent" in findings[0].message


def test_missing_scenarios_returns_one_block_finding(tmp_path: Path) -> None:
    """Missing '## Scenarios' → exactly 1 BLOCK finding, message contains 'Scenarios'."""
    content = _ALL_SECTIONS.replace("## Scenarios\n", "")
    path = _write_spec(tmp_path, content)
    findings = validate_capability_spec_sections(path)
    assert len(findings) == 1
    assert findings[0].severity == "BLOCK"
    assert "Scenarios" in findings[0].message


def test_multiple_missing_sections_returns_one_finding_each(tmp_path: Path) -> None:
    """Missing '## Domain' AND '## Decisions' → exactly 2 BLOCK findings."""
    content = _ALL_SECTIONS.replace("## Domain\n", "").replace("## Decisions\n", "")
    path = _write_spec(tmp_path, content)
    findings = validate_capability_spec_sections(path)
    assert len(findings) == 2
    messages = {f.message for f in findings}
    assert all(f.severity == "BLOCK" for f in findings)
    assert any("Domain" in m for m in messages)
    assert any("Decisions" in m for m in messages)


def test_h3_section_header_does_not_count(tmp_path: Path) -> None:
    """'### Intent' (H3) does not satisfy the H2 requirement → BLOCK for Intent."""
    content = _ALL_SECTIONS.replace("## Intent\n", "### Intent\n")
    path = _write_spec(tmp_path, content)
    findings = validate_capability_spec_sections(path)
    assert len(findings) == 1
    assert findings[0].severity == "BLOCK"
    assert "Intent" in findings[0].message


def test_empty_file_returns_seven_block_findings(tmp_path: Path) -> None:
    """Empty file → 7 BLOCK findings, one per required section."""
    path = _write_spec(tmp_path, "")
    findings = validate_capability_spec_sections(path)
    assert len(findings) == 7
    assert all(f.severity == "BLOCK" for f in findings)


def test_sections_in_different_order_still_passes(tmp_path: Path) -> None:
    """Sections in non-canonical order → still returns no findings (order not enforced)."""
    content = """\
## Decisions

Links.

## Negative Requirements

Must not.

## Acceptance Criteria

Criteria.

## Scenarios

Scenarios.

## Domain

Glossary.

## Scope

Scope.

## Intent

Intent.
"""
    path = _write_spec(tmp_path, content)
    findings = validate_capability_spec_sections(path)
    assert findings == []


def test_file_not_found_returns_block_finding(tmp_path: Path) -> None:
    """Non-existent file → BLOCK finding with 'not found' in message."""
    path = tmp_path / "absent.md"
    findings = validate_capability_spec_sections(path)
    assert len(findings) == 1
    assert findings[0].severity == "BLOCK"
    assert "not found" in findings[0].message.lower()


def test_section_header_with_trailing_whitespace_matches(tmp_path: Path) -> None:
    """'## Intent   ' (trailing spaces) → still counts as present."""
    content = _ALL_SECTIONS.replace("## Intent\n", "## Intent   \n")
    path = _write_spec(tmp_path, content)
    findings = validate_capability_spec_sections(path)
    assert findings == []


def test_validate_capability_spec_sections_ignores_fenced_h2_headers(tmp_path: Path) -> None:
    """All seven H2 headers fenced inside a code block must not satisfy the
    section-presence check — fenced content is documentation, not structure."""
    spec = tmp_path / "SPEC.md"
    spec.write_text(
        "```markdown\n"
        "## Intent\n"
        "## Scope\n"
        "## Domain\n"
        "## Scenarios\n"
        "## Acceptance Criteria\n"
        "## Negative Requirements\n"
        "## Decisions\n"
        "```\n"
        "\n"
        "(no real sections present — all headers are inside the fence)\n",
        encoding="utf-8",
    )
    findings = validate_capability_spec_sections(spec)
    assert len(findings) == 7  # one BLOCK per missing required section
    assert all(f.severity == "BLOCK" for f in findings)


def test_body_content_under_section_is_irrelevant(tmp_path: Path) -> None:
    """Rich body content under each H2 → still returns no findings."""
    content = """\
## Intent

This is a **long** paragraph with lots of content.
It spans multiple lines and includes [links](https://example.com).

## Scope

- In scope: feature A
- Out of scope: feature B

## Domain

| Term | Definition |
|------|-----------|
| foo  | bar        |

## Scenarios

```gherkin
Scenario: basic
  Given something
  Then something else
```

## Acceptance Criteria

1. AC-1: measurable outcome
2. AC-2: another outcome

## Negative Requirements

The system MUST NOT do bad things.

## Decisions

- 2026-01-01-some-decision
"""
    path = _write_spec(tmp_path, content)
    findings = validate_capability_spec_sections(path)
    assert findings == []
