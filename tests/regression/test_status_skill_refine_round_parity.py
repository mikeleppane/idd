"""Regression: forge-status surfaces refine round counter (deep-M-A5).

`/forge:status` is the user's read-only window onto the active feature.
Without surfacing ``state.json.routing.refine_attempts``, a user halfway
through the Socratic refine loop has no way to see "round X of 5" without
opening state.json directly. The skill prose must instruct appending
``(round X/5)`` to the status line when ``current_phase == "refine"``.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SKILL_PATH = REPO / "skills" / "forge-status" / "SKILL.md"
_BODY = SKILL_PATH.read_text(encoding="utf-8")


def test_status_skill_documents_refine_attempts_field() -> None:
    """The skill body must reference the canonical state.json field so
    future agents follow the lookup path rather than inventing one."""
    assert "refine_attempts" in _BODY, (
        "skills/forge-status/SKILL.md must reference state.json.routing.refine_attempts "
        "as the source for the refine round counter (deep-M-A5)"
    )


def test_status_skill_documents_round_render_format() -> None:
    """The skill body must commit to the ``(round X/5)`` literal so the
    rendered status line is consistent across invocations.
    """
    assert "(round" in _BODY, (
        "skills/forge-status/SKILL.md must lock the literal ``(round`` prefix "
        "for the refine round counter rendering"
    )
    assert "/5" in _BODY, (
        "skills/forge-status/SKILL.md must spell the cap as ``X/5`` so the "
        "5-round Socratic cap is visible in the status line"
    )
