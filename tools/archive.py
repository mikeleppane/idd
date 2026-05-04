"""Feature folder archival + canonical capability spec writes.

These are pure file operations with explicit failure modes; the Python layer
owns the path math so the LLM cannot misplace shipped artifacts.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Callable
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


def ship_feature(
    repo_root: Path,
    feature_id: str,
    capability: str,
    body: str,
    pre_archive_hook: Callable[[Path], None] | None = None,
) -> tuple[Path, Path]:
    """Atomically write the canonical capability spec and archive the feature folder.

    The transactional contract for /idd:ship: all preflight checks pass before any
    write, and the canonical spec write is rolled back if archival fails.

    Preflight (all-or-nothing):
        1. Validate `feature_id` slug.
        2. Validate `capability` slug.
        3. Source `.idd/features/<feature_id>/` must be a directory.
        4. Canonical `.idd/specs/<capability>/SPEC.md` must NOT exist.
        5. Archive target `.idd/features/archive/<feature_id>/` must NOT exist.

    Mutation:
        1. Write canonical spec via `write_canonical_spec`.
        2. Run ``pre_archive_hook(source)`` against the still-live feature folder
           if provided. Use this to mutate ``state.json`` (e.g., mark
           ``current_phase: done``) so the live state and the archived copy
           agree. Hook failure rolls back the canonical write before re-raising.
        3. Move feature folder via `archive_feature`.
        4. On move failure, delete the canonical spec file (and its parent dir
           if it was newly created and is now empty), then re-raise. The
           pre-archive hook's effects on the live state.json are NOT undone —
           callers that mutate state must idempotently re-apply on retry.

    Args:
        repo_root: Repository root containing the .idd/ tree.
        feature_id: Feature folder name in YYYY-MM-DD-slug form.
        capability: Capability slug (lowercase letters, digits, hyphens).
        body: Full SPEC.md text content (frontmatter + body).
        pre_archive_hook: Optional callback that runs after the canonical write
            and before the archive move, given the live ``source`` path. Any
            exception is treated as ship failure and triggers canonical rollback.

    Returns:
        Tuple of (canonical_spec_path, archive_path) on success.

    Raises:
        ArchiveError: any preflight failure (invalid slug, missing source,
            existing canonical, existing archive) leaves the repo untouched.
            Hook or archive-step failure rolls back the canonical write before
            re-raising.
    """
    _validate_feature_id(feature_id)
    _validate_capability(capability)

    source = repo_root / ".idd" / "features" / feature_id
    if not source.is_dir():
        raise ArchiveError(f"feature folder not found: {source}")

    canonical_target = canonical_spec_path(repo_root, capability)
    if canonical_target.exists():
        raise ArchiveError(
            f"canonical spec already exists at {canonical_target}; "
            "feature already shipped — delta proposals (M3+) required for changes",
        )

    archive_target = repo_root / ".idd" / "features" / "archive" / feature_id
    if archive_target.exists():
        raise ArchiveError(f"feature already archived at {archive_target}")

    capability_dir_existed = canonical_target.parent.exists()

    canonical = write_canonical_spec(repo_root, capability, body)

    def _rollback_canonical() -> None:
        canonical.unlink(missing_ok=True)
        if not capability_dir_existed:
            parent = canonical.parent
            if parent.exists() and not any(parent.iterdir()):
                parent.rmdir()

    if pre_archive_hook is not None:
        try:
            pre_archive_hook(source)
        except Exception as exc:
            _rollback_canonical()
            raise ArchiveError(
                f"ship_feature: pre_archive_hook failed; canonical spec rolled back: {exc}",
            ) from exc

    try:
        archived = archive_feature(repo_root, feature_id)
    except ArchiveError as exc:
        _rollback_canonical()
        raise ArchiveError(
            f"ship_feature: archive failed; canonical spec rolled back: {exc}",
        ) from exc
    return canonical, archived
