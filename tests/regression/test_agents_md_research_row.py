"""AGENTS.md surfaces research + cross-ai surface in user-facing prose.

Locks:
- A `forge-research` row exists in the skills table.
- The `forge-review` row mentions `--cross-ai` (and the paste-back flag).
- A `/forge:research` row exists in the commands table.
- A `templates/feature/RESEARCH.md` row exists in the templates table.
- No internal milestone/phase labels in the new prose.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
AGENTS = REPO / "AGENTS.md"

# Internal milestone shorthand we ban from user-facing prose. "finding" is a
# legitimate generic noun for review output items so it is NOT in this set;
# the rule targets bare M8/P0..P6/milestone references that leak the
# planning-doc shorthand into the user surface.
_FORBIDDEN_LABELS = ("M8", "P0", "P1", "P2", "P3", "P4", "P5", "P6", "milestone")


def _read() -> str:
    return AGENTS.read_text(encoding="utf-8")


def test_skills_table_has_forge_research_row() -> None:
    text = _read()
    pattern = re.compile(r"\|\s*`forge-research`\s*\|", re.MULTILINE)
    assert pattern.search(text), "AGENTS.md skills table is missing a `forge-research` row."


def test_forge_review_row_mentions_cross_ai() -> None:
    text = _read()
    review_row = next(
        (
            line
            for line in text.splitlines()
            if "`forge-review`" in line and "skills/forge-review" in line
        ),
        None,
    )
    assert review_row, "AGENTS.md skills table is missing the `forge-review` row entirely."
    assert "--cross-ai" in review_row, (
        "`forge-review` row description must mention `--cross-ai` so the cross-AI surface is discoverable."
    )


def test_commands_table_has_forge_research_row() -> None:
    text = _read()
    pattern = re.compile(r"\|\s*`/forge:research`\s*\|")
    assert pattern.search(text), "AGENTS.md commands table is missing a `/forge:research` row."


def test_commands_table_review_row_mentions_cross_ai_modes() -> None:
    text = _read()
    review_row = next(
        (
            line
            for line in text.splitlines()
            if "`/forge:review`" in line and "commands/review" in line
        ),
        None,
    )
    assert review_row, "AGENTS.md commands table is missing the `/forge:review` row."
    assert "--cross-ai" in review_row
    assert "--cross-ai-paste" in review_row
    assert "--auto" in review_row


def test_templates_table_lists_research_md() -> None:
    text = _read()
    pattern = re.compile(r"`templates/feature/RESEARCH\.md`")
    assert pattern.search(text), (
        "AGENTS.md templates table is missing the RESEARCH.md template row."
    )


def test_no_internal_phase_labels_in_research_or_cross_ai_rows() -> None:
    text = _read()
    relevant_rows = [
        line
        for line in text.splitlines()
        if any(
            token in line
            for token in ("forge-research", "/forge:research", "RESEARCH.md", "--cross-ai")
        )
    ]
    for row in relevant_rows:
        for forbidden in _FORBIDDEN_LABELS:
            assert forbidden not in row, (
                f"AGENTS.md row {row!r} contains forbidden internal label {forbidden!r}; "
                "user-facing docs describe behavior, not internal milestones."
            )
