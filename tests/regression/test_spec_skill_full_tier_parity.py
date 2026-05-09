"""Regression: forge-spec full-tier narrative parity.

Locks the full-tier narrative additions to ``skills/forge-spec/SKILL.md``:
1. Step 6 Intent draft consumes ``state.json.refined_idea`` verbatim when set
   by ``/forge:refine``.
2. Step 6 Domain section may stay as the single-line placeholder
   ``_TBD: filled by /forge:domain_`` for full-tier features (focused and
   standard remain unchanged).
3. Step 7 self-review gate skips the "every Domain term appears in Intent /
   Scope / Scenarios" check when full-tier Domain is the placeholder.
"""

from __future__ import annotations

import re
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
    # M6 L10: trailing underscore is now MANDATORY (was optional via `_?`).
    assert "^_TBD: filled by /forge:domain_$" in _BODY, (
        "SKILL.md must lock the placeholder comparator regex with mandatory "
        "trailing underscore so partial fills and the unrendered no-underscore "
        "variant are rejected (M6 L10)"
    )
    assert "strip leading and trailing whitespace" in _BODY, (
        "SKILL.md must specify whitespace stripping as part of the comparator"
    )
    assert "Backslash-escaped" in _BODY, (
        "SKILL.md must call out that backslash-escaped placeholder variants do "
        "not match (deep-M3 drift mode)"
    )


# ---------------------------------------------------------------------------
# 8. next_phase resolution after spec is per-tier deterministic, not free-form
# ---------------------------------------------------------------------------


def test_spec_skill_documents_per_tier_next_phase_resolution() -> None:
    """`next_phase` after spec must be derived from `state.json.tier`, not from a
    free-form user request. Otherwise the full-tier `# Domain` placeholder can
    leak past spec exit when the user requests anything other than `domain`.
    """
    assert "tier" in _BODY and "next_phase" in _BODY, (
        "SKILL.md Step 8 must mention `tier` and `next_phase` together"
    )

    expectations = (
        ("focused", "execute"),
        ("standard", "scenarios"),
        ("full", "domain"),
    )
    for tier, phase in expectations:
        assert tier in _BODY, f"SKILL.md must spell out the `{tier}` tier mapping"
        assert phase in _BODY, f"SKILL.md must name `{phase}` as the next phase for some tier"

    # The vague pre-fix wording must be gone — a tier-blind "first phase the user
    # requested" reading is exactly what allowed the placeholder to leak.
    assert "first phase the user requested" not in _BODY, (
        "SKILL.md must not pick `next_phase` from a free-form user request — "
        "the tier alone determines it"
    )


def test_spec_skill_full_tier_next_phase_is_domain() -> None:
    """The full-tier route MUST advance to the dedicated /forge:domain phase so
    the `_TBD: filled by /forge:domain_` placeholder is populated before spec
    is consumed downstream. Phrased in prose ("when the feature's tier is
    `full`") to satisfy the `test_scan_is_not_tier_gated` regression that
    forbids the literal `tier == "full"` substring in this skill body.
    """
    assert "tier is `full`" in _BODY, (
        "SKILL.md must state that when the feature's tier is `full`, next_phase "
        'resolves to `domain` (prose form to avoid the forbidden `tier == "full"` '
        "substring guarded by test_scan_is_not_tier_gated)"
    )
    assert '"domain"' in _BODY, (
        'SKILL.md Step 8 must literally name `"domain"` as the resolved next_phase for full tier'
    )


# ---------------------------------------------------------------------------
# 9. Executable placeholder-regex tests (deep-M-A4)
# ---------------------------------------------------------------------------
#
# The SKILL.md prose locks the comparator regex
# ``^_TBD: filled by /forge:domain_?$`` (test_spec_skill_locks_placeholder_comparator).
# These tests run that regex against the documented inputs so the comparator
# is empirically protected, not just textually mentioned.

_DOMAIN_PLACEHOLDER_RE = re.compile(r"^_TBD: filled by /forge:domain_$")


def test_full_tier_domain_placeholder_canonical_form_matches() -> None:
    body = "_TBD: filled by /forge:domain_"
    assert _DOMAIN_PLACEHOLDER_RE.match(body) is not None


def test_full_tier_domain_placeholder_no_trailing_underscore_does_not_match() -> None:
    """M6 L10: trailing underscore is now MANDATORY. The unrendered
    no-underscore variant must NOT match — previously the comparator
    silently accepted both forms, masking partial fills.
    """
    body = "_TBD: filled by /forge:domain"
    assert _DOMAIN_PLACEHOLDER_RE.match(body) is None


def test_full_tier_domain_placeholder_backslash_escaped_does_not_match() -> None:
    body = "\\_TBD: filled by /forge:domain\\_"
    assert _DOMAIN_PLACEHOLDER_RE.match(body) is None


def test_full_tier_domain_placeholder_with_leading_trailing_whitespace_matches_after_strip() -> (
    None
):
    body = "   _TBD: filled by /forge:domain_  \n"
    assert _DOMAIN_PLACEHOLDER_RE.match(body.strip()) is not None
