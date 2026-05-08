"""Feature folder archival + canonical capability spec writes.

These are pure file operations with explicit failure modes; the Python layer
owns the path math so the LLM cannot misplace shipped artifacts.
"""

from __future__ import annotations

import fcntl
import json
import re
import shutil
import sys
from collections.abc import Callable
from pathlib import Path

import yaml

from tools.constitution_amend import atomic_replace
from tools.delta_merge import apply_delta_ops, parse_proposal_body
from tools.validate._feature_layout import _ORPHAN_FEATURE_FILES
from tools.validate._finding import EXIT_NONZERO_SEVERITIES, Finding
from tools.validate.delta import validate_delta
from tools.validate.spec_structural import (
    validate_capability_spec_sections,
    validate_frontmatter,
)

_CAPABILITY_RE = re.compile(r"^[a-z0-9-]+$")
# Schema-aligned slug: must start with alnum, at least 3 chars total.
# Matches schemas/capability-spec-frontmatter.schema.json and
# delta-proposal-frontmatter.schema.json:affects_capability.
_CAPABILITY_SLUG_SCHEMA_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,}$")
_FEATURE_ID_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])-[a-z0-9-]+$")
# Schema-aligned change id: strict month/day, schema-aligned slug suffix.
_CHANGE_ID_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])-[a-z0-9][a-z0-9-]{2,}$")

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


def scan_existing_capabilities(repo_root: Path) -> list[str]:
    """Return a sorted list of canonical capability slugs present in the repo.

    A capability is considered canonical when a directory exists at
    ``.forge/specs/<slug>/`` and that directory contains a ``SPEC.md`` file.
    Directories without ``SPEC.md`` are treated as non-canonical and skipped.
    Listing is filesystem-driven, not state-driven.

    Args:
        repo_root: Repository root containing the ``.forge/`` tree.

    Returns:
        Sorted list of capability slug strings (may be empty). Never raises
        ``FileNotFoundError`` — if ``.forge/specs/`` does not exist, returns
        ``[]``.
    """
    specs_dir = repo_root / ".forge" / "specs"
    if not specs_dir.is_dir():
        return []
    return sorted(d.name for d in specs_dir.iterdir() if d.is_dir() and (d / "SPEC.md").is_file())


def _orphan_conditions_met(folder: Path) -> bool:  # noqa: PLR0911
    """Return True when the folder satisfies all three D-2a orphan conditions.

    Conditions (all must hold):
      1. state.json.current_phase == "refine" AND phases.refine.status == "in_progress".
      2. state.json.commits == [].
      3. Folder contents are a strict subset of _ORPHAN_FEATURE_FILES.

    Does NOT raise on I/O failures — returns False so callers treat a broken
    state.json as a non-orphan (safe default). The defensive predicate is
    intentionally a flat sequence of independent guards (PLR0911 silenced):
    each early-return marks a distinct precondition that must hold, and
    flattening to one final boolean would obscure which condition failed.
    """
    state_path = folder / "state.json"
    if not state_path.is_file():
        return False
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    if not isinstance(payload, dict):
        return False

    # Condition 1: phase + status
    if payload.get("current_phase") != "refine":
        return False
    phases = payload.get("phases")
    if not isinstance(phases, dict):
        return False
    refine_block = phases.get("refine")
    if not isinstance(refine_block, dict) or refine_block.get("status") != "in_progress":
        return False

    # Condition 2: no commits
    commits = payload.get("commits") or []
    if commits:
        return False

    # Condition 3: folder contents are a strict subset of _ORPHAN_FEATURE_FILES
    try:
        present = {p.name for p in folder.iterdir()}
    except OSError:
        return False
    return present.issubset(_ORPHAN_FEATURE_FILES)


def cleanup_orphan_feature(repo_root: Path, feature_id: str) -> bool:
    """Remove an orphan feature folder that has never advanced past the initial seed.

    Validates ``feature_id`` slug, checks the three D-2a conditions via a shared
    helper, re-checks them immediately before ``shutil.rmtree`` (race-narrowing),
    then removes the folder.  All condition failures are logged to stderr and
    return ``False``; only invalid ``feature_id`` raises ``ArchiveError``.

    D-2a conditions (all three must hold):
      1. ``state.json.current_phase == "refine"`` AND
         ``phases.refine.status == "in_progress"``.
      2. ``state.json.commits == []``.
      3. Folder contents are a strict subset of
         ``_ORPHAN_FEATURE_FILES = {"state.json", "SPEC.md", "decisions.md"}``.

    Args:
        repo_root: Repository root containing the ``.forge/`` tree.
        feature_id: Feature folder name in YYYY-MM-DD-slug form.

    Returns:
        ``True`` on successful removal; ``False`` on any condition violation.

    Raises:
        ArchiveError: ``feature_id`` slug is malformed.
        Any unexpected I/O exception (``PermissionError``, disk error) propagates.
    """
    _validate_feature_id(feature_id)

    folder = repo_root / ".forge" / "features" / feature_id
    if not folder.is_dir():
        print(
            f"WARN: cleanup_orphan_feature: {feature_id!r} is not a directory — skipping",
            file=sys.stderr,
        )
        return False

    # Preflight check.
    if not _orphan_conditions_met(folder):
        print(
            f"WARN: cleanup_orphan_feature: {feature_id!r} does not meet orphan conditions "
            f"(preflight) — skipping",
            file=sys.stderr,
        )
        return False

    # Race-narrowing re-check immediately before rmtree.
    if not _orphan_conditions_met(folder):
        print(
            f"WARN: cleanup_orphan_feature: {feature_id!r} conditions changed before rmtree "
            f"— aborting to avoid data loss",
            file=sys.stderr,
        )
        return False

    shutil.rmtree(folder)
    return True


def _validate_capability(capability: str) -> None:
    if not _CAPABILITY_RE.fullmatch(capability):
        raise ArchiveError(f"invalid capability slug: {capability!r}")


def _validate_feature_id(feature_id: str) -> None:
    if not _FEATURE_ID_RE.fullmatch(feature_id):
        raise ArchiveError(f"invalid feature id: {feature_id!r}")


def _validate_change_id(change_id: str) -> None:
    if not _CHANGE_ID_RE.fullmatch(change_id):
        raise ArchiveError(f"invalid change id: {change_id!r}")


def _run_validator(
    fn: Callable[[Path], list[Finding]],
    path: Path,
    label: str,
) -> None:
    """Run a validator function; raise ArchiveError if any finding's severity is in EXIT_NONZERO_SEVERITIES.

    Args:
        fn: Validator callable that accepts a Path and returns a list of Finding records.
        path: Path to pass to the validator.
        label: Human-readable label used in the ArchiveError message on failure.

    Raises:
        ArchiveError: When any finding has a severity in EXIT_NONZERO_SEVERITIES.
    """
    findings = fn(path)
    blockers = [f for f in findings if f.severity in EXIT_NONZERO_SEVERITIES]
    if blockers:
        details = "; ".join(f"{f.severity}: {f.message}" for f in blockers)
        raise ArchiveError(f"{label} validation failed: {details}")


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


_PROPOSAL_FRONTMATTER_RE = re.compile(r"^---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)


def _mark_change_merged_hook(proposal_path: Path) -> Callable[[Path], None]:
    """Return a closure that flips proposal.md ``status: approved`` -> ``merged``.

    Designed for ``merge_delta_proposal(pre_archive_hook=...)``.  The closure
    captures ``proposal_path`` from its lexical scope; the ``change_folder``
    argument passed by merge_delta_proposal is intentionally ignored because
    the proposal path is already known from the factory argument.

    Idempotency: if ``status`` is already ``merged``, the closure is a no-op
    (does NOT raise).  This matches the retry-safe pattern used by
    ``make_acknowledgement_hook`` in ``tools.ship_gate``.

    Status guard: raises ``ArchiveError`` when the current status is anything
    other than ``approved`` or ``merged``.  ``merge_delta_proposal``'s
    preflight already enforces ``approved``; this guard is a defensive layer
    for buggy or malicious retries that call the hook with an unexpected
    status.

    Args:
        proposal_path: Path to the live ``proposal.md``.

    Returns:
        Callable matching ``Callable[[Path], None]`` for ``merge_delta_proposal``.

    Raises:
        ArchiveError: When the factory is called with a non-existent path (via
            the inner ``_read_proposal_frontmatter`` call), or when the closure
            is invoked and the current status is not ``approved`` or ``merged``.
    """

    def _flip_to_merged(
        change_folder: Path,  # noqa: ARG001 — received from merge_delta_proposal; path is captured
    ) -> None:
        fm = _read_proposal_frontmatter(proposal_path)
        status = fm.get("status")
        if status == "merged":
            return
        if status != "approved":
            raise ArchiveError(f"proposal status is {status!r}; expected approved or merged")
        fm["status"] = "merged"
        # Re-emit frontmatter + original body content below the delimiter.
        text = proposal_path.read_text(encoding="utf-8")
        body_after_fm = re.sub(r"^---\r?\n.*?\r?\n---\r?\n", "", text, count=1, flags=re.DOTALL)
        new_text = (
            "---\n"
            + yaml.safe_dump(fm, default_flow_style=False, allow_unicode=True)
            + "---\n"
            + body_after_fm
        )
        atomic_replace(proposal_path, new_text)

    return _flip_to_merged


def _read_proposal_frontmatter(proposal_path: Path) -> dict[str, object]:
    """Parse and return proposal.md YAML frontmatter as a dict.

    Uses the same ``---`` block + ``yaml.safe_load`` path as
    ``tools.validate._frontmatter`` for consistent behaviour.

    Raises:
        ArchiveError: When the frontmatter block is absent or malformed.
    """
    text = proposal_path.read_text(encoding="utf-8")
    match = _PROPOSAL_FRONTMATTER_RE.match(text)
    if not match:
        raise ArchiveError(f"proposal frontmatter missing or malformed: {proposal_path}")
    try:
        parsed = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise ArchiveError(f"proposal frontmatter YAML error: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ArchiveError(
            f"proposal frontmatter must be a YAML mapping, got {type(parsed).__name__}"
        )
    return parsed


def _validate_merged_body(change_folder: Path, merged_body: str) -> None:
    """Write merged body to a temp file, validate it, and clean up.

    Raises:
        ArchiveError: When the merged body fails frontmatter or sections validation.
    """
    tmp = change_folder / "canonical-merged.tmp.md"
    tmp.write_text(merged_body, encoding="utf-8")
    try:
        _run_validator(
            lambda p: validate_frontmatter(p, kind="capability-spec"),
            tmp,
            "merged canonical frontmatter",
        )
        _run_validator(validate_capability_spec_sections, tmp, "merged canonical sections")
    except ArchiveError:
        tmp.unlink(missing_ok=True)
        raise
    tmp.unlink(missing_ok=True)


def _run_hook(
    hook: Callable[[Path], None],
    change_folder: Path,
    proposal_snapshot: Path,
    proposal_path: Path,
) -> None:
    """Run pre_archive_hook; restore proposal.md and re-raise on failure.

    Raises:
        ArchiveError: Wrapping the original hook exception after rollback.
    """
    try:
        hook(change_folder)
    except Exception as orig:
        proposal_snapshot.replace(proposal_path)
        raise ArchiveError(f"pre_archive_hook failed: {orig!r}") from orig


def _write_canonical(
    canonical_spec: Path,
    merged_body: str,
    proposal_snapshot: Path,
    proposal_path: Path,
) -> None:
    """Atomic-replace canonical SPEC.md; restore proposal.md on failure.

    Raises:
        ArchiveError: Wrapping the original exception after rollback.
    """
    try:
        atomic_replace(canonical_spec, merged_body)
    except Exception as orig:
        proposal_snapshot.replace(proposal_path)
        raise ArchiveError(f"atomic_replace of canonical spec failed: {orig!r}") from orig


def _move_to_archive(
    change_folder: Path,
    archive_target: Path,
    canonical_spec: Path,
    canonical_snapshot: Path,
    proposal_snapshot: Path,
    proposal_path: Path,
) -> None:
    """Move change folder to archive; restore canonical + proposal on failure.

    Raises:
        ArchiveError: Wrapping the original exception after rollback.
    """
    archive_root = archive_target.parent
    archive_root.mkdir(parents=True, exist_ok=True)
    try:
        shutil.move(str(change_folder), str(archive_target))
    except Exception as orig:
        shutil.copy2(canonical_snapshot, canonical_spec)
        proposal_snapshot.replace(proposal_path)
        shutil.rmtree(archive_target, ignore_errors=True)
        raise ArchiveError(f"archive move failed: {orig!r}") from orig


def _preflight_merge(
    repo_root: Path,
    change_id: str,
    capability: str,
) -> tuple[Path, Path, Path, Path]:
    """Run all preflight checks for merge_delta_proposal.

    Returns:
        (change_folder, proposal_path, canonical_spec, archive_target)

    Raises:
        ArchiveError: On any preflight violation.
    """
    _validate_change_id(change_id)
    _validate_capability(capability)

    change_folder = repo_root / ".forge" / "changes" / change_id
    proposal_path = change_folder / "proposal.md"

    if not proposal_path.is_file():
        raise ArchiveError(f"proposal not found: {proposal_path}")

    fm = _read_proposal_frontmatter(proposal_path)

    status = fm.get("status")
    if status != "approved":
        raise ArchiveError(
            f"proposal status must be 'approved' to merge, got {status!r}: {proposal_path}"
        )

    affects = fm.get("affects_capability")
    if affects != capability:
        raise ArchiveError(
            f"proposal affects_capability {affects!r} does not match capability arg {capability!r}"
        )

    canonical_spec = repo_root / ".forge" / "specs" / capability / "SPEC.md"
    if not canonical_spec.is_file():
        raise ArchiveError(f"canonical spec not found: {canonical_spec}")

    archive_target = repo_root / ".forge" / "changes" / "archive" / change_id
    if archive_target.exists():
        raise ArchiveError(f"archive already exists at {archive_target}")

    _run_validator(validate_delta, proposal_path, "delta proposal")

    return change_folder, proposal_path, canonical_spec, archive_target


def merge_delta_proposal(
    repo_root: Path,
    change_id: str,
    capability: str,
    pre_archive_hook: Callable[[Path], None] | None = None,
) -> tuple[Path, Path]:
    """Atomically merge a delta proposal into the canonical capability spec and archive it.

    Transactional, all-or-nothing.  Includes proposal.md status flip in the
    transaction — any mid-flight failure restores both the canonical spec and
    the proposal via pre-committed snapshot files.

    Preflight (all checked before any mutation):
        1. Validate ``change_id`` against the strict change-id regex.
        2. Validate ``capability`` slug.
        3. ``proposal.md`` exists at ``.forge/changes/<change_id>/proposal.md``.
        4. Frontmatter ``status == "approved"``.
        5. Frontmatter ``affects_capability == capability`` arg.
        6. ``.forge/specs/<capability>/SPEC.md`` exists.
        7. ``.forge/changes/archive/<change_id>/`` does NOT exist.
        8. ``validate_delta(proposal_path)`` returns no BLOCK/HIGH findings.

    Mutation order (locked per plan D-7):
        1. Snapshot canonical SPEC.md to ``canonical-pre.md`` in change folder.
        2. Snapshot proposal.md to ``proposal-pre.md`` in change folder.
        3. Apply ops via ``parse_proposal_body`` + ``apply_delta_ops`` in memory.
        4. Validate merged body (write to ``canonical-merged.tmp.md``, run
           ``validate_frontmatter`` + ``validate_capability_spec_sections``).
           Any BLOCK/HIGH → discard merge; canonical untouched; raise.
        5. Run ``pre_archive_hook(change_folder)`` if provided.  Hook failure →
           restore proposal.md; re-raise wrapped as ``ArchiveError``.
        6. Atomic-replace canonical SPEC.md via ``atomic_replace``.  Failure →
           restore proposal.md; re-raise wrapped as ``ArchiveError``.
        7. Move change folder via ``shutil.move`` to archive.  Failure →
           restore canonical + proposal.md; clean partial archive; re-raise.

    Args:
        repo_root: Repository root containing the ``.forge/`` tree.
        change_id: Change folder name (YYYY-MM-DD-slug form, schema-aligned slug).
        capability: Capability slug (lowercase letters, digits, hyphens).
        pre_archive_hook: Optional callback run after merged-body validation and
            before the archive move, given the live ``change_folder`` path.  The
            default hook in T6 (``_mark_change_merged_hook``) flips proposal.md
            ``status: approved`` to ``merged``.  Any exception is wrapped in
            ``ArchiveError`` after proposal.md is restored from its snapshot.

    Returns:
        ``(canonical_spec_path, archive_path)`` on success.  Snapshot files
        (``canonical-pre.md``, ``proposal-pre.md``) ride into the archive
        folder for forensics and future ``/forge:undo`` (M4).

    Raises:
        ArchiveError: On any preflight or mutation failure.  Hook exceptions are
            wrapped in ``ArchiveError`` after rollback.
    """
    # Advisory file lock — fail fast if a concurrent merge is already in flight
    # for the same change_id.  We derive the proposal path here (pure path math)
    # so the lock is held for the entire transaction.  _preflight_merge repeats
    # the is_file() check; a missing file fails there with a clear message.
    _lock_path = repo_root / ".forge" / "changes" / change_id / "proposal.md"
    try:
        _lock_fh = _lock_path.open("rb")
    except OSError:
        _lock_fh = None  # path doesn't exist yet; _preflight_merge will surface this

    if _lock_fh is not None:
        try:
            fcntl.flock(_lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            _lock_fh.close()
            raise ArchiveError(f"another merge is in flight for change_id {change_id!r}") from exc

    try:
        change_folder, proposal_path, canonical_spec, archive_target = _preflight_merge(
            repo_root, change_id, capability
        )

        # Steps 1 + 2: snapshots
        canonical_snapshot = change_folder / "canonical-pre.md"
        proposal_snapshot = change_folder / "proposal-pre.md"
        shutil.copy2(canonical_spec, canonical_snapshot)
        shutil.copy2(proposal_path, proposal_snapshot)

        # Step 3: apply ops in memory
        proposal_text = proposal_path.read_text(encoding="utf-8")
        canonical_text = canonical_spec.read_text(encoding="utf-8")
        ops = parse_proposal_body(proposal_text)
        merged_body = apply_delta_ops(canonical_text, ops)

        # Step 4: validate merged body
        _validate_merged_body(change_folder, merged_body)

        # Step 5: pre_archive_hook
        if pre_archive_hook is not None:
            _run_hook(pre_archive_hook, change_folder, proposal_snapshot, proposal_path)

        # Step 6: atomic-replace canonical SPEC.md
        _write_canonical(canonical_spec, merged_body, proposal_snapshot, proposal_path)

        # Step 7: move change folder to archive
        _move_to_archive(
            change_folder,
            archive_target,
            canonical_spec,
            canonical_snapshot,
            proposal_snapshot,
            proposal_path,
        )
        return canonical_spec, archive_target
    finally:
        if _lock_fh is not None:
            _lock_fh.close()
