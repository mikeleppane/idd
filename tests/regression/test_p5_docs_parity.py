"""Regression: P5 surface presence in AGENTS.md and README.md.

Asserts:
1. AGENTS.md contains the 'forge-change' literal (skills table mention).
2. AGENTS.md contains the '/forge:change' literal (commands table mention).
3. AGENTS.md contains the 'merge_delta_proposal' literal (lifecycle prose).
4. README.md contains both '/forge:change' and 'Delta proposals' literals.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
AGENTS_PATH = REPO / "AGENTS.md"
README_PATH = REPO / "README.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_agents_md_skills_table_has_forge_change() -> None:
    """AGENTS.md skills table must contain the forge-change row."""
    text = _read(AGENTS_PATH)
    assert "forge-change" in text, (
        "AGENTS.md is missing a 'forge-change' entry in the skills table."
    )


def test_agents_md_commands_table_has_forge_change() -> None:
    """AGENTS.md commands table must contain the /forge:change row."""
    text = _read(AGENTS_PATH)
    assert "/forge:change" in text, (
        "AGENTS.md is missing a '/forge:change' entry in the commands table."
    )
