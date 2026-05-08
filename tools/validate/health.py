"""Repo-wide FORGE health validator (M3 §5.3.6 D-HEALTH)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from ._feature_layout import TEMPLATED_FEATURE_FILES
from ._finding import Finding
from ._frontmatter import (
    _build_validator,
    _FrontmatterParseError,
    _load_schema,
    _parse_frontmatter,
    _read_text,
)
from .constitution import validate_constitution
from .spec_structural import validate_capability_uniqueness


def _state_payload(state_path: Path) -> dict[str, Any] | None:
    """Best-effort parse of a state.json. Returns None on any failure."""
    text = _read_text(state_path)
    if text is None:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _check_feature_payload(
    entry: Path,
    state_path: Path,
    payload: dict[str, Any],
) -> list[Finding]:
    """Check id match, phase validity, done-not-archived, and orphan for a parsed payload."""
    findings: list[Finding] = []
    recorded_id = payload.get("feature_id")
    if recorded_id != entry.name:
        findings.append(
            Finding(
                "HIGH",
                "health",
                state_path,
                f"folder name {entry.name!r} does not match "
                f"state.json.feature_id {recorded_id!r}; "
                f"manual rename or git mv required",
            ),
        )
        return findings

    current_phase = payload.get("current_phase")
    phases = payload.get("phases")
    if (
        isinstance(current_phase, str)
        and isinstance(phases, dict)
        and current_phase != "done"
        and current_phase not in phases
    ):
        findings.append(
            Finding(
                "BLOCK",
                "health",
                state_path,
                f"current_phase {current_phase!r} not in phases enum "
                f"for feature {entry.name!r}; restore from git history",
            ),
        )
        return findings

    if current_phase == "done":
        findings.append(
            Finding(
                "MEDIUM",
                "health",
                entry,
                f"feature {entry.name!r} is at current_phase=done but not "
                f"archived; run /forge:ship or tools.archive.archive_feature",
            ),
        )
        return findings

    commits = payload.get("commits") or []
    refine_block = phases.get("refine") if isinstance(phases, dict) else None
    extra_files = [p for p in entry.iterdir() if p.name not in TEMPLATED_FEATURE_FILES]
    if (
        current_phase == "refine"
        and isinstance(refine_block, dict)
        and refine_block.get("status") == "in_progress"
        and not commits
        and not extra_files
    ):
        findings.append(
            Finding(
                "LOW",
                "health",
                entry,
                f"orphan feature folder {entry.name!r} (refine + no commits); "
                f"run tools.archive.cleanup_orphan_feature(<id>) when ready",
            ),
        )
    return findings


def _check_feature_entry(
    entry: Path,
    state_validator: Draft202012Validator,
) -> list[Finding]:
    """Run all per-feature health checks. Returns findings for one feature folder."""
    state_path = entry / "state.json"
    if not state_path.exists():
        return [
            Finding(
                "HIGH",
                "health",
                entry,
                f"feature folder {entry.name!r} is missing state.json; "
                f"re-seed from templates/feature/state.json or archive",
            )
        ]

    payload = _state_payload(state_path)
    if payload is None:
        return [
            Finding(
                "BLOCK",
                "health",
                state_path,
                f"state.json failed to parse for feature {entry.name!r}; restore from git history",
            )
        ]

    schema_errors = sorted(
        state_validator.iter_errors(payload),
        key=lambda e: list(e.path),
    )
    if schema_errors:
        return [
            Finding(
                "BLOCK",
                "health",
                state_path,
                f"state.json fails schema for feature {entry.name!r}: "
                f"{err.message}; restore from git history",
            )
            for err in schema_errors
        ]

    return _check_feature_payload(entry, state_path, payload)


def _check_change_entry(entry: Path, canonical_root: Path) -> list[Finding]:
    """Run all per-change health checks. Returns findings for one change folder."""
    findings: list[Finding] = []
    proposal = entry / "proposal.md"
    if not proposal.exists():
        return findings
    text = _read_text(proposal)
    if text is None:
        return findings
    try:
        parsed = _parse_frontmatter(text)
    except _FrontmatterParseError:
        return findings
    if parsed is None:
        return findings
    fm, _body = parsed
    affects = fm.get("affects_capability")
    status = fm.get("status")
    if isinstance(affects, str):
        canonical = canonical_root / affects / "SPEC.md"
        if not canonical.exists():
            findings.append(
                Finding(
                    "HIGH",
                    "health",
                    proposal,
                    f"change {entry.name!r} targets non-existent "
                    f"canonical capability {affects!r}; fix affects_capability "
                    f"or drop the change",
                ),
            )
            return findings
    if status == "approved":
        findings.append(
            Finding(
                "MEDIUM",
                "health",
                proposal,
                f"change {entry.name!r} is approved but not merged; "
                f"run /forge:ship --change {entry.name}",
            ),
        )
    return findings


def _check_canonical_entry(entry: Path) -> list[Finding]:
    """Run canonical-spec health checks for one slug folder."""
    findings: list[Finding] = []
    spec = entry / "SPEC.md"
    text = _read_text(spec)
    if text is None:
        return findings
    try:
        parsed = _parse_frontmatter(text)
    except _FrontmatterParseError:
        return findings
    if parsed is None:
        return findings
    fm, _body = parsed
    if not fm.get("evidence"):
        findings.append(
            Finding(
                "LOW",
                "health",
                spec,
                f"canonical spec {entry.name!r} missing 'evidence:' link "
                f"to source archived feature; backfill manually",
            ),
        )
    return findings


def validate_health(repo_root: Path) -> list[Finding]:
    """Repo-wide FORGE health scan per M3 spec §5.3.6 D-HEALTH.

    Read-only. Each finding has severity + remediation hint embedded in message.
    Severities mirror the spec table directly:

        | Check                                                         | Severity |
        | Orphan feature folder                                          | LOW      |
        | Feature folder name != state.json.feature_id                   | HIGH     |
        | state.json fails to parse                                      | BLOCK    |
        | state.json fails schema validation                             | BLOCK    |
        | state.json.current_phase not in phases enum                    | BLOCK    |
        | Feature folder missing state.json                              | HIGH     |
        | Feature with current_phase=done not archived                   | MEDIUM   |
        | Capability slug collision                                      | HIGH     |
        | Approved change not merged                                     | MEDIUM   |
        | Change targets non-existent canonical capability               | HIGH     |
        | Canonical SPEC.md missing `evidence:` link                     | LOW      |
        | Constitution article count >=12                                | WARN     |
        | Constitution article count >=16                                | BLOCK    |

    Args:
        repo_root: Repository root containing the .forge/ tree.

    Returns:
        List of Finding records. Empty list means all checks clean.

    Note:
        Findings delegated from `validate_capability_uniqueness` and
        `validate_constitution` carry their source validator's `target` field
        (e.g. ``"capability-uniqueness"``, ``"constitution"``) rather than
        ``"health"``. This preserves provenance so the user knows which
        sub-validator produced each finding when ``/forge:validate --target
        health`` aggregates results.
    """
    findings: list[Finding] = []
    forge_root = repo_root / ".forge"
    if not forge_root.is_dir():
        return findings

    findings.extend(validate_capability_uniqueness(repo_root))

    constitution = forge_root / "CONSTITUTION.md"
    if constitution.exists():
        findings.extend(validate_constitution(constitution))

    state_schema = _load_schema("state.schema.json")
    state_validator = _build_validator(state_schema)

    features_root = forge_root / "features"
    if features_root.is_dir():
        for entry in sorted(features_root.iterdir()):
            if not entry.is_dir() or entry.name == "archive":
                continue
            findings.extend(_check_feature_entry(entry, state_validator))

    canonical_root = forge_root / "specs"
    changes_root = forge_root / "changes"
    if changes_root.is_dir():
        for entry in sorted(changes_root.iterdir()):
            if not entry.is_dir() or entry.name == "archive":
                continue
            findings.extend(_check_change_entry(entry, canonical_root))

    if canonical_root.is_dir():
        for entry in sorted(canonical_root.iterdir()):
            if not entry.is_dir():
                continue
            findings.extend(_check_canonical_entry(entry))

    return findings
