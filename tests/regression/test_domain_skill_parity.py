"""Regression: forge-domain skill + /forge:domain command shape parity.

Asserts:
1. skills/forge-domain/SKILL.md exists.
2. SKILL.md frontmatter contains name, description, and model: sonnet lines.
3. SKILL.md body references state helpers (``tools.state.complete_phase`` and
   ``tools.state.start_phase``).
4. SKILL.md body documents the phase transition (``current_phase``,
   ``domain``, ``scenarios``).
5. SKILL.md body documents the 4-8 glossary size band.
6. SKILL.md body mentions Mermaid as optional.
7. SKILL.md body documents the M3 out-of-scope callout
   (``bounded-context``).
8. SKILL.md body documents deviation handling (``decisions.md`` and
   ``deviations``).
9. SKILL.md body points to ``/forge:scenarios`` as the next-phase command.
10. commands/domain.md exists with ``argument-hint:`` containing
    ``--feature``.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SKILL_PATH = REPO / "skills" / "forge-domain" / "SKILL.md"
COMMAND_PATH = REPO / "commands" / "domain.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Skill file exists
# ---------------------------------------------------------------------------


def test_forge_domain_skill_exists() -> None:
    assert SKILL_PATH.exists(), f"Expected {SKILL_PATH} to exist"


# ---------------------------------------------------------------------------
# 2. Skill frontmatter tokens
# ---------------------------------------------------------------------------


def test_domain_skill_frontmatter_has_name_description_model() -> None:
    text = _read(SKILL_PATH)
    assert "name: forge-domain" in text, "SKILL.md frontmatter must contain 'name: forge-domain'"
    assert "description:" in text, "SKILL.md frontmatter must contain 'description:' line"
    assert "model: sonnet" in text, "SKILL.md frontmatter must contain 'model: sonnet'"


# ---------------------------------------------------------------------------
# 3. Body references state helpers
# ---------------------------------------------------------------------------


def test_domain_skill_references_state_helpers() -> None:
    text = _read(SKILL_PATH)
    assert "tools.state.complete_phase" in text, (
        "SKILL.md must reference tools.state.complete_phase"
    )
    assert "tools.state.start_phase" in text, "SKILL.md must reference tools.state.start_phase"


# ---------------------------------------------------------------------------
# 4. Body documents phase transition
# ---------------------------------------------------------------------------


def test_domain_skill_documents_phase_transition() -> None:
    text = _read(SKILL_PATH)
    assert "current_phase" in text, "SKILL.md must reference current_phase"
    assert "domain" in text, "SKILL.md must mention 'domain' phase"
    assert "scenarios" in text, "SKILL.md must mention 'scenarios' phase"


# ---------------------------------------------------------------------------
# 5. Body documents the 4-8 glossary size band
# ---------------------------------------------------------------------------


def test_domain_skill_documents_glossary_size_band() -> None:
    text = _read(SKILL_PATH)
    band_en_dash = f"4{chr(0x2013)}8"  # 4 + EN DASH (U+2013) + 8
    assert (band_en_dash in text) or ("4-8" in text), (
        "SKILL.md must document the 4-8 (en- or hyphen-dash) glossary entry size band"
    )


# ---------------------------------------------------------------------------
# 6. Body documents Mermaid optionality
# ---------------------------------------------------------------------------


def test_domain_skill_documents_mermaid_optionality() -> None:
    text = _read(SKILL_PATH)
    assert "Mermaid" in text, "SKILL.md must mention Mermaid sketches"
    assert "optional" in text.lower(), "SKILL.md must describe Mermaid as optional"


# ---------------------------------------------------------------------------
# 7. Body documents the M3 out-of-scope callout
# ---------------------------------------------------------------------------


def test_domain_skill_documents_m3_scope_callout() -> None:
    text = _read(SKILL_PATH)
    assert "bounded-context" in text, (
        "SKILL.md must include the M3 out-of-scope 'bounded-context' callout"
    )


# ---------------------------------------------------------------------------
# 8. Body documents deviation handling
# ---------------------------------------------------------------------------


def test_domain_skill_documents_deviation_handling() -> None:
    text = _read(SKILL_PATH)
    assert "decisions.md" in text, (
        "SKILL.md must reference decisions.md for unresolvable-term deviation"
    )
    assert "deviations" in text, (
        "SKILL.md must reference state.json.deviations append on unresolvable-term auto-mode"
    )


# ---------------------------------------------------------------------------
# 9. Body points to /forge:scenarios
# ---------------------------------------------------------------------------


def test_domain_skill_points_to_next_command() -> None:
    text = _read(SKILL_PATH)
    assert "/forge:scenarios" in text, (
        "SKILL.md must point to /forge:scenarios as the next-phase command"
    )


# ---------------------------------------------------------------------------
# 10. Command exists with argument-hint
# ---------------------------------------------------------------------------


def test_domain_command_exists_with_argument_hint() -> None:
    assert COMMAND_PATH.exists(), f"Expected {COMMAND_PATH} to exist"
    text = _read(COMMAND_PATH)
    assert "argument-hint:" in text, (
        "commands/domain.md frontmatter must contain 'argument-hint:' line"
    )
    assert "--feature" in text, (
        "commands/domain.md frontmatter argument-hint must mention '--feature'"
    )


# ---------------------------------------------------------------------------
# 11. Body documents fence-aware section detection (T11 H1 / deep-H3 lesson)
# ---------------------------------------------------------------------------


def test_domain_skill_documents_full_tier_guard() -> None:
    text = _read(SKILL_PATH)
    assert "full-tier only" in text or "full tier only" in text, (
        "SKILL.md must guard against running on focused/standard tiers"
    )
    assert 'tier == "full"' in text or "tier is 'full'" in text, (
        "SKILL.md must spell out the explicit tier == 'full' check"
    )


def test_domain_skill_documents_fence_aware_section_scan() -> None:
    text = _read(SKILL_PATH)
    assert "fence-aware" in text.lower() or "mask" in text.lower(), (
        "SKILL.md must instruct fence-aware section scanning when locating "
        "# Intent / # Scenarios / # Domain headers (per P5 T11 H1 lesson)"
    )
    assert "fenced" in text.lower(), (
        "SKILL.md must explicitly mention fenced code blocks in the H1-shadowing rule"
    )
