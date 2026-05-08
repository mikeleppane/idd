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

# Files that may appear in a never-advanced orphan feature folder.
# Used by both cleanup_orphan_feature (tools/archive.py) and the health
# orphan-detection check (tools/validate/health.py) so the two predicates stay
# in sync (Reviewer-2 finding, M3 P5 T3).
_ORPHAN_FEATURE_FILES: frozenset[str] = frozenset(
    {
        "state.json",
        "SPEC.md",
        "decisions.md",
    }
)

# Phases that mark a never-advanced seed feature.  Shared between
# tools/archive.py::_orphan_conditions_met and
# tools/validate/health.py::_check_feature_payload so a future change to one
# predicate cannot silently desync the other (M3 P6.1 T7 finding p6-1-M1).
# Includes BOTH the original P5 refine-tier seed AND the P6.1 focused/standard
# /forge:do pre-seed.
_ORPHAN_SEED_PHASES: frozenset[str] = frozenset({"refine", "spec"})
