"""Regression: forge-spec full-tier narrative parity.

Locks the M3 P4 T4 narrative additions to ``skills/forge-spec/SKILL.md``:
1. Step 6 Intent draft consumes ``state.json.refined_idea`` verbatim when set
   by ``/forge:refine``.
2. Step 6 Domain section may stay as the single-line placeholder
   ``_TBD: filled by /forge:domain_`` for full-tier features (focused and
   standard remain unchanged).
3. Step 7 self-review gate skips the "every Domain term appears in Intent /
   Scope / Scenarios" check when full-tier Domain is the placeholder.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SKILL_PATH = REPO / "skills" / "forge-spec" / "SKILL.md"
_BODY = SKILL_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Intent draft consumes refined_idea verbatim
# ---------------------------------------------------------------------------


def test_spec_skill_documents_refined_idea_consumption() -> None:
    assert "state.json.refined_idea" in _BODY, (
        "SKILL.md must reference state.json.refined_idea as the Intent draft seed"
    )
    assert "verbatim" in _BODY, "SKILL.md must instruct lifting the refined_idea paragraph verbatim"


# ---------------------------------------------------------------------------
# 2. Intent draft references /forge:refine as the producer
# ---------------------------------------------------------------------------


def test_spec_skill_references_forge_refine_for_intent_draft() -> None:
    assert "refined_idea" in _BODY, (
        "SKILL.md must mention refined_idea when documenting Intent seeding"
    )
    assert "/forge:refine" in _BODY, (
        "SKILL.md must point to /forge:refine as the producer of refined_idea"
    )


# ---------------------------------------------------------------------------
# 3. Domain placeholder string is documented exactly
# ---------------------------------------------------------------------------


def test_spec_skill_documents_full_tier_domain_placeholder() -> None:
    assert "_TBD: filled by /forge:domain_" in _BODY, (
        "SKILL.md must document the exact full-tier Domain placeholder string "
        "'_TBD: filled by /forge:domain_'"
    )


# ---------------------------------------------------------------------------
# 4. Full-tier exception label is present (case-sensitive)
# ---------------------------------------------------------------------------


def test_spec_skill_documents_full_tier_domain_exception_label() -> None:
    assert "Full-tier exception:" in _BODY, (
        "SKILL.md Domain bullet must carry the 'Full-tier exception:' label"
    )


# ---------------------------------------------------------------------------
# 5. Self-review gate skips the Domain-term check for the placeholder
# ---------------------------------------------------------------------------


def test_spec_skill_self_review_skips_domain_term_check_for_placeholder() -> None:
    assert "Full-tier Domain-placeholder allowance" in _BODY, (
        "SKILL.md self-review gate must name the 'Full-tier Domain-placeholder allowance'"
    )
    assert "skipped" in _BODY, (
        "SKILL.md self-review gate must state the Domain-term check is skipped "
        "for the full-tier placeholder"
    )


# ---------------------------------------------------------------------------
# 6. Focused / standard tier contract preserved verbatim
# ---------------------------------------------------------------------------


def test_spec_skill_focused_standard_domain_unchanged() -> None:
    assert "For focused and standard tiers" in _BODY, (
        "SKILL.md Domain bullet must preserve the 'For focused and standard tiers' "
        "callout so the existing contract is unambiguous"
    )
    assert "existing behavior" in _BODY, (
        "SKILL.md Domain bullet must label the focused/standard rule as 'existing behavior'"
    )


# ---------------------------------------------------------------------------
# 7. Placeholder comparator locked (deep-M3): regex form + strip rule
# ---------------------------------------------------------------------------


def test_spec_skill_locks_placeholder_comparator() -> None:
    assert "^_TBD: filled by /forge:domain_?$" in _BODY, (
        "SKILL.md must lock the placeholder comparator regex so partial fills "
        "and whitespace drift are rejected (T11 deep-M3)"
    )
    assert "strip leading and trailing whitespace" in _BODY, (
        "SKILL.md must specify whitespace stripping as part of the comparator"
    )
    assert "Backslash-escaped" in _BODY, (
        "SKILL.md must call out that backslash-escaped placeholder variants do "
        "not match (deep-M3 drift mode)"
    )
