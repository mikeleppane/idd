"""Read, write, and transition feature state.json files."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import jsonschema


class StateError(RuntimeError):
    """Raised when state.json cannot be read, parsed, or transitioned."""


def _validator_for(schema: dict[str, Any]) -> jsonschema.Draft202012Validator:
    return jsonschema.Draft202012Validator(
        schema,
        format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER,
    )


def read_state(path: Path, schema_path: Path | None = None) -> dict[str, Any]:
    """Read, parse, and (optionally) schema-validate a state.json file.

    Format-aware: when `schema_path` is given, the validator enforces
    `format: date-time` against RFC 3339 timestamps via the
    `rfc3339-validator` extra.

    Args:
        path: Path to the state.json file.
        schema_path: Optional path to a JSON Schema for validation.

    Returns:
        Parsed state.json payload.

    Raises:
        StateError: File missing, invalid JSON, or schema validation fails.
    """
    if not path.exists():
        raise StateError(f"state.json not found at {path}")
    try:
        payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StateError(f"state.json at {path} is invalid JSON: {exc}") from exc

    if schema_path is not None:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        validator = _validator_for(schema)
        errors = sorted(validator.iter_errors(payload), key=lambda e: list(e.path))
        if errors:
            messages = "; ".join(e.message for e in errors)
            raise StateError(f"state.json at {path} fails schema: {messages}")

    return payload


def _utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
