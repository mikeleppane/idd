"""Regression: forge-refine skill + /forge:refine command shape parity.

Asserts:
1. skills/forge-refine/SKILL.md exists.
2. SKILL.md frontmatter contains name, description, and model: sonnet lines.
3. SKILL.md body references the state helpers
   (``tools.state.increment_refine_attempts`` and
   ``tools.state.record_refined_idea``).
4. SKILL.md body documents the phase transition (``complete_phase``,
   ``start_phase``, ``refine``, ``spec``).
5. SKILL.md body documents the 5-round cap.
6. SKILL.md body documents deviation handling (``decisions.md`` and
   ``deviations``).
7. SKILL.md body lists ``routing.idea`` as the input source.
8. SKILL.md body points to ``/forge:spec`` as the next phase.
9. commands/refine.md exists.
10. commands/refine.md frontmatter contains an ``argument-hint:`` pointing at
    ``--feature``.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SKILL_PATH = REPO / "skills" / "forge-refine" / "SKILL.md"
COMMAND_PATH = REPO / "commands" / "refine.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Skill file exists
# ---------------------------------------------------------------------------


def test_forge_refine_skill_exists() -> None:
    assert SKILL_PATH.exists(), f"Expected {SKILL_PATH} to exist"


# ---------------------------------------------------------------------------
# 2. Skill frontmatter tokens
# ---------------------------------------------------------------------------


def test_refine_skill_frontmatter_has_name_description_model() -> None:
    text = _read(SKILL_PATH)
    assert "name: forge-refine" in text, "SKILL.md frontmatter must contain 'name: forge-refine'"
    assert "description:" in text, "SKILL.md frontmatter must contain 'description:' line"
    assert "model: sonnet" in text, "SKILL.md frontmatter must contain 'model: sonnet'"


# ---------------------------------------------------------------------------
# 3. Body references state helpers
# ---------------------------------------------------------------------------


def test_refine_skill_references_state_helpers() -> None:
    text = _read(SKILL_PATH)
    assert "tools.state.increment_refine_attempts" in text, (
        "SKILL.md must reference tools.state.increment_refine_attempts"
    )
    assert "tools.state.record_refined_idea" in text, (
        "SKILL.md must reference tools.state.record_refined_idea"
    )


# ---------------------------------------------------------------------------
# 4. Body documents phase transition
# ---------------------------------------------------------------------------


def test_refine_skill_documents_phase_transition() -> None:
    text = _read(SKILL_PATH)
    assert "complete_phase" in text, "SKILL.md must reference complete_phase"
    assert "start_phase" in text, "SKILL.md must reference start_phase"
    assert "refine" in text, "SKILL.md must mention 'refine' phase"
    assert "spec" in text, "SKILL.md must mention 'spec' phase"


# ---------------------------------------------------------------------------
# 5. Body documents the 5-round cap
# ---------------------------------------------------------------------------


def test_refine_skill_documents_round_cap() -> None:
    text = _read(SKILL_PATH)
    assert re.search(r"5 rounds", text, re.IGNORECASE), (
        "SKILL.md must mention '5 rounds' (the Socratic loop cap)"
    )


# ---------------------------------------------------------------------------
# 6. Body documents deviation handling
# ---------------------------------------------------------------------------


def test_refine_skill_documents_deviation_handling() -> None:
    text = _read(SKILL_PATH)
    assert "decisions.md" in text, "SKILL.md must reference decisions.md for round-cap deviation"
    assert "deviations" in text, "SKILL.md must reference state.json.deviations append on round-cap"


# ---------------------------------------------------------------------------
# 7. Body lists routing.idea as input source
# ---------------------------------------------------------------------------


def test_refine_skill_lists_routing_idea_input() -> None:
    text = _read(SKILL_PATH)
    assert "routing.idea" in text, (
        "SKILL.md must document state.json.routing.idea as the seeded input"
    )


# ---------------------------------------------------------------------------
# 8. Body points to /forge:spec
# ---------------------------------------------------------------------------


def test_refine_skill_points_to_next_command() -> None:
    text = _read(SKILL_PATH)
    assert "/forge:spec" in text, "SKILL.md must point to /forge:spec as the next-phase command"


# ---------------------------------------------------------------------------
# 9. Command file exists
# ---------------------------------------------------------------------------


def test_refine_command_exists() -> None:
    assert COMMAND_PATH.exists(), f"Expected {COMMAND_PATH} to exist"


# ---------------------------------------------------------------------------
# 10. Command frontmatter contains argument-hint with --feature
# ---------------------------------------------------------------------------


def test_refine_command_frontmatter_has_argument_hint() -> None:
    text = _read(COMMAND_PATH)
    assert "argument-hint:" in text, (
        "commands/refine.md frontmatter must contain 'argument-hint:' line"
    )
    assert "--feature" in text, (
        "commands/refine.md frontmatter argument-hint must mention '--feature'"
    )
