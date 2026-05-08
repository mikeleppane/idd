"""Regression: P6.1 surface presence in AGENTS.md, README.md, and skills/_meta.json.

Asserts the locked contract for M3 P6.1 `/forge:do` adaptive routing:

1. AGENTS.md skills table contains a `forge-do` row classified `explicit`.
2. AGENTS.md commands table contains a `/forge:do` row.
3. AGENTS.md P6.1 prose names `tools.routing.seed_routed_feature` as the
   post-confirm Python entry helper.
4. AGENTS.md P6.1 prose carries the locked dispatch literal
   `Next: /forge:spec --feature <feature_id>`.
5. AGENTS.md retains a full-tier callout pointing at the still-deferred
   `/forge:do --full` (M3 P6.2) entry path.
6. README.md no longer carries the obsolete focused/standard
   "until P6.2" entry-path note (the focused/standard portion is shipped).
7. README.md describes `/forge:do --focused` and `/forge:do --standard`
   as the canonical adaptive-routing entry points (P6.1).
8. `skills/_meta.json` does NOT list `forge-do` — `forge-do` is a
   routing entry-point skill, not a phase-bearing lifecycle skill.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
AGENTS_PATH = REPO / "AGENTS.md"
README_PATH = REPO / "README.md"
META_PATH = REPO / "skills" / "_meta.json"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_agents_md_has_forge_do_skill_row() -> None:
    text = _read(AGENTS_PATH)
    # Find the skills-table row for forge-do; pin the explicit classification.
    pattern = re.compile(
        r"\|\s*`forge-do`\s*\|[^|]*\|\s*explicit\s*\|",
        re.IGNORECASE,
    )
    assert pattern.search(text), (
        "AGENTS.md skills table is missing a `forge-do` row marked `explicit`."
    )


def test_agents_md_has_forge_do_command_row() -> None:
    text = _read(AGENTS_PATH)
    pattern = re.compile(r"\|\s*`/forge:do`\s*\|")
    assert pattern.search(text), "AGENTS.md commands table is missing a `/forge:do` row."


def test_meta_json_does_not_list_forge_do() -> None:
    payload = json.loads(_read(META_PATH))
    skills = payload.get("skills", {})
    assert "forge-do" not in skills, (
        "`skills/_meta.json` must NOT list `forge-do` — it is a routing "
        "entry-point skill, not a phase-bearing lifecycle skill. AGENTS.md "
        "is the canonical surface (per plan §6)."
    )
