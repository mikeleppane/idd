"""Tests for ``tools.ship_gate.parse_review_findings_for_harvest``.

WS3-H2 closes a maintainability hazard: the forge-review harvest
sub-step previously hand-parsed REVIEW.code.md while
``parse_review_findings`` parsed the same table format on the ship-gate
side. Two parsers of one table invited silent drift on column-order
changes. This module pins the contract of the shared harvest helper so a
future REVIEW.md template change touches one path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tools import ship_gate as sg


def _write(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# Happy path                                                                  #
# --------------------------------------------------------------------------- #


def test_parse_review_findings_for_harvest_returns_only_sha_resolved_high_plus(
    tmp_path: Path,
) -> None:
    src = _write(
        tmp_path / "REVIEW.code.md",
        """---
spec: 2026-05-12-demo
target: code
status: open
cycles: 2
---

# Findings

| ID | Severity | Status | Resolved by | Location | Problem | Recommended Fix | Source |
|----|----------|--------|-------------|----------|---------|-----------------|--------|
| F-1 | BLOCK | open | | src/a.py:1 | [constitution:A1] open block | fix-a | self |
| F-2 | BLOCK | resolved | 1a2b3c4d5e6f7890abcdef1234567890abcdef12 | src/b.py:2 | [constitution:A1] block resolved | fix-b | heavy-subagent |
| F-3 | HIGH | resolved | spec-edit | PLAN.md slice 2 | [constitution:A2] high but spec-edit | fix-c | self |
| F-4 | MEDIUM | resolved | aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa | src/d.py:4 | [constitution:A3] medium drop | fix-d | self |
""",
    )
    candidates = sg.parse_review_findings_for_harvest(src)
    assert [c.row_id for c in candidates] == ["F-2"]
    only = candidates[0]
    assert only.severity == "BLOCK"
    assert only.resolved_by == "1a2b3c4d5e6f7890abcdef1234567890abcdef12"
    assert only.location == "src/b.py:2"
    assert "block resolved" in only.problem
    assert only.recommended_fix == "fix-b"
    assert only.article_tags == ("A1",)
    assert only.lesson_tags == ()


def test_parse_review_findings_for_harvest_extracts_lesson_and_article_tags(
    tmp_path: Path,
) -> None:
    src = _write(
        tmp_path / "REVIEW.code.md",
        """---
spec: 2026-05-12-demo
target: code
status: open
cycles: 1
---

# Findings

| ID | Severity | Status | Resolved by | Location | Problem | Recommended Fix | Source |
|----|----------|--------|-------------|----------|---------|-----------------|--------|
| F-1 | HIGH | resolved | 1a2b3c4d5e6f7890abcdef1234567890abcdef12 | src/a.py:1 | [constitution:A2] [lesson:L007] mixed tags | fix | heavy-subagent |
""",
    )
    [cand] = sg.parse_review_findings_for_harvest(src)
    assert cand.article_tags == ("A2",)
    assert cand.lesson_tags == ("L007",)


# --------------------------------------------------------------------------- #
# Severity-translation helper                                                 #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("review_severity", "lesson_severity"),
    [
        ("BLOCK", "CRITICAL"),
        ("HIGH", "HIGH"),
        ("MEDIUM", "MEDIUM"),
        ("LOW", "LOW"),
    ],
)
def test_review_to_lesson_severity_mapping(review_severity: str, lesson_severity: str) -> None:
    assert sg._REVIEW_TO_LESSON_SEVERITY[review_severity] == lesson_severity


# --------------------------------------------------------------------------- #
# Column-rename safety: header lookup, not positional indexing                #
# --------------------------------------------------------------------------- #


def test_parse_review_findings_for_harvest_uses_header_lookup_not_index(
    tmp_path: Path,
) -> None:
    """Reorder columns vs template; both parsers must still find the cells."""
    src = _write(
        tmp_path / "REVIEW.code.md",
        """---
spec: 2026-05-12-demo
target: code
status: open
cycles: 1
---

# Findings

| ID | Status | Severity | Location | Resolved by | Problem | Recommended Fix | Source |
|----|--------|----------|----------|-------------|---------|-----------------|--------|
| F-1 | resolved | HIGH | src/a.py:1 | 1a2b3c4d5e6f7890abcdef1234567890abcdef12 | [constitution:A1] reorder | fix-a | heavy-subagent |
| F-2 | open | HIGH | src/b.py:2 | | [constitution:A1] still open | fix-b | self |
""",
    )
    candidates = sg.parse_review_findings_for_harvest(src)
    assert len(candidates) == 1
    assert candidates[0].row_id == "F-1"
    assert candidates[0].resolved_by == "1a2b3c4d5e6f7890abcdef1234567890abcdef12"
    assert candidates[0].location == "src/a.py:1"
    assert candidates[0].recommended_fix == "fix-a"

    # And parse_review_findings, sharing the same header lookup, finds F-2.
    open_findings = sg.parse_review_findings(src)
    assert [f.article_id for f in open_findings] == ["A1"]
    assert open_findings[0].location == "src/b.py:2"


# --------------------------------------------------------------------------- #
# Missing file                                                                #
# --------------------------------------------------------------------------- #


def test_parse_review_findings_for_harvest_missing_file_returns_empty(
    tmp_path: Path,
) -> None:
    assert sg.parse_review_findings_for_harvest(tmp_path / "does_not_exist.md") == []


# --------------------------------------------------------------------------- #
# Resolved-by case normalization (slice 5 carryover)                          #
# --------------------------------------------------------------------------- #


def test_parse_review_findings_for_harvest_lowercases_sha(tmp_path: Path) -> None:
    src = _write(
        tmp_path / "REVIEW.code.md",
        """---
spec: 2026-05-12-demo
target: code
status: open
cycles: 1
---

# Findings

| ID | Severity | Status | Resolved by | Location | Problem | Recommended Fix | Source |
|----|----------|--------|-------------|----------|---------|-----------------|--------|
| F-1 | HIGH | resolved | ABCDEF0123456789ABCDEF0123456789ABCDEF01 | src/a.py:1 | [constitution:A1] upper-sha | fix | self |
""",
    )
    [cand] = sg.parse_review_findings_for_harvest(src)
    assert cand.resolved_by == "abcdef0123456789abcdef0123456789abcdef01"


# --------------------------------------------------------------------------- #
# Closed-vocab strictness still raises (matches parse_review_findings)        #
# --------------------------------------------------------------------------- #


def test_parse_review_findings_for_harvest_raises_on_bad_resolved_by(
    tmp_path: Path,
) -> None:
    src = _write(
        tmp_path / "REVIEW.code.md",
        """---
spec: 2026-05-12-demo
target: code
status: open
cycles: 1
---

# Findings

| ID | Severity | Status | Resolved by | Location | Problem | Recommended Fix | Source |
|----|----------|--------|-------------|----------|---------|-----------------|--------|
| F-1 | HIGH | resolved | bogus-marker | src/a.py:1 | [constitution:A1] bad | fix | self |
""",
    )
    with pytest.raises(sg.ShipGateError):
        sg.parse_review_findings_for_harvest(src)


def test_parse_review_findings_for_harvest_raises_on_bad_severity(
    tmp_path: Path,
) -> None:
    src = _write(
        tmp_path / "REVIEW.code.md",
        """---
spec: 2026-05-12-demo
target: code
status: open
cycles: 1
---

# Findings

| ID | Severity | Status | Resolved by | Location | Problem | Recommended Fix | Source |
|----|----------|--------|-------------|----------|---------|-----------------|--------|
| F-1 | High | resolved | 1a2b3c4d5e6f7890abcdef1234567890abcdef12 | src/a.py:1 | [constitution:A1] case-typo | fix | self |
""",
    )
    with pytest.raises(sg.ShipGateError):
        sg.parse_review_findings_for_harvest(src)


# --------------------------------------------------------------------------- #
# Legacy layout: no Resolved by column → harvest yields nothing               #
# --------------------------------------------------------------------------- #


def test_parse_review_findings_for_harvest_legacy_layout_no_resolved_by_column(
    tmp_path: Path,
) -> None:
    """Without a Resolved by column there is no SHA to harvest against."""
    src = _write(
        tmp_path / "REVIEW.code.md",
        """---
spec: 2026-05-12-demo
target: code
status: resolved
cycles: 2
---

# Findings

| ID | Severity | Status | Location | Problem | Recommended Fix | Source |
|----|----------|--------|----------|---------|-----------------|--------|
| F-1 | HIGH | resolved | src/a.py:1 | [constitution:A1] direct call | moved in commit deadbeef | heavy-subagent |
""",
    )
    assert sg.parse_review_findings_for_harvest(src) == []


# --------------------------------------------------------------------------- #
# No Findings heading → both parsers return empty                             #
# --------------------------------------------------------------------------- #


def test_parse_review_findings_for_harvest_no_findings_heading(tmp_path: Path) -> None:
    src = _write(
        tmp_path / "REVIEW.code.md",
        """---
spec: 2026-05-12-demo
target: code
status: open
cycles: 1
---

Nothing here.
""",
    )
    assert sg.parse_review_findings_for_harvest(src) == []
