"""Verify the ``lessons`` validator target is documented in every public surface.

The ``--target lessons`` route exists in ``tools/validate/cli.py``; without
matching prose in ``AGENTS.md``, ``commands/validate.md``, and
``skills/forge-validate/SKILL.md`` the target is invisible to non-Claude
discovery (Cursor / Aider / Codex) and to the slash-command recipe. These
parity checks pin the three doc surfaces in lockstep with the CLI.
"""

from __future__ import annotations

from pathlib import Path

import pytest

DOC_FILES = [
    "AGENTS.md",
    "commands/validate.md",
    "skills/forge-validate/SKILL.md",
]


@pytest.mark.parametrize("doc_path", DOC_FILES)
def test_doc_lists_lessons_validate_target(doc_path: str) -> None:
    text = Path(doc_path).read_text(encoding="utf-8")
    assert "lessons" in text, (
        f"{doc_path} must list the lessons target — the validate CLI accepts "
        "--target lessons and the all-target fans out to validate_lessons"
    )


def test_validate_command_lists_lessons_in_fanout() -> None:
    """``commands/validate.md`` must describe lessons inside the ``all`` recipe."""
    text = Path("commands/validate.md").read_text(encoding="utf-8")
    # Anchor the assertion to the all-target subsection so a stray reference
    # elsewhere does not satisfy the check.
    assert "validate_lessons" in text, (
        "commands/validate.md must mention validate_lessons inside the "
        "--target all fan-out so users know lessons participates in repo-wide "
        "validation"
    )


def test_forge_validate_skill_lists_lessons_target() -> None:
    """``skills/forge-validate/SKILL.md`` must list the lessons target by name."""
    text = Path("skills/forge-validate/SKILL.md").read_text(encoding="utf-8")
    assert "`lessons`" in text, "forge-validate SKILL.md must list `lessons` as a repo-wide target"
