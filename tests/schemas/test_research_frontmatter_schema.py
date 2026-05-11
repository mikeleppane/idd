"""Tests for ``schemas/research-frontmatter.schema.json``.

Locks the RESEARCH.md frontmatter shape so the validator the future research
phase wires in does not need to bump the schema on day one. Each case targets
one rule from the schema:

* Required quartet ``spec`` / ``status`` / ``tier`` / ``research_grounding``
  — missing any of them is rejected.
* ``research_grounding`` is a closed enum (no ad-hoc grounding modes).
* ``spec`` matches the canonical feature-id regex shared with state.schema —
  no trailing hyphen, no double hyphen, no upper-case slug. RESEARCH.md must
  not drift from the per-feature state file's ``feature_id``.
* ``tier: focused`` validates at the schema layer even though focused refuses
  research at runtime — the frontmatter shape stays open for future use.
* ``parallel_used: true`` is accepted (optional, defaulted false in schema).
* ``additionalProperties: false`` rejects unknown top-level keys so a typo
  like ``grounding:`` does not silently shadow ``research_grounding:``.
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema


def _validator_for(schemas_dir: Path) -> jsonschema.Draft202012Validator:
    schema_path = schemas_dir / "research-frontmatter.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    return jsonschema.Draft202012Validator(
        schema,
        format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER,
    )


def _minimal_valid() -> dict[str, object]:
    """Smallest frontmatter that satisfies every ``required`` field."""
    return {
        "spec": "2026-05-09-add-research-validator",
        "status": "in_progress",
        "tier": "standard",
        "research_grounding": "full",
    }


def test_minimal_valid_frontmatter_validates(schemas_dir: Path) -> None:
    """Case (a): full minimal valid frontmatter passes."""
    validator = _validator_for(schemas_dir)
    errors = list(validator.iter_errors(_minimal_valid()))
    assert errors == [], f"minimal valid frontmatter rejected: {errors}"


def test_missing_research_grounding_rejected(schemas_dir: Path) -> None:
    """Case (b): dropping ``research_grounding`` violates ``required``."""
    validator = _validator_for(schemas_dir)
    payload = _minimal_valid()
    del payload["research_grounding"]
    errors = list(validator.iter_errors(payload))
    assert errors, "missing research_grounding must be rejected by required"


def test_unknown_grounding_mode_rejected(schemas_dir: Path) -> None:
    """Case (c): ``research_grounding: vibes`` is not in the closed enum."""
    validator = _validator_for(schemas_dir)
    payload = _minimal_valid()
    payload["research_grounding"] = "vibes"
    errors = list(validator.iter_errors(payload))
    assert errors, "unknown research_grounding must be rejected by enum"


def test_bad_spec_trailing_hyphen_rejected(schemas_dir: Path) -> None:
    """Case (d): trailing-hyphen slug ``2026-05-09-foo-`` violates the pattern."""
    validator = _validator_for(schemas_dir)
    payload = _minimal_valid()
    payload["spec"] = "2026-05-09-foo-"
    errors = list(validator.iter_errors(payload))
    assert errors, "trailing-hyphen spec must be rejected by pattern"


def test_spec_with_double_hyphen_rejected(schemas_dir: Path) -> None:
    """Case (e): ``2026-05-09-foo--bar`` violates the negative-lookahead clause."""
    validator = _validator_for(schemas_dir)
    payload = _minimal_valid()
    payload["spec"] = "2026-05-09-foo--bar"
    errors = list(validator.iter_errors(payload))
    assert errors, "double-hyphen spec must be rejected by pattern lookahead"


def test_focused_tier_validates(schemas_dir: Path) -> None:
    """Case (f): ``tier: focused`` is accepted by the schema.

    Focused refuses research at runtime, but the frontmatter shape stays open
    so a future tier-change does not require a schema bump.
    """
    validator = _validator_for(schemas_dir)
    payload = _minimal_valid()
    payload["tier"] = "focused"
    errors = list(validator.iter_errors(payload))
    assert errors == [], f"tier=focused rejected: {errors}"


def test_parallel_used_true_validates(schemas_dir: Path) -> None:
    """Case (g): ``parallel_used: true`` is a valid optional boolean."""
    validator = _validator_for(schemas_dir)
    payload = _minimal_valid()
    payload["parallel_used"] = True
    errors = list(validator.iter_errors(payload))
    assert errors == [], f"parallel_used=true rejected: {errors}"


def test_unknown_top_level_property_rejected(schemas_dir: Path) -> None:
    """Case (h): ``additionalProperties: false`` rejects unknown keys."""
    validator = _validator_for(schemas_dir)
    payload = _minimal_valid()
    payload["surprise"] = "boo"
    errors = list(validator.iter_errors(payload))
    assert errors, "unknown top-level property must be rejected"
