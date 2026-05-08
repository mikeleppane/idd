"""Feature folder archival + canonical capability spec writes.

These are pure file operations with explicit failure modes; the Python layer
owns the path math so the LLM cannot misplace shipped artifacts.
"""

from __future__ import annotations

import re
import shutil
import sys
from collections.abc import Callable
from pathlib import Path

_CAPABILITY_RE = re.compile(r"^[a-z0-9-]+$")
# Schema-aligned slug: must start with alnum, at least 3 chars total.
# Matches schemas/capability-spec-frontmatter.schema.json and
# delta-proposal-frontmatter.schema.json:affects_capability.
_CAPABILITY_SLUG_SCHEMA_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,}$")
_FEATURE_ID_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])-[a-z0-9-]+$")

# Minimum token length to survive the content-word filter (step 5 of slug_from_idea).
_SLUG_MIN_TOKEN_LEN: int = 2

_STOPWORDS: frozenset[str] = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "of",
        "to",
        "with",
        "for",
        "from",
        "by",
        "in",
        "on",
        "at",
        "as",
        "is",
        "be",
        "that",
        "this",
        "my",
        "our",
        "we",
    }
)
# Pattern matches `[constitution:A<n>]` tags inside REVIEW.code.md cells.
# Used by the advisory `_emit_constitution_skip_warning` helper.
_CONSTITUTION_TAG_RE = re.compile(r"\[constitution:A\d+\]")
_SLUG_CLEANUP_RE = re.compile(r"[^a-z0-9 ]")


class ArchiveError(RuntimeError):
    """Raised when archival or canonical spec writes cannot proceed."""


def slug_from_idea(text: str, *, max_words: int = 5) -> str:
    """Derive a capability slug from a free-text idea description.

    The algorithm is deterministic and requires no NLP or external calls.

    Steps:
        1. Lowercase the input.
        2. Replace any character outside ``[a-z0-9 ]`` with a single space.
        3. Tokenize on whitespace.
        4. Drop stopwords from ``_STOPWORDS``.
        5. Drop tokens of length < 2.
        6. Take the first ``max_words`` distinct tokens (preserve insertion
           order, deduplicate).
        7. Hyphen-join.  The result must match
           ``^[a-z0-9][a-z0-9-]{2,}$`` (≥ 3 chars, alnum-leading).

    Args:
        text: Free-text idea description from the user.
        max_words: Maximum number of distinct content tokens to use.
                   Keyword-only; defaults to 5.

    Returns:
        A valid capability slug string.

    Raises:
        ValueError: When ``max_words`` is less than 1 (programmer error /
            invalid argument).
        ArchiveError: When the final slug is empty (message contains the
            verbatim ``text``), or shorter than 3 characters (message
            contains both the computed slug and the verbatim ``text``).
    """
    if max_words < 1:
        raise ValueError(f"max_words must be >= 1, got {max_words}")
    lowered = text.lower()
    cleaned = _SLUG_CLEANUP_RE.sub(" ", lowered)
    tokens = cleaned.split()
    # Drop stopwords and tokens that are too short (length < _SLUG_MIN_TOKEN_LEN)
    content = [t for t in tokens if t not in _STOPWORDS and len(t) >= _SLUG_MIN_TOKEN_LEN]
    # Take first max_words distinct tokens (deduplicate, preserve order)
    seen: set[str] = set()
    distinct: list[str] = []
    for token in content:
        if token not in seen:
            seen.add(token)
            distinct.append(token)
        if len(distinct) == max_words:
            break
    slug = "-".join(distinct)
    # Validate final slug matches the schema-aligned pattern
    if not slug or not _CAPABILITY_SLUG_SCHEMA_RE.fullmatch(slug):
        if not slug:
            raise ArchiveError(f"slug computed from idea is empty: {text}")
        raise ArchiveError(f"slug computed from idea is too short: {slug} ({text})")
    return slug


def _validate_capability(capability: str) -> None:
    if not _CAPABILITY_RE.fullmatch(capability):
        raise ArchiveError(f"invalid capability slug: {capability!r}")


def _validate_feature_id(feature_id: str) -> None:
    if not _FEATURE_ID_RE.fullmatch(feature_id):
        raise ArchiveError(f"invalid feature id: {feature_id!r}")


def archive_feature(repo_root: Path, feature_id: str) -> Path:
    """Move .forge/features/<id>/ to .forge/features/archive/<id>/.

    Args:
        repo_root: Repository root containing the .forge/ tree.
        feature_id: Feature folder name in YYYY-MM-DD-slug form.

    Returns:
        Path to the archived feature folder.

    Raises:
        ArchiveError: feature id malformed, source missing, or target already exists.
    """
    _validate_feature_id(feature_id)
    source = repo_root / ".forge" / "features" / feature_id
    if not source.is_dir():
        raise ArchiveError(f"feature folder not found: {source}")
    archive_root = repo_root / ".forge" / "features" / "archive"
    archive_root.mkdir(parents=True, exist_ok=True)
    target = archive_root / feature_id
    if target.exists():
        raise ArchiveError(f"feature already archived at {target}")
    shutil.move(str(source), str(target))
    return target


def canonical_spec_path(repo_root: Path, capability: str) -> Path:
    """Return .forge/specs/<capability>/SPEC.md (does not validate existence).

    Args:
        repo_root: Repository root containing the .forge/ tree.
        capability: Capability slug (lowercase letters, digits, hyphens).

    Returns:
        Path to the canonical capability SPEC.md.

    Raises:
        ArchiveError: capability slug malformed.
    """
    _validate_capability(capability)
    return repo_root / ".forge" / "specs" / capability / "SPEC.md"


def write_canonical_spec(repo_root: Path, capability: str, body: str) -> Path:
    """Write the canonical capability SPEC.md. Refuses to overwrite.

    Args:
        repo_root: Repository root containing the .forge/ tree.
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


def _emit_constitution_skip_warning(repo_root: Path, feature_id: str) -> None:
    """Best-effort stderr notice when the Constitution gate is silently skipped.

    Advisory only. Runs at the top of ``ship_feature`` when the caller did
    NOT pass a ``pre_archive_hook``. Surfaces a single-line WARN to stderr
    when:

    - ``.forge/CONSTITUTION.md`` exists (the project opted in to a
      Constitution), AND
    - the feature's ``REVIEW.code.md`` carries at least one
      ``[constitution:A<n>]`` tag (some unresolved finding cited an
      article).

    The check is intentionally cheap and string-based — no parser dependency,
    no Constitution loading. Any internal exception is swallowed so a
    malformed file cannot regress ship_feature itself; the gate stays
    advisory in M3 (M4 will integrate properly).

    The exact wording is contract: ``Constitution gate skipped`` — the test
    suite pins it so tooling can grep the output.
    """
    try:
        constitution = repo_root / ".forge" / "CONSTITUTION.md"
        review = repo_root / ".forge" / "features" / feature_id / "REVIEW.code.md"
        if not constitution.exists() or not review.exists():
            return
        text = review.read_text(encoding="utf-8", errors="replace")
        if not _CONSTITUTION_TAG_RE.search(text):
            return
        # Single-line WARN; the orchestrator/operator can grep it. Body
        # mirrors the SKILL.md step number so the operator has a precise
        # pointer back to the documented gate flow.
        print(
            "WARN: Constitution gate skipped — see /forge:ship SKILL.md step 3.5",
            file=sys.stderr,
        )
    except Exception:
        # A malformed REVIEW.code.md or read error must not regress
        # ship_feature itself. The warning is opportunistic; the bare
        # `Exception` is intentional — any failure inside the helper is
        # swallowed so the advisory cannot break the ship contract.
        return


def ship_feature(
    repo_root: Path,
    feature_id: str,
    capability: str,
    body: str,
    pre_archive_hook: Callable[[Path], None] | None = None,
) -> tuple[Path, Path]:
    """Atomically write the canonical capability spec and archive the feature folder.

    The transactional contract for /forge:ship: all preflight checks pass before any
    write, and the canonical spec write is rolled back if archival fails.

    Preflight (all-or-nothing):
        1. Validate `feature_id` slug.
        2. Validate `capability` slug.
        3. Source `.forge/features/<feature_id>/` must be a directory.
        4. Canonical `.forge/specs/<capability>/SPEC.md` must NOT exist.
        5. Archive target `.forge/features/archive/<feature_id>/` must NOT exist.

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
        repo_root: Repository root containing the .forge/ tree.
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
    if pre_archive_hook is None:
        # Advisory only. M3 keeps ship_feature itself unchanged (no
        # raise/abort); the gate hook lives in tools.ship_gate. The warning
        # makes a misconfigured retry that drops the gate hook visible to
        # the operator instead of silent.
        _emit_constitution_skip_warning(repo_root, feature_id)

    _validate_feature_id(feature_id)
    _validate_capability(capability)

    source = repo_root / ".forge" / "features" / feature_id
    if not source.is_dir():
        raise ArchiveError(f"feature folder not found: {source}")

    canonical_target = canonical_spec_path(repo_root, capability)
    if canonical_target.exists():
        raise ArchiveError(
            f"canonical spec already exists at {canonical_target}; "
            "feature already shipped — delta proposals (M3+) required for changes",
        )

    archive_target = repo_root / ".forge" / "features" / "archive" / feature_id
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
