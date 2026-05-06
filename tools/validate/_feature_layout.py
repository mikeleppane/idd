"""Shared feature-folder layout constants (M3 §5.3.6 D-HEALTH)."""

from __future__ import annotations

STATE_FILENAME = "state.json"
SPEC_FILENAME = "SPEC.md"
PLAN_FILENAME = "PLAN.md"
DECISIONS_FILENAME = "decisions.md"

TEMPLATED_FEATURE_FILES: frozenset[str] = frozenset(
    {
        "state.json",
        "SPEC.md",
        "PLAN.md",
        "UNDERSTANDING.md",
        "REVIEW.md",
        "REVIEW.plan.md",
        "REVIEW.code.md",
        "VERIFICATION.md",
        "decisions.md",
    }
)
