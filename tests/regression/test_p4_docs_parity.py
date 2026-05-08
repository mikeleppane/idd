"""Regression: P4 surface presence in AGENTS.md and README.md.

Asserts:
1. AGENTS.md skills table contains forge-refine and forge-domain rows.
2. AGENTS.md commands table contains /forge:refine and /forge:domain rows.
3. AGENTS.md lifecycle prose mentions the M3 P4 footprint (increment_refine_attempts).
4. README.md contains the Pre-spec phases callout with both new commands.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
AGENTS_PATH = REPO / "AGENTS.md"
README_PATH = REPO / "README.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_agents_md_skills_table_has_forge_refine_and_forge_domain() -> None:
    text = _read(AGENTS_PATH)
    assert "forge-refine" in text, "AGENTS.md is missing 'forge-refine' skills table row."
    assert "forge-domain" in text, "AGENTS.md is missing 'forge-domain' skills table row."


def test_agents_md_commands_table_has_refine_and_domain() -> None:
    text = _read(AGENTS_PATH)
    assert "/forge:refine" in text, "AGENTS.md is missing '/forge:refine' commands table row."
    assert "/forge:domain" in text, "AGENTS.md is missing '/forge:domain' commands table row."


def test_agents_md_lifecycle_prose_has_p4_footprint() -> None:
    text = _read(AGENTS_PATH)
    assert "increment_refine_attempts" in text, (
        "AGENTS.md lifecycle prose is missing 'increment_refine_attempts' (M3 P4 footprint)."
    )
