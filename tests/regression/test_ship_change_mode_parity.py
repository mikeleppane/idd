"""Regression: forge-ship skill + /forge:ship command --change mode parity.

Asserts that skills/forge-ship/SKILL.md and commands/ship.md correctly
document the --change delta-merge mode introduced in M3-P5 (T8).

Tests:
1. test_ship_skill_documents_change_mode_branch
   SKILL.md body contains the literal token ``--change`` AND ``change_id``.
2. test_ship_skill_references_merge_delta_proposal
   SKILL.md body contains ``tools.archive.merge_delta_proposal``.
3. test_ship_skill_references_mark_change_merged_hook
   SKILL.md body contains ``_mark_change_merged_hook``.
4. test_ship_skill_documents_archiveerror_surface
   SKILL.md body contains ``ArchiveError`` (anchors rollback-error story).
5. test_ship_skill_documents_no_constitution_gate_in_change_mode
   SKILL.md explicitly states the Constitution gate does NOT apply in
   --change mode.  Acceptable substrings checked: "does NOT apply".
6. test_ship_command_documents_change_arg
   commands/ship.md mentions ``--change`` AND ``merge_delta_proposal``
   AND ``_mark_change_merged_hook``.
7. test_ship_skill_does_not_couple_to_feature_state_in_change_mode
   SKILL.md states the change-mode flow does NOT advance feature state.json.
   Acceptable substring: "no feature state.json" (case-insensitive).
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SKILL_PATH = REPO / "skills" / "forge-ship" / "SKILL.md"
COMMAND_PATH = REPO / "commands" / "ship.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. --change flag + change_id placeholder both present
# ---------------------------------------------------------------------------


def test_ship_skill_documents_change_mode_branch() -> None:
    """SKILL.md body must mention the --change flag and the change_id placeholder."""
    text = _read(SKILL_PATH)
    assert "--change" in text, "forge-ship/SKILL.md must contain the '--change' flag token"
    assert "change_id" in text, "forge-ship/SKILL.md must contain 'change_id' placeholder"


# ---------------------------------------------------------------------------
# 2. merge_delta_proposal reference
# ---------------------------------------------------------------------------


def test_ship_skill_references_merge_delta_proposal() -> None:
    """SKILL.md body must reference tools.archive.merge_delta_proposal."""
    assert "tools.archive.merge_delta_proposal" in _read(SKILL_PATH), (
        "forge-ship/SKILL.md must reference 'tools.archive.merge_delta_proposal'"
    )


# ---------------------------------------------------------------------------
# 3. _mark_change_merged_hook reference
# ---------------------------------------------------------------------------


def test_ship_skill_references_mark_change_merged_hook() -> None:
    """SKILL.md body must reference _mark_change_merged_hook."""
    assert "_mark_change_merged_hook" in _read(SKILL_PATH), (
        "forge-ship/SKILL.md must reference '_mark_change_merged_hook'"
    )


# ---------------------------------------------------------------------------
# 4. ArchiveError surface documented
# ---------------------------------------------------------------------------


def test_ship_skill_documents_archiveerror_surface() -> None:
    """SKILL.md body must contain 'ArchiveError' to anchor rollback-error story."""
    assert "ArchiveError" in _read(SKILL_PATH), (
        "forge-ship/SKILL.md must document 'ArchiveError' handling for --change mode"
    )


# ---------------------------------------------------------------------------
# 5. Constitution gate explicitly excluded from --change mode
# ---------------------------------------------------------------------------


def test_ship_skill_documents_no_constitution_gate_in_change_mode() -> None:
    """SKILL.md must state the Constitution gate does NOT apply in --change mode.

    Checked substring: 'does NOT apply' (case-sensitive; matches the canonical
    phrasing inserted by T8 into the mode-selector branch).
    """
    assert "does NOT apply" in _read(SKILL_PATH), (
        "forge-ship/SKILL.md must state the Constitution gate 'does NOT apply' "
        "in --change mode (per Open Scoping #12)"
    )


# ---------------------------------------------------------------------------
# 6. commands/ship.md documents --change arg with both tool references
# ---------------------------------------------------------------------------


def test_ship_command_documents_change_arg() -> None:
    """commands/ship.md must mention --change, merge_delta_proposal, and _mark_change_merged_hook."""
    text = _read(COMMAND_PATH)
    assert "--change" in text, "commands/ship.md must document the '--change' argument"
    assert "merge_delta_proposal" in text, "commands/ship.md must reference 'merge_delta_proposal'"
    assert "_mark_change_merged_hook" in text, (
        "commands/ship.md must reference '_mark_change_merged_hook'"
    )


# ---------------------------------------------------------------------------
# 7. --change mode does not advance feature state.json
# ---------------------------------------------------------------------------


def test_ship_skill_does_not_couple_to_feature_state_in_change_mode() -> None:
    """SKILL.md must state --change mode does not advance feature state.json.

    Checked substring: 'feature state.json' (case-insensitive).
    The T8 skill body uses the phrasing "Do NOT advance any feature state.json"
    which contains this substring when lowercased.
    """
    assert "feature state.json" in _read(SKILL_PATH).lower(), (
        "forge-ship/SKILL.md must document that --change mode does not advance "
        "feature state.json (e.g. 'Do NOT advance any feature state.json')"
    )
