"""Tests for ``schemas/research-config.schema.json``.

Locks the spec ``research.*`` config surface so the future loader does not
need to bump the schema on day one. Each case targets one rule from the schema:

* All fields are optional (empty ``{}`` is a valid config).
* ``websearch_fallback`` is a typed boolean.
* Numeric bounds (``minimum``) on ``websearch_max_queries_per_run``.
* ``ecosystems`` items honour the closed enum (12 known ecosystems).
* ``ecosystems`` enforces ``uniqueItems`` so a user pin cannot list the same
  plugin twice and quietly mask a typo.
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema


def _validator_for(schemas_dir: Path) -> jsonschema.Draft202012Validator:
    schema_path = schemas_dir / "research-config.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    return jsonschema.Draft202012Validator(
        schema,
        format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER,
    )


def test_empty_config_validates(schemas_dir: Path) -> None:
    """Case (a): an empty config ``{}`` is valid (all fields optional)."""
    validator = _validator_for(schemas_dir)
    errors = list(validator.iter_errors({}))
    assert errors == [], f"empty config rejected: {errors}"


def test_websearch_fallback_boolean_validates(schemas_dir: Path) -> None:
    """Case (b): ``{websearch_fallback: true}`` is valid."""
    validator = _validator_for(schemas_dir)
    errors = list(validator.iter_errors({"websearch_fallback": True}))
    assert errors == [], f"websearch_fallback=true rejected: {errors}"


def test_websearch_max_queries_below_minimum_rejected(schemas_dir: Path) -> None:
    """Case (c): ``websearch_max_queries_per_run: 0`` violates ``minimum: 1``."""
    validator = _validator_for(schemas_dir)
    errors = list(validator.iter_errors({"websearch_max_queries_per_run": 0}))
    assert errors, "websearch_max_queries_per_run=0 must be rejected by minimum"


def test_known_ecosystems_validate(schemas_dir: Path) -> None:
    """Case (d): ``{ecosystems: ["python", "node"]}`` is valid."""
    validator = _validator_for(schemas_dir)
    errors = list(validator.iter_errors({"ecosystems": ["python", "node"]}))
    assert errors == [], f"known ecosystems rejected: {errors}"


def test_unknown_ecosystem_rejected(schemas_dir: Path) -> None:
    """Case (e): ``{ecosystems: ["cobol"]}`` is rejected by items.enum."""
    validator = _validator_for(schemas_dir)
    errors = list(validator.iter_errors({"ecosystems": ["cobol"]}))
    assert errors, "unknown ecosystem must be rejected by items.enum"


def test_duplicate_ecosystems_rejected(schemas_dir: Path) -> None:
    """Case (f): duplicate entries in ``ecosystems`` violate ``uniqueItems``."""
    validator = _validator_for(schemas_dir)
    errors = list(validator.iter_errors({"ecosystems": ["python", "python"]}))
    assert errors, "duplicate ecosystems must be rejected by uniqueItems"


def test_unknown_top_level_property_rejected(schemas_dir: Path) -> None:
    """Case (g): ``additionalProperties: false`` rejects unknown top-level keys."""
    validator = _validator_for(schemas_dir)
    errors = list(validator.iter_errors({"surprise": True}))
    assert errors, "unknown top-level property must be rejected"
