"""Feature folder archival + canonical capability spec writes.

These are pure file operations with explicit failure modes; the Python layer
owns the path math so the LLM cannot misplace shipped artifacts.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

_CAPABILITY_RE = re.compile(r"^[a-z0-9-]+$")
_FEATURE_ID_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])-[a-z0-9-]+$")


class ArchiveError(RuntimeError):
    """Raised when archival or canonical spec writes cannot proceed."""


def _validate_capability(capability: str) -> None:
    if not _CAPABILITY_RE.fullmatch(capability):
        raise ArchiveError(f"invalid capability slug: {capability!r}")


def _validate_feature_id(feature_id: str) -> None:
    if not _FEATURE_ID_RE.fullmatch(feature_id):
        raise ArchiveError(f"invalid feature id: {feature_id!r}")


def archive_feature(repo_root: Path, feature_id: str) -> Path:
    """Move .idd/features/<id>/ to .idd/features/archive/<id>/.

    Args:
        repo_root: Repository root containing the .idd/ tree.
        feature_id: Feature folder name in YYYY-MM-DD-slug form.

    Returns:
        Path to the archived feature folder.

    Raises:
        ArchiveError: feature id malformed, source missing, or target already exists.
    """
    _validate_feature_id(feature_id)
    source = repo_root / ".idd" / "features" / feature_id
    if not source.is_dir():
        raise ArchiveError(f"feature folder not found: {source}")
    archive_root = repo_root / ".idd" / "features" / "archive"
    archive_root.mkdir(parents=True, exist_ok=True)
    target = archive_root / feature_id
    if target.exists():
        raise ArchiveError(f"feature already archived at {target}")
    shutil.move(str(source), str(target))
    return target


def canonical_spec_path(repo_root: Path, capability: str) -> Path:
    """Return .idd/specs/<capability>/SPEC.md (does not validate existence).

    Args:
        repo_root: Repository root containing the .idd/ tree.
        capability: Capability slug (lowercase letters, digits, hyphens).

    Returns:
        Path to the canonical capability SPEC.md.

    Raises:
        ArchiveError: capability slug malformed.
    """
    _validate_capability(capability)
    return repo_root / ".idd" / "specs" / capability / "SPEC.md"


def write_canonical_spec(repo_root: Path, capability: str, body: str) -> Path:
    """Write the canonical capability SPEC.md. Refuses to overwrite.

    Args:
        repo_root: Repository root containing the .idd/ tree.
        capability: Capability slug (lowercase letters, digits, hyphens).
        body: Full SPEC.md text content (frontmatter + body).

    Returns:
        Path to the written canonical SPEC.md.

    Raises:
        ArchiveError: capability slug malformed, or canonical spec already exists.
    """
    target = canonical_spec_path(repo_root, capability)
    if target.exists():
        raise ArchiveError(
            f"canonical spec already exists at {target}; "
            "delta proposals (M3+) required for changes",
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    return target
