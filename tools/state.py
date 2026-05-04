"""Read, write, and transition feature state.json files."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import jsonschema


class StateError(RuntimeError):
    """Raised when state.json cannot be read, parsed, or transitioned."""


_FEATURE_ID_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])-[a-z0-9-]+$")


VALID_TIERS = ("focused", "standard", "full")


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


VALID_REVIEW_TARGETS = ("plan", "code")


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

    if phase == "review":
        targets_done = sorted(payload["phases"][phase].get("targets_done", []))
        required = sorted(VALID_REVIEW_TARGETS)
        if targets_done != required:
            raise StateError(
                f"cannot complete phase 'review': both review targets must be done; "
                f"targets_done={targets_done}, required={required}"
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

    new_block: dict[str, Any] = {"status": "in_progress", "started_at": timestamp}
    if phase == "review":
        prior = payload["phases"].get("review")
        if isinstance(prior, dict):
            for carry in ("targets_done", "current_target"):
                if carry in prior:
                    new_block[carry] = prior[carry]
    payload["phases"][phase] = new_block
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
    if final_tier not in VALID_TIERS:
        raise StateError(f"invalid final_tier {final_tier!r}; must be one of {VALID_TIERS}")
    if proposed_tier is not None and proposed_tier not in VALID_TIERS:
        raise StateError(f"invalid proposed_tier {proposed_tier!r}; must be one of {VALID_TIERS}")
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


def record_refined_idea(
    path: Path,
    *,
    refined: str,
    schema_path: Path | None = None,
) -> dict[str, Any]:
    """Persist the refined idea paragraph to state.json.refined_idea.

    Args:
        path: state.json path.
        refined: Single-paragraph refined idea text.
        schema_path: Optional schema for read+write validation.

    Returns:
        Updated state payload.

    Raises:
        StateError: empty input, or schema validation failure.
    """
    if not refined.strip():
        raise StateError("refined_idea must be non-empty")
    payload = read_state(path, schema_path=schema_path)
    payload["refined_idea"] = refined
    write_state(path, payload, schema_path=schema_path)
    return payload


def set_review_target(
    path: Path,
    *,
    review_target: str,
    schema_path: Path | None = None,
) -> dict[str, Any]:
    """Set phases.review.current_target and ensure targets_done is initialized.

    Args:
        path: state.json path.
        review_target: One of VALID_REVIEW_TARGETS.
        schema_path: Optional schema for read+write validation.

    Returns:
        Updated state payload.

    Raises:
        StateError: invalid review_target, missing review entry, or schema failure.
    """
    if review_target not in VALID_REVIEW_TARGETS:
        raise StateError(
            f"invalid review_target {review_target!r}; must be one of {VALID_REVIEW_TARGETS}"
        )
    payload = read_state(path, schema_path=schema_path)
    review_block = payload.get("phases", {}).get("review")
    if review_block is None:
        raise StateError("cannot set review_target: phases.review entry missing")
    review_status = review_block.get("status")
    if review_status != "in_progress":
        raise StateError(
            f"cannot set review_target: review status is {review_status!r}, expected 'in_progress'"
        )
    review_block["current_target"] = review_target
    review_block.setdefault("targets_done", [])
    write_state(path, payload, schema_path=schema_path)
    return payload


def complete_review_target(
    path: Path,
    *,
    review_target: str,
    schema_path: Path | None = None,
) -> dict[str, Any]:
    """Append `review_target` to phases.review.targets_done. Idempotent.

    Args:
        path: state.json path.
        review_target: Must equal phases.review.current_target.
        schema_path: Optional schema for read+write validation.

    Returns:
        Updated state payload.

    Raises:
        StateError: invalid target, missing review entry, mismatched current_target,
            or schema failure.
    """
    if review_target not in VALID_REVIEW_TARGETS:
        raise StateError(
            f"invalid review_target {review_target!r}; must be one of {VALID_REVIEW_TARGETS}"
        )
    payload = read_state(path, schema_path=schema_path)
    review_block = payload.get("phases", {}).get("review")
    if review_block is None:
        raise StateError("cannot complete review_target: phases.review entry missing")
    review_status = review_block.get("status")
    if review_status != "in_progress":
        raise StateError(
            f"cannot complete review_target: review status is {review_status!r}, "
            f"expected 'in_progress'"
        )
    current = review_block.get("current_target")
    if current != review_target:
        raise StateError(
            f"cannot complete review_target {review_target!r}: current_target is {current!r}"
        )
    targets_done = review_block.setdefault("targets_done", [])
    if review_target not in targets_done:
        targets_done.append(review_target)
    write_state(path, payload, schema_path=schema_path)
    return payload


def feature_folder_exists(repo_root: Path, feature_id: str) -> bool:
    """Return True when .idd/features/<feature_id>/ exists under repo_root."""
    return (repo_root / ".idd" / "features" / feature_id).is_dir()


def find_active_feature(
    repo_root: Path,
    feature_id: str | None = None,
) -> Path:
    """Resolve which .idd/features/<id>/ to act on. Read-only.

    Precedence (D-S6 in M3 spec):
        1. Explicit `feature_id` arg wins.
        2. Else single active feature (state.json.current_phase != 'done').
        3. Else: zero active -> StateError; multiple active -> StateError listing them.

    Excludes any folder under `.idd/features/archive/`.

    Args:
        repo_root: Repository root containing the .idd/ tree.
        feature_id: Optional explicit feature id (matches folder name).

    Returns:
        Path to the resolved feature folder.

    Raises:
        StateError: when no feature matches, multiple active without explicit id,
            or the explicit id has no matching folder/state.json.
    """
    features_root = repo_root / ".idd" / "features"
    if feature_id is not None:
        if not _FEATURE_ID_RE.fullmatch(feature_id):
            raise StateError(f"invalid feature id: {feature_id!r}")
        candidate = features_root / feature_id
        if not candidate.is_dir() or not (candidate / "state.json").exists():
            raise StateError(f"feature {feature_id!r} not found at {candidate}")
        return candidate

    if not features_root.is_dir():
        raise StateError("no active feature: .idd/features/ does not exist")

    active: list[Path] = []
    for entry in sorted(features_root.iterdir()):
        if not entry.is_dir() or entry.name == "archive":
            continue
        state_path = entry / "state.json"
        if not state_path.exists():
            continue
        try:
            payload = read_state(state_path)
        except StateError as exc:
            raise StateError(
                f"cannot resolve active feature: state.json under {entry.name} is invalid: {exc}"
            ) from exc
        if payload.get("current_phase") != "done":
            active.append(entry)

    if not active:
        raise StateError("no active feature: every feature is at current_phase='done'")
    if len(active) > 1:
        ids = ", ".join(p.name for p in active)
        raise StateError(f"multiple active features ({ids}); pass --feature <id> to disambiguate")
    return active[0]


_FOCUSED_NEXT: dict[str, str | None] = {
    "spec": "/idd:execute",
    "execute": "/idd:verify",
    "verify": None,
}

_STANDARD_NEXT: dict[str, str | None] = {
    "refine": "/idd:spec",
    "spec": "/idd:scenarios",
    "scenarios": "/idd:plan",
    "plan": "/idd:crucible",
    "crucible": "/idd:review --target plan",
    "execute": "/idd:review --target code",
    "verify": "/idd:ship",
    "ship": None,
}

_FULL_NEXT: dict[str, str | None] = {
    **_STANDARD_NEXT,
    "spec": "/idd:domain",
    "domain": "/idd:scenarios",
}


def _next_review_command(state_payload: dict[str, Any]) -> str:
    """Resolve the next command when the current phase is `review`."""
    review = state_payload.get("phases", {}).get("review", {})
    done = review.get("targets_done", [])
    if "plan" not in done:
        return "/idd:review --target plan"
    if "code" not in done:
        return "/idd:execute"
    return "/idd:verify"


def next_phase_command(state_payload: dict[str, Any]) -> str | None:
    """Return the slash-command for the next pipeline phase, or None when done.

    Read-only. Pure function over a state.json payload. See M3 spec §5.3.7.

    Args:
        state_payload: Parsed state.json (must contain `tier`, `current_phase`,
            and `phases`).

    Returns:
        Slash command string (e.g. '/idd:scenarios') or None when at terminal phase.
    """
    tier = state_payload.get("tier")
    phase = state_payload.get("current_phase")

    if not isinstance(phase, str) or not isinstance(tier, str):
        return None

    if tier == "focused":
        return _FOCUSED_NEXT.get(phase)

    table = {"standard": _STANDARD_NEXT, "full": _FULL_NEXT}.get(tier)
    if table is None:
        return None
    if phase == "review":
        return _next_review_command(state_payload)
    return table.get(phase)
