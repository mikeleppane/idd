"""Tests for ``tools.cross_ai.parse`` — external response → ``Finding`` rows.

Cases (a)-(i) per the cross-ai substrate plan:

  * (a) Clean response with two findings parses into two ``Finding``
    tuples, each carrying the overridden ``source`` field.
  * (b) Explanatory paragraph before the table does not block the parse —
    the first matching header anywhere in the body wins.
  * (c) No table at all → empty tuple, ``ParseWarning`` emitted so the
    caller can decide whether to surface it to the operator.
  * (d) Malformed table (missing required column) → empty tuple,
    ``ParseWarning`` emitted; a half-formed table is never silently
    accepted as a clean review.
  * (e) Constitution tag ``[constitution:A1]`` in the Problem column
    survives verbatim — the dispatcher routes findings back to the
    originating Article, so any reformatting breaks the link.
  * (f) Every documented severity (``BLOCK`` / ``HIGH`` / ``MEDIUM`` /
    ``LOW`` / ``INFO``) round-trips unchanged.
  * (g) Unknown severity (``URGENT``) is preserved verbatim — the parser
    is a vocabulary-neutral pipe; the caller maps unknowns later.
  * (h) ``status`` is preserved verbatim, including reviewer-blank cells
    — the parser never injects a default.
  * (i) ``Finding`` is frozen — downstream code cannot mutate rows in
    place, so the report stays internally consistent.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from tools.cross_ai.parse import Finding, ParseWarning, Severity, parse_response

_CLEAN_TWO_FINDINGS = """\
# Cross-AI review

Reviewer surfaced 2 findings.

| ID | Severity | Status | Location | Problem | Fix | Source |
|---|---|---|---|---|---|---|
| F1 | HIGH | open | tools/foo.py:12 | direct ORM call | move to repo | reviewer |
| F2 | MEDIUM | open | tools/bar.py:34 | missing test | add unit test | reviewer |
"""

_PARAGRAPH_BEFORE_TABLE = """\
The diff looks reasonable overall. One concern noted below.

Some prose in between.

| ID | Severity | Status | Location | Problem | Fix | Source |
|---|---|---|---|---|---|---|
| F1 | LOW | open | tools/baz.py:1 | nit | rename | reviewer |
"""

_NO_TABLE = """\
# Cross-AI review

Looks clean. No findings.
"""

_MALFORMED_TABLE_MISSING_COLUMN = """\
# Cross-AI review

| ID | Severity | Location | Problem | Fix | Source |
|---|---|---|---|---|---|
| F1 | HIGH | tools/foo.py:12 | direct ORM call | move to repo | reviewer |
"""

_CONSTITUTION_TAG = """\
| ID | Severity | Status | Location | Problem | Fix | Source |
|---|---|---|---|---|---|---|
| F1 | HIGH | open | tools/foo.py:12 | [constitution:A1] direct ORM call | move to repo | constitution:A1 |
"""

_BLANK_STATUS = """\
| ID | Severity | Status | Location | Problem | Fix | Source |
|---|---|---|---|---|---|---|
| F1 | HIGH |  | tools/foo.py:12 | thing | fix it | reviewer |
"""

_UNKNOWN_SEVERITY = """\
| ID | Severity | Status | Location | Problem | Fix | Source |
|---|---|---|---|---|---|---|
| F1 | URGENT | open | tools/foo.py:12 | thing | fix it | reviewer |
"""


def _every_severity_table() -> str:
    rows = "\n".join(
        f"| F{i} | {sev} | open | tools/x.py:{i} | p | f | reviewer |"
        for i, sev in enumerate(("BLOCK", "HIGH", "MEDIUM", "LOW", "INFO"), start=1)
    )
    return (
        "| ID | Severity | Status | Location | Problem | Fix | Source |\n"
        "|---|---|---|---|---|---|---|\n"
        f"{rows}\n"
    )


def test_parse_response_returns_two_findings_and_overrides_source() -> None:
    # (a) Two clean rows; source overridden to external-<reviewer_id>.
    result = parse_response(_CLEAN_TWO_FINDINGS, reviewer_id="codex", target="plan")

    assert len(result) == 2
    assert all(isinstance(f, Finding) for f in result)
    assert result[0].id == "F1"
    assert result[0].severity == "HIGH"
    assert result[0].status == "open"
    assert result[0].location == "tools/foo.py:12"
    assert result[0].problem == "direct ORM call"
    assert result[0].fix == "move to repo"
    assert result[0].source == "external-codex"
    assert result[1].id == "F2"
    assert result[1].source == "external-codex"


def test_parse_response_skips_explanatory_paragraph_before_table() -> None:
    # (b) Body has prose then a table; the parser locates the table.
    result = parse_response(_PARAGRAPH_BEFORE_TABLE, reviewer_id="claude", target="code")

    assert len(result) == 1
    assert result[0].id == "F1"
    assert result[0].severity == "LOW"
    assert result[0].source == "external-claude"


def test_parse_response_with_no_table_returns_empty_tuple_and_warns() -> None:
    # (c) No findings table → empty tuple + ParseWarning so callers can
    # surface the missing-table condition to the operator.
    with pytest.warns(ParseWarning):
        result = parse_response(_NO_TABLE, reviewer_id="codex", target="plan")

    assert result == ()


def test_parse_response_with_malformed_table_returns_empty_tuple_and_warns() -> None:
    # (d) Table missing the required Status column is not silently
    # accepted — caller sees an empty tuple and a ParseWarning.
    with pytest.warns(ParseWarning):
        result = parse_response(
            _MALFORMED_TABLE_MISSING_COLUMN,
            reviewer_id="codex",
            target="plan",
        )

    assert result == ()


def test_parse_response_preserves_constitution_tag_in_problem_column() -> None:
    # (e) The dispatcher routes constitution-tagged findings back to the
    # originating Article; reformatting the tag would break the link.
    result = parse_response(_CONSTITUTION_TAG, reviewer_id="codex", target="plan")

    assert len(result) == 1
    assert result[0].problem == "[constitution:A1] direct ORM call"


def test_parse_response_preserves_every_documented_severity() -> None:
    # (f) BLOCK / HIGH / MEDIUM / LOW / INFO all round-trip unchanged.
    result = parse_response(_every_severity_table(), reviewer_id="codex", target="plan")

    severities = [f.severity for f in result]
    assert severities == ["BLOCK", "HIGH", "MEDIUM", "LOW", "INFO"]
    # Sanity: the Severity vocabulary list matches the documented set.
    assert {s.value for s in Severity} == {"BLOCK", "HIGH", "MEDIUM", "LOW", "INFO"}


def test_parse_response_preserves_unknown_severity_verbatim() -> None:
    # (g) Unknown severity passes through — the parser is a
    # vocabulary-neutral pipe.
    result = parse_response(_UNKNOWN_SEVERITY, reviewer_id="codex", target="plan")

    assert len(result) == 1
    assert result[0].severity == "URGENT"


def test_parse_response_preserves_blank_status_verbatim() -> None:
    # (h) Reviewer-blank Status cell is not coerced to "open" — the
    # parser preserves whatever vocabulary the reviewer emitted.
    result = parse_response(_BLANK_STATUS, reviewer_id="codex", target="plan")

    assert len(result) == 1
    assert result[0].status == ""


def test_finding_is_frozen() -> None:
    # (i) Finding rows cannot be mutated in place after construction.
    finding = Finding(
        id="F1",
        severity="HIGH",
        status="open",
        location="tools/foo.py:12",
        problem="thing",
        fix="fix it",
        source="external-codex",
    )

    with pytest.raises(FrozenInstanceError):
        finding.severity = "LOW"  # type: ignore[misc]


# --- coverage: header candidate without separator → rejected ---------------


def test_header_lookalike_without_separator_is_rejected() -> None:
    """A prose line that names every required column but is not followed
    by a Markdown separator must NOT be accepted as a header — otherwise
    arbitrary inline mentions of the column names would parse as tables.
    """
    body = (
        "Reviewer mentioned the | ID | Severity | Status | Location | Problem | Fix | columns.\n"
        "But never produced an actual table.\n"
    )
    with pytest.warns(ParseWarning):
        result = parse_response(body, reviewer_id="codex", target="plan")
    assert result == ()


def test_table_terminates_on_blank_line_followed_by_prose() -> None:
    """The data-row loop must stop at the first non-table line so trailing
    prose under the table is not interpreted as a finding.
    """
    body = (
        "| ID | Severity | Status | Location | Problem | Fix | Source |\n"
        "|---|---|---|---|---|---|---|\n"
        "| F1 | HIGH | open | x.py:1 | issue | fix | reviewer |\n"
        "\n"
        "Closing thoughts: this looks fine otherwise.\n"
    )
    result = parse_response(body, reviewer_id="codex", target="plan")
    assert len(result) == 1
    assert result[0].id == "F1"


def test_blank_line_between_separator_and_first_row_tolerated() -> None:
    """An authoring style that spaces the separator one line away from
    the first data row must still parse — the skip-separator helper
    walks past intervening blanks so the table is not truncated.
    """
    body = (
        "| ID | Severity | Status | Location | Problem | Fix | Source |\n"
        "|---|---|---|---|---|---|---|\n"
        "\n"
        "| F1 | LOW | open | x.py:1 | issue | fix | reviewer |\n"
    )
    result = parse_response(body, reviewer_id="codex", target="plan")
    assert len(result) == 1
    assert result[0].severity == "LOW"


def test_header_followed_by_only_blank_lines_is_rejected() -> None:
    """A header candidate followed by EOF with only blank lines (no
    separator at all) is not a valid table — the parser walks the
    look-ahead helper to its terminal ``return False`` branch.
    """
    body = "| ID | Severity | Status | Location | Problem | Fix | Source |\n\n\n"
    with pytest.warns(ParseWarning):
        result = parse_response(body, reviewer_id="codex", target="plan")
    assert result == ()
