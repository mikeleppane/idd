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


VALID_LIFECYCLE_PHASES = (
    "refine",
    "research",
    "spec",
    "domain",
    "scenarios",
    "plan",
    "crucible",
    "review",
    "execute",
    "verify",
    "ship",
)


def write_state(
    path: Path,
    payload: dict[str, Any],
    schema_path: Path | None = None,
) -> None:
    """Validate (when schema given) and write payload to disk pretty-printed.

    On schema failure, no file is written.

    Args:
        path: Destination file.
        payload: State payload.
        schema_path: Optional schema for validation before write.

    Raises:
        StateError: Validation failed; file not written.
    """
    if schema_path is not None:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        validator = _validator_for(schema)
        errors = sorted(validator.iter_errors(payload), key=lambda e: list(e.path))
        if errors:
            messages = "; ".join(e.message for e in errors)
            raise StateError(f"refusing to write: payload fails schema: {messages}")

    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def complete_phase(
    path: Path,
    phase: str,
    schema_path: Path | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    """Mark `phase` done with completed_at timestamp. Persist and return new state.

    Lifecycle ordering is enforced: ``phase`` must be the current phase and
    its existing status must be ``in_progress``. ``current_phase`` is NOT
    changed; call ``start_phase`` next to move forward.

    Args:
        path: state.json path.
        phase: Lifecycle phase name to complete.
        schema_path: Optional schema for read+write validation.
        now: Optional ISO 8601 timestamp; defaults to UTC now.

    Returns:
        Updated state payload.

    Raises:
        StateError: Unknown phase, missing ``phases`` map, missing entry,
            phase is not the current phase, status is not ``in_progress``,
            or schema validation fails.
    """
    if phase not in VALID_LIFECYCLE_PHASES:
        raise StateError(f"unknown phase '{phase}'; must be one of {VALID_LIFECYCLE_PHASES}")

    payload = read_state(path, schema_path=schema_path)
    timestamp = now or _utc_now_iso()

    if "phases" not in payload or not isinstance(payload["phases"], dict):
        raise StateError("state.json is missing the required `phases` mapping")
    if phase not in payload["phases"]:
        raise StateError(f"cannot complete phase '{phase}': not present in phases")
    if payload.get("current_phase") != phase:
        raise StateError(
            f"cannot complete phase '{phase}': current_phase is '{payload.get('current_phase')}'"
        )
    current_status = payload["phases"][phase].get("status")
    if current_status != "in_progress":
        raise StateError(
            f"cannot complete phase '{phase}': status is '{current_status}', expected 'in_progress'"
        )

    payload["phases"][phase]["status"] = "done"
    payload["phases"][phase]["completed_at"] = timestamp

    write_state(path, payload, schema_path=schema_path)
    return payload


def start_phase(
    path: Path,
    phase: str,
    schema_path: Path | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    """Set `current_phase = phase` and create/replace its phases entry as in_progress.

    Args:
        path: state.json path.
        phase: Lifecycle phase name to start.
        schema_path: Optional schema for read+write validation.
        now: Optional ISO 8601 timestamp; defaults to UTC now.

    Returns:
        Updated state payload.

    Raises:
        StateError: Unknown phase or schema failure.
    """
    if phase not in VALID_LIFECYCLE_PHASES:
        raise StateError(f"unknown phase '{phase}'; must be one of {VALID_LIFECYCLE_PHASES}")

    payload = read_state(path, schema_path=schema_path)
    timestamp = now or _utc_now_iso()

    if "phases" not in payload or not isinstance(payload["phases"], dict):
        raise StateError("state.json is missing the required `phases` mapping")

    payload["phases"][phase] = {"status": "in_progress", "started_at": timestamp}
    payload["current_phase"] = phase

    write_state(path, payload, schema_path=schema_path)
    return payload


def finish_feature(
    path: Path,
    schema_path: Path | None = None,
) -> dict[str, Any]:
    """Mark the feature finished by setting current_phase = 'done'.

    Does not add a 'done' entry under `phases` (schema's propertyNames forbids it).

    Args:
        path: state.json path.
        schema_path: Optional schema for read+write validation.

    Returns:
        Updated state payload.
    """
    payload = read_state(path, schema_path=schema_path)
    payload["current_phase"] = "done"
    write_state(path, payload, schema_path=schema_path)
    return payload


def record_routing_decision(
    path: Path,
    *,
    idea: str,
    final_tier: str,
    proposed_tier: str | None = None,
    rationale: str | None = None,
    constitution_present: bool = False,
    schema_path: Path | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    """Record a routing decision in state.json.routing. Idempotent overwrite.

    Args:
        path: state.json path.
        idea: User-supplied idea text.
        final_tier: Tier the user confirmed (focused/standard/full).
        proposed_tier: Tier the router proposed before user override.
        rationale: One-sentence reason from the router or user.
        constitution_present: True when .idd/CONSTITUTION.md was loaded at routing time.
        schema_path: Optional schema for read+write validation.
        now: Optional ISO 8601 timestamp; defaults to UTC now.

    Returns:
        Updated state payload.

    Raises:
        StateError: schema validation failure on read or write.
    """
    payload = read_state(path, schema_path=schema_path)
    block: dict[str, Any] = {
        "idea": idea,
        "final_tier": final_tier,
        "decided_at": now or _utc_now_iso(),
        "constitution_present": constitution_present,
    }
    if proposed_tier is not None:
        block["proposed_tier"] = proposed_tier
    if rationale is not None:
        block["rationale"] = rationale
    payload["routing"] = block
    write_state(path, payload, schema_path=schema_path)
    return payload


def feature_folder_exists(repo_root: Path, feature_id: str) -> bool:
    """Return True when .idd/features/<feature_id>/ exists under repo_root."""
    return (repo_root / ".idd" / "features" / feature_id).is_dir()
