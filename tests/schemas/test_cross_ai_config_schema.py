"""Tests for ``schemas/cross-ai-config.schema.json``.

Locks the spec §5.3.6 ``cross_ai.*`` config surface so the P1 loader does not
need to bump the schema on day one. Each case targets one rule from the schema:

* ``mode`` enum and the conditional ``allowed_clis`` requirement when
  ``mode == "auto"``.
* ``allowed_clis`` ``uniqueItems`` and ``items.enum``.
* ``additionalProperties: false`` at the top level.
* ``redaction.fatal_regex`` is a permitted, typed field (kept here so the P1
  loader does not have to widen the schema once it starts consuming it).
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema


def _validator_for(schemas_dir: Path) -> jsonschema.Draft202012Validator:
    schema_path = schemas_dir / "cross-ai-config.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    return jsonschema.Draft202012Validator(
        schema,
        format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER,
    )


def test_minimal_manual_mode_validates(schemas_dir: Path) -> None:
    """Case (a): a config with only ``mode: "manual"`` is valid."""
    validator = _validator_for(schemas_dir)
    errors = list(validator.iter_errors({"mode": "manual"}))
    assert errors == [], f"minimal manual mode rejected: {errors}"


def test_auto_mode_without_allowed_clis_rejected(schemas_dir: Path) -> None:
    """Case (b): ``mode: "auto"`` requires ``allowed_clis``."""
    validator = _validator_for(schemas_dir)
    errors = list(validator.iter_errors({"mode": "auto"}))
    assert errors, "auto mode without allowed_clis must be rejected"


def test_auto_mode_with_single_allowed_cli_validates(schemas_dir: Path) -> None:
    """Case (c): ``mode: "auto"`` with one allowed CLI is valid."""
    validator = _validator_for(schemas_dir)
    errors = list(validator.iter_errors({"mode": "auto", "allowed_clis": ["codex"]}))
    assert errors == [], f"auto + ['codex'] rejected: {errors}"


def test_auto_mode_with_empty_allowed_clis_rejected(schemas_dir: Path) -> None:
    """Case (d): ``allowed_clis: []`` violates ``minItems: 1`` in auto mode."""
    validator = _validator_for(schemas_dir)
    errors = list(validator.iter_errors({"mode": "auto", "allowed_clis": []}))
    assert errors, "empty allowed_clis must be rejected by minItems: 1"


def test_auto_mode_with_duplicate_allowed_clis_rejected(schemas_dir: Path) -> None:
    """Case (e): ``allowed_clis`` enforces ``uniqueItems``."""
    validator = _validator_for(schemas_dir)
    errors = list(validator.iter_errors({"mode": "auto", "allowed_clis": ["codex", "codex"]}))
    assert errors, "duplicate entries in allowed_clis must be rejected"


def test_disabled_mode_without_allowed_clis_validates(schemas_dir: Path) -> None:
    """Case (f): ``mode: "disabled"`` does not require ``allowed_clis``."""
    validator = _validator_for(schemas_dir)
    errors = list(validator.iter_errors({"mode": "disabled"}))
    assert errors == [], f"disabled mode without allowed_clis rejected: {errors}"


def test_unknown_cli_in_allowed_clis_rejected(schemas_dir: Path) -> None:
    """Case (g): ``allowed_clis`` items must be one of the known CLI names."""
    validator = _validator_for(schemas_dir)
    errors = list(validator.iter_errors({"mode": "auto", "allowed_clis": ["bogus"]}))
    assert errors, "unknown CLI in allowed_clis must be rejected by items.enum"


def test_unknown_top_level_property_rejected(schemas_dir: Path) -> None:
    """Case (h): ``additionalProperties: false`` rejects unknown top-level keys."""
    validator = _validator_for(schemas_dir)
    errors = list(validator.iter_errors({"mode": "manual", "surprise": True}))
    assert errors, "unknown top-level property must be rejected"


def test_redaction_fatal_regex_validates(schemas_dir: Path) -> None:
    """Case (i): ``redaction.fatal_regex`` is a permitted array of strings.

    Locks the spec §5.3.6 contract that the P1 loader will exercise; without
    this field the loader would force a schema bump on day one.
    """
    validator = _validator_for(schemas_dir)
    payload = {
        "mode": "manual",
        "redaction": {"fatal_regex": ["[Aa]piKey"]},
    }
    errors = list(validator.iter_errors(payload))
    assert errors == [], f"redaction.fatal_regex rejected: {errors}"
