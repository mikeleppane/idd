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


def test_agents_md_p6_1_prose_mentions_seed_routed_feature() -> None:
    text = _read(AGENTS_PATH)
    assert "tools.routing.seed_routed_feature" in text, (
        "AGENTS.md is missing `tools.routing.seed_routed_feature` reference in the P6.1 prose."
    )
    # The mention must sit inside (or near) a P6.1 paragraph — disambiguate
    # against any future references by finding the closest pair of
    # ("P6.1", "seed_routed_feature") indices.
    assert "P6.1" in text, "AGENTS.md is missing the P6.1 anchor."
    seed_indices = [m.start() for m in re.finditer(r"tools\.routing\.seed_routed_feature", text)]
    p6_1_indices = [m.start() for m in re.finditer(r"P6\.1", text)]
    closest = min(abs(s - p) for s in seed_indices for p in p6_1_indices)
    assert closest < 1000, (
        "AGENTS.md must have a `tools.routing.seed_routed_feature` mention "
        "within ~1000 chars of a `P6.1` anchor (i.e. the same paragraph)."
    )


def test_agents_md_p6_1_prose_has_dispatch_literal() -> None:
    text = _read(AGENTS_PATH)
    assert "Next: /forge:spec --feature <feature_id>" in text, (
        "AGENTS.md P6.1 prose must carry the locked dispatch literal "
        "`Next: /forge:spec --feature <feature_id>`."
    )


def test_agents_md_full_tier_callout_retained_scoped_to_full() -> None:
    text = _read(AGENTS_PATH)
    # Full-tier manual-bootstrap callout still lives until P6.2 ships.
    assert "P6.2" in text, "AGENTS.md must still reference the deferred P6.2 milestone."
    # The full-tier callout phrase must remain in some form.
    assert "Full-tier" in text or "full-tier" in text, (
        "AGENTS.md must retain a full-tier-scoped callout for the P6.2 deferral."
    )


def test_readme_p6_1_callout_replaces_focused_standard_until_p6_2() -> None:
    text = _read(README_PATH)
    # The old M3 P4 phrasing claimed focused/standard adaptive routing
    # was deferred until P6.2; that claim is obsolete in P6.1.
    forbidden = "focused/standard adaptive routing"
    if forbidden in text:
        # If the substring still appears, it must be in a context that
        # refers to it as already-shipped (not deferred).
        idx = text.find(forbidden)
        window = text[max(0, idx - 80) : idx + 200]
        assert "until P6.2" not in window and "lands in M3 P6.2" not in window, (
            "README.md still claims focused/standard adaptive routing is "
            "deferred to P6.2; P6.1 ships those tiers."
        )
    # `/forge:do --full` callouts MAY still mention P6.2 — that is the
    # remaining deferral.


def test_readme_p6_1_mentions_forge_do_focused_and_standard() -> None:
    text = _read(README_PATH)
    assert "/forge:do --focused" in text or "`/forge:do --focused`" in text, (
        "README.md must describe `/forge:do --focused` as the focused-tier "
        "adaptive-routing entry point (P6.1)."
    )
    assert "/forge:do --standard" in text or "`/forge:do --standard`" in text, (
        "README.md must describe `/forge:do --standard` as the standard-tier "
        "adaptive-routing entry point (P6.1)."
    )


def test_meta_json_does_not_list_forge_do() -> None:
    payload = json.loads(_read(META_PATH))
    skills = payload.get("skills", {})
    assert "forge-do" not in skills, (
        "`skills/_meta.json` must NOT list `forge-do` — it is a routing "
        "entry-point skill, not a phase-bearing lifecycle skill. AGENTS.md "
        "is the canonical surface (per plan §6)."
    )
