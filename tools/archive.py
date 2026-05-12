"""Feature folder archival + canonical capability spec writes.

These are pure file operations with explicit failure modes; the Python layer
owns the path math so the LLM cannot misplace shipped artifacts.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import sys
import tempfile
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Final

import yaml

from tools.constitution_amend import atomic_replace
from tools.delta_merge import apply_delta_ops, parse_proposal_body
from tools.state import (
    VALID_TIERS,
    _utc_now_iso,
    feature_folder_exists,
    write_state,
)
from tools.validate._feature_layout import _ORPHAN_FEATURE_FILES, _ORPHAN_SEED_PHASES
from tools.validate._finding import EXIT_NONZERO_SEVERITIES, Finding
from tools.validate.delta import validate_delta
from tools.validate.spec_structural import (
    validate_capability_spec_sections,
    validate_frontmatter,
)

# fcntl is POSIX-only; keep tools.archive importable on Windows by guarding it.
# The advisory lock in merge_delta_proposal is skipped when fcntl is unavailable;
# the rest of the module (ship_feature, slug_from_idea, etc.) works regardless.
fcntl: ModuleType | None
try:
    import fcntl
except ModuleNotFoundError:  # pragma: no cover - non-POSIX (Windows)
    fcntl = None

_FLOW_VERSION_V3 = 3
_CAPABILITY_RE = re.compile(r"^[a-z0-9-]+$")
# Schema-aligned slug: must start with alnum, at least 3 chars total, and
# never carry a trailing hyphen or two consecutive hyphens.
# Matches schemas/capability-spec-frontmatter.schema.json and
# delta-proposal-frontmatter.schema.json:affects_capability.
_CAPABILITY_SLUG_SCHEMA_RE = re.compile(r"^[a-z0-9](?:[a-z0-9]|-(?=[a-z0-9])){2,}$")
# Strict feature-id: ``YYYY-MM-DD`` + alnum-leading slug with no trailing
# hyphen and no consecutive hyphens. Month is constrained to ``01-12`` and
# day to ``01-31`` so impossible calendar segments are rejected here AND at
# the schema boundary. The schema pattern at
# ``schemas/state.schema.json#properties.feature_id.pattern`` mirrors this
# regex string for byte; the runtime guard in ``tools.state._FEATURE_ID_RE``
# mirrors it too.
_FEATURE_ID_RE = re.compile(
    r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])-[a-z0-9](?:[a-z0-9]|-(?=[a-z0-9]))+$"
)
# Schema-aligned change id: strict month/day, schema-aligned slug suffix.
_CHANGE_ID_RE = re.compile(
    r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])-[a-z0-9](?:[a-z0-9]|-(?=[a-z0-9])){2,}$"
)

# Minimum token length to survive the content-word filter (step 5 of slug_from_idea).
_SLUG_MIN_TOKEN_LEN: int = 2

# Filesystem name-length cap for the derived slug. NAME_MAX on the standard
# POSIX filesystems is 255 bytes; subtract the 11-byte ``YYYY-MM-DD-`` date
# prefix plus headroom for suffix-disambig variants to land at 200 bytes.
_SLUG_MAX_BYTES: int = 200

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
    # Normalize via NFKD + ascii-ignore so accented Latin (``café`` ->
    # ``cafe``) and German umlauts (``über`` -> ``uber``) collapse to their
    # base form before the existing ``[^a-z0-9 ]`` cleanup. Non-decomposable
    # characters (CJK, Devanagari, etc.) are stripped entirely; the existing
    # "all tokens filtered" empty-slug path catches that case downstream.
    normalized = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    lowered = normalized.lower()
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
    # Validate final slug matches the schema-aligned pattern.  Split the
    # empty-slug failure into two paths so the operator can tell whether the
    # input had no tokens at all (empty / whitespace) versus tokens that all
    # got filtered as stopwords or too-short.
    if not slug:
        if tokens:
            raise ArchiveError(
                f"slug computed from idea is empty (all tokens filtered as "
                f"stopwords or too short, min token length {_SLUG_MIN_TOKEN_LEN}): {text}"
            )
        raise ArchiveError(f"slug computed from idea is empty: {text}")
    if not _CAPABILITY_SLUG_SCHEMA_RE.fullmatch(slug):
        raise ArchiveError(f"slug computed from idea is too short: {slug} ({text})")
    # Filesystem-safety cap: most POSIX filesystems (ext4, btrfs, xfs) enforce
    # NAME_MAX = 255 bytes on a single path component. The feature folder
    # name is ``YYYY-MM-DD-<slug>`` (11 bytes of date prefix) and the seed
    # may add suffix-disambig text, so cap the slug at 200 bytes leaving a
    # comfortable margin. Pathological inputs (single 4000-char token with
    # no spaces; only filterable characters in a single long word) would
    # otherwise OSError mid-seed with "File name too long".
    if len(slug.encode("utf-8")) > _SLUG_MAX_BYTES:
        raise ArchiveError(
            f"slug computed from idea exceeds {_SLUG_MAX_BYTES}-byte filesystem cap "
            f"(got {len(slug.encode('utf-8'))} bytes); shorten the idea before /forge:do"
        )
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


# _ORPHAN_SEED_PHASES is sourced from tools/validate/_feature_layout.py so the
# orphan predicate stays in lock-step with health.py.  The set covers BOTH the
# refine-tier path (cleanup_orphan_feature) AND the focused/standard pre-seed
# path written by /forge:do (cleanup_seeded_feature).


def _orphan_conditions_met(folder: Path) -> bool:  # noqa: PLR0911
    """Return True when the folder satisfies all three orphan conditions.

    Conditions (all must hold):
      1. state.json.current_phase in {"refine", "spec"} AND
         phases[current_phase].status == "in_progress".
      2. state.json.commits == [].
      3. Folder contents are a strict subset of _ORPHAN_FEATURE_FILES.

    Generalized to cover both the refine-tier seed (used by
    cleanup_orphan_feature) and the focused/standard /forge:do pre-seed
    (used by cleanup_seeded_feature).  Does NOT raise on I/O
    failures — returns False so callers treat a broken state.json as a
    non-orphan (safe default).  The defensive predicate is intentionally a
    flat sequence of independent guards (PLR0911 silenced): each early-return
    marks a distinct precondition that must hold, and flattening to one final
    boolean would obscure which condition failed.
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

    # Condition 1: phase + status (refine|spec x in_progress).
    current_phase = payload.get("current_phase")
    if current_phase not in _ORPHAN_SEED_PHASES:
        return False
    phases = payload.get("phases")
    if not isinstance(phases, dict):
        return False
    phase_block = phases.get(current_phase)
    if not isinstance(phase_block, dict) or phase_block.get("status") != "in_progress":
        return False

    # Condition 2: commits is exactly an empty list.  Fail-closed against
    # malformed shapes — missing key, ``None``, ``""``, ``0``, ``False``, or
    # any non-list value all refuse cleanup.  The previous ``or []`` form
    # silently coerced these falsy values to ``[]`` and removed the folder
    # (external review finding: cleanup must fail-closed on malformed state).
    commits = payload.get("commits")
    if commits != []:
        return False

    # Condition 3: folder contents are a strict subset of _ORPHAN_FEATURE_FILES
    try:
        present = {p.name for p in folder.iterdir()}
    except OSError:
        return False
    return present.issubset(_ORPHAN_FEATURE_FILES)


def _cleanup_via_predicate(repo_root: Path, feature_id: str, *, log_label: str) -> bool:
    """Shared cleanup body for cleanup_orphan_feature and cleanup_seeded_feature.

    Both public entry points share the same generalized predicate, the same
    shutil.rmtree, and the same race-narrowing re-check.  Only the stderr
    WARN label differs so log lines name the actual call site.

    Args:
        repo_root: Repository root containing the ``.forge/`` tree.
        feature_id: Feature folder name in YYYY-MM-DD-slug form.  Caller must
            already have run ``_validate_feature_id`` — this helper does NOT
            re-validate.
        log_label: Prefix used in stderr WARN lines (e.g.
            ``"cleanup_orphan_feature"`` or ``"cleanup_seeded_feature"``).

    Returns:
        ``True`` on successful removal; ``False`` on any condition violation.
    """
    folder = repo_root / ".forge" / "features" / feature_id
    if not folder.is_dir():
        print(
            f"WARN: {log_label}: {feature_id!r} is not a directory — skipping",
            file=sys.stderr,
        )
        return False

    # Preflight check.
    if not _orphan_conditions_met(folder):
        print(
            f"WARN: {log_label}: {feature_id!r} does not meet orphan conditions "
            f"(preflight) — skipping",
            file=sys.stderr,
        )
        return False

    # Race-narrowing re-check immediately before rmtree.
    if not _orphan_conditions_met(folder):
        print(
            f"WARN: {log_label}: {feature_id!r} conditions changed before rmtree "
            f"— aborting to avoid data loss",
            file=sys.stderr,
        )
        return False

    shutil.rmtree(folder)
    return True


def cleanup_orphan_feature(repo_root: Path, feature_id: str) -> bool:
    """Remove an orphan feature folder that has never advanced past the initial seed.

    Validates ``feature_id`` slug, checks the generalized orphan conditions via
    a shared helper, re-checks them immediately before ``shutil.rmtree``
    (race-narrowing), then removes the folder.  All condition failures are
    logged to stderr (label ``cleanup_orphan_feature``) and return ``False``;
    only invalid ``feature_id`` raises ``ArchiveError``.

    Conditions (all three must hold):
      1. ``state.json.current_phase in {"refine", "spec"}`` AND
         ``phases[current_phase].status == "in_progress"``.
      2. ``state.json.commits == []``.
      3. Folder contents are a strict subset of
         ``_ORPHAN_FEATURE_FILES = {"state.json", "SPEC.md", "decisions.md"}``.

    Use ``cleanup_seeded_feature`` instead at the ``/forge:do`` integration
    point so log lines name the right call site; the predicate is identical.

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
    return _cleanup_via_predicate(repo_root, feature_id, log_label="cleanup_orphan_feature")


def cleanup_seeded_feature(repo_root: Path, feature_id: str) -> bool:
    """Remove a ``/forge:do`` pre-seed feature folder that has never advanced.

    Distinct call-site alias for :func:`cleanup_orphan_feature`.  Same
    generalized predicate (``refine|spec x in_progress`` + no commits + folder
    contents subset of ``_ORPHAN_FEATURE_FILES``), same race-narrowing
    recheck, same ``shutil.rmtree``, but stderr WARN messages name
    ``cleanup_seeded_feature`` so log lines point at the actual entry point
    used by the focused / standard tier seed-cancel cleanup path.

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
    return _cleanup_via_predicate(repo_root, feature_id, log_label="cleanup_seeded_feature")


_FEATURE_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates" / "feature"

# Skipped-phase marker written into seed state.json so health-validate flags
# ``research`` as intentionally skipped rather than reading the absent phase
# block as a missing-step regression.
_RESEARCH_SKIPPED_ENTRY: dict[str, str] = {
    "phase": "research",
    "reason": "research deferred; manual research acceptable",
}

# Seed-time entry phases accepted by ``create_feature_folder``.  ``"spec"`` is
# the focused/standard entry; ``"refine"`` is the full-tier entry; ``"research"``
# is the standard-tier opt-in entry (``/forge:do --standard --research``). Any
# other lifecycle phase value (e.g. ``"plan"``, ``"execute"``) is post-seed
# territory and is rejected here so /forge:do callers cannot accidentally create
# a folder mid-lifecycle.
_VALID_SEED_PHASES: frozenset[str] = frozenset({"spec", "refine", "research"})


_GITIGNORE_BEGIN: Final[str] = "# === BEGIN FORGE managed ==="
_GITIGNORE_END: Final[str] = "# === END FORGE managed ==="
_GITIGNORE_BODY: Final[tuple[str, ...]] = (
    ".forge/**/*.lock",
    ".forge/state/*.log",
)


def _ensure_target_gitignore_rules(repo_root: Path) -> None:
    """Append a managed `.gitignore` block to ``repo_root/.gitignore`` once.

    Downstream target repos that opt to track ``.forge/`` will otherwise commit
    ephemeral artifacts the runtime drops next to feature state: lockfiles
    (``state.json.lock``) from the advisory-lock helper, and any future log
    sidecars. This helper appends a clearly fenced FORGE-managed block listing
    those patterns the first time a feature is seeded.

    Idempotent: re-seeding is detected by presence of ``_GITIGNORE_BEGIN`` in
    the existing file. No-op when ``.gitignore`` does not exist (caller has
    chosen not to use git; mutating their working tree would be surprising).
    No-op when ``repo_root`` is the FORGE plugin install itself (already has
    its own ``/.forge/*`` rule covering every artifact in this tree).
    """
    gitignore = repo_root / ".gitignore"
    if not gitignore.is_file():
        return
    try:
        existing = gitignore.read_text(encoding="utf-8")
    except OSError:
        return
    if _GITIGNORE_BEGIN in existing:
        return
    if "/.forge/*" in existing:
        return
    suffix = "" if existing.endswith("\n") else "\n"
    block_lines = (
        _GITIGNORE_BEGIN,
        "# Auto-added on first feature seed. Edit freely between BEGIN/END.",
        *_GITIGNORE_BODY,
        _GITIGNORE_END,
        "",
    )
    with gitignore.open("a", encoding="utf-8") as fh:
        fh.write(suffix)
        fh.write("\n".join(block_lines))


def _render_seed_spec_md(template: str, *, feature_id: str, tier: str) -> str:
    """Substitute the four placeholders in templates/feature/SPEC.md.

    The template body uses literal placeholder strings; the substitution is a
    plain ``str.replace`` chain so callers cannot accidentally re-substitute a
    later token into an already-rendered span.

    Args:
        template: Raw text of ``templates/feature/SPEC.md``.
        feature_id: Validated YYYY-MM-DD-slug feature id.
        tier: Validated tier name.

    Returns:
        Rendered SPEC.md text with frontmatter ``id``/``tier``/``created``/
        ``capability`` populated from ``feature_id`` + ``tier``.
    """
    created = feature_id[:10]  # YYYY-MM-DD prefix
    slug = feature_id[11:]  # everything after the date prefix
    rendered = template
    rendered = rendered.replace("<YYYY-MM-DD-slug>", feature_id)
    rendered = rendered.replace("<focused|standard|full>", tier)
    rendered = rendered.replace("<YYYY-MM-DD>", created)
    rendered = rendered.replace("<stable-capability-handle>", slug)
    return rendered


def create_feature_folder(
    repo_root: Path,
    *,
    feature_id: str,
    tier: str,
    current_phase: str = "spec",
    schema_path: Path | None = None,
    include_research_skip: bool = True,
) -> Path:
    """Seed a fresh ``.forge/features/<feature_id>/`` folder for ``/forge:do``.

    Composes the three ``templates/feature/`` files (state.json, SPEC.md,
    decisions.md) into a new feature folder with substitutions for
    ``feature_id`` and ``tier``.  The ``current_phase`` keyword controls the
    seed entry: ``"spec"`` (focused/standard), ``"refine"`` (full-tier only),
    or ``"research"`` (standard-tier opt-in via
    ``/forge:do --standard --research`` and full-tier).  Any other lifecycle
    phase is post-seed territory and is refused.

    Per-file write is atomic via ``atomic_replace`` (tempfile +
    ``Path.replace`` on the same directory — POSIX-rename semantics).
    The multi-file folder seed is **best-effort, not transactional** — on any
    per-file write failure (or schema refusal from ``write_state``), the
    partial folder is removed via ``shutil.rmtree`` before the original
    exception is re-raised.

    The ``state.json`` body is built in memory and validated via
    ``tools.state.write_state(..., schema_path=schema_path)`` so an invalid
    seed payload refuses BEFORE the folder is left behind on disk.

    State body shape:

      - ``feature_id`` / ``tier`` (validated above)
      - ``current_phase`` (``"spec"``, ``"refine"``, or ``"research"``)
      - ``phases.<current_phase> = {"status": "in_progress", "started_at": <utc-iso>}``
      - ``skipped`` = ``[{"phase": "research", ...}]`` when
        ``include_research_skip`` is ``True`` (legacy default for features
        that never run research); otherwise ``[]``.
      - ``deviations = []``
      - ``commits = []``

    The ``routing`` block is **not** written here — ``record_routing_decision``
    writes it next as a separate validated step.

    Validation order (locked):
        1. ``feature_id`` slug format.
        2. ``tier`` membership in ``VALID_TIERS``.
        3. ``current_phase`` membership in ``{"spec", "refine", "research"}``.
        4. ``current_phase == "refine"`` ⇒ ``tier == "full"`` (refine is
           full-tier-only per the locked deep-followup decision).
        5. ``current_phase == "research"`` ⇒ ``tier in {"standard", "full"}``
           (research never runs on focused tier).
        6. Folder collision via ``feature_folder_exists``.

    Args:
        repo_root: Repository root containing the ``.forge/`` tree.
        feature_id: Feature folder name in YYYY-MM-DD-slug form.
        tier: One of ``VALID_TIERS`` (focused/standard/full).
        current_phase: Seed entry phase; one of
            ``{"spec", "refine", "research"}``.  Defaults to ``"spec"`` for
            the focused/standard entry.  ``"refine"`` opens the full-tier
            entry and additionally requires ``tier == "full"``.  ``"research"``
            opens the standard-with-opt-in or full-tier research entry and
            additionally requires ``tier in {"standard", "full"}``.
        schema_path: Optional path to ``schemas/state.schema.json``.  When
            given, ``write_state`` validates the seed payload before any disk
            mutation.
        include_research_skip: When ``True`` (default), seed
            ``skipped = [{"phase": "research", ...}]`` so health-validate
            recognises research as intentionally deferred (legacy behavior
            for features that never run research).  When ``False``, seed
            ``skipped = []`` — research is part of the effective phase list
            for this feature, so the deferral marker would lie about the
            actual lifecycle.

    Returns:
        Path to the new ``.forge/features/<feature_id>/`` folder.

    Raises:
        ArchiveError: ``feature_id`` slug malformed, ``tier`` not in
            ``VALID_TIERS``, ``current_phase`` outside the seed-phase set,
            tier/phase pairing violation, or the feature folder already
            exists.
        StateError: Seed payload fails schema validation when
            ``schema_path`` is given (folder rmtree'd before re-raise).
        OSError: Per-file write failure (folder rmtree'd before re-raise).
    """
    _validate_feature_id(feature_id)
    if tier not in VALID_TIERS:
        raise ArchiveError(f"invalid tier: {tier!r}; must be one of {VALID_TIERS}")
    if current_phase not in _VALID_SEED_PHASES:
        raise ArchiveError(
            f"invalid current_phase {current_phase!r}; "
            "must be one of {'spec', 'refine', 'research'}"
        )
    if current_phase == "refine" and tier != "full":
        raise ArchiveError(f"current_phase 'refine' requires tier 'full'; got tier {tier!r}")
    if current_phase == "research" and tier not in ("standard", "full"):
        raise ArchiveError(
            f"current_phase 'research' requires tier in {{'standard', 'full'}}; got tier {tier!r}"
        )
    if feature_folder_exists(repo_root, feature_id):
        raise ArchiveError(f"feature folder already exists: {feature_id!r}")

    _ensure_target_gitignore_rules(repo_root)

    folder = repo_root / ".forge" / "features" / feature_id
    # Wrap mkdir's FileExistsError as ArchiveError so callers see a
    # consistent exception type when a TOCTOU race fires (folder created
    # between the feature_folder_exists check above and this mkdir).
    try:
        folder.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise ArchiveError(
            f"feature folder already exists (race detected): {feature_id!r}"
        ) from exc

    try:
        # state.json — validated before any disk write via write_state.
        # ``include_research_skip`` controls the legacy ``skipped[research]``
        # deferral marker. When research is part of the effective phase list
        # for this feature (full tier always; standard with ``--research``),
        # the marker is suppressed so health-validate does not contradict the
        # actual lifecycle.
        skipped_seed: list[dict[str, str]] = (
            [dict(_RESEARCH_SKIPPED_ENTRY)] if include_research_skip else []
        )
        seed_state: dict[str, object] = {
            "feature_id": feature_id,
            "tier": tier,
            "current_phase": current_phase,
            "phases": {current_phase: {"status": "in_progress", "started_at": _utc_now_iso()}},
            "skipped": skipped_seed,
            "deviations": [],
            "commits": [],
        }
        write_state(folder / "state.json", seed_state, schema_path=schema_path)

        # SPEC.md — render template via four-placeholder substitution.
        spec_template = (_FEATURE_TEMPLATES_DIR / "SPEC.md").read_text(encoding="utf-8")
        spec_body = _render_seed_spec_md(spec_template, feature_id=feature_id, tier=tier)
        atomic_replace(folder / "SPEC.md", spec_body)

        # decisions.md — copy template byte-for-byte (no substitutions).
        decisions_body = (_FEATURE_TEMPLATES_DIR / "decisions.md").read_text(encoding="utf-8")
        atomic_replace(folder / "decisions.md", decisions_body)
    except BaseException:
        # Best-effort multi-file rollback.  Any failure mid-seed (schema
        # refusal, per-file OSError, KeyboardInterrupt) removes the partial
        # folder before re-raising so /forge:do callers never inherit a
        # half-rendered feature.  ``ignore_errors=True`` keeps the cleanup
        # itself from masking the original exception.
        shutil.rmtree(folder, ignore_errors=True)
        raise

    return folder


def _validate_capability(capability: str) -> None:
    if not _CAPABILITY_RE.fullmatch(capability):
        raise ArchiveError(
            f"invalid capability slug: {capability!r}; expected slug matching ^[a-z0-9][a-z0-9-]*$"
        )


def _validate_feature_id(feature_id: str) -> None:
    if not _FEATURE_ID_RE.fullmatch(feature_id):
        raise ArchiveError(
            f"invalid feature id: {feature_id!r}; "
            "expected 'YYYY-MM-DD-<slug>' (strict month/day, "
            "slug matching ^[a-z0-9][a-z0-9-]+$)"
        )


def _validate_change_id(change_id: str) -> None:
    if not _CHANGE_ID_RE.fullmatch(change_id):
        raise ArchiveError(
            f"invalid change id: {change_id!r}; "
            "expected 'YYYY-MM-DD-<slug>' (strict month/day, "
            "slug matching ^[a-z0-9][a-z0-9-]{2,}$)"
        )


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


def _read_flow_version(state_path: Path) -> int:
    """Return the integer ``flow_version`` recorded in state.json, defaulting to 1.

    Absence of the field is treated as ``1`` per the schema's documented
    application convention. Any read or parse failure returns ``1`` as well —
    the caller treats unreadable state as legacy v1 so the historical
    archive-at-ship behavior is preserved when state.json is malformed.
    """
    if not state_path.is_file():
        return 1
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return 1
    raw = payload.get("flow_version") if isinstance(payload, dict) else None
    if isinstance(raw, int) and not isinstance(raw, bool):
        return raw
    return 1


def archive_feature_after_qa(repo_root: Path, feature_id: str) -> Path:
    """Move a v3 feature folder to ``.forge/features/archive/<id>/`` once qa is done.

    The v3 lifecycle keeps the feature folder live in
    ``.forge/features/<id>/`` between ship and qa so post-merge
    ``/forge:qa --against merged`` can find it. This helper performs the
    deferred move once ``phases.qa.status == "done"``.

    Idempotency:
        When the source folder is already absent and the archive target
        exists, the call returns the archive path without raising. This
        matches retry-safety expectations of the qa skill, which calls this
        helper after ``complete_phase("qa")`` succeeds.

    Collision:
        When BOTH the source and the archive target exist, the call raises
        ``ArchiveError`` rather than clobbering either folder. The caller
        must reconcile manually.

    Args:
        repo_root: Repository root containing the ``.forge/`` tree.
        feature_id: Feature folder name in YYYY-MM-DD-slug form.

    Returns:
        Path to the archived feature folder.

    Raises:
        ArchiveError: ``feature_id`` slug malformed, the feature is not v3,
            ``phases.qa.status`` is not ``"done"``, both source and archive
            exist (collision), or the source is missing AND no archive
            entry exists.
    """
    _validate_feature_id(feature_id)
    source = repo_root / ".forge" / "features" / feature_id
    archive_root = repo_root / ".forge" / "features" / "archive"
    target = archive_root / feature_id

    # Idempotent fast-path: source already moved, target present.
    if not source.exists() and target.is_dir():
        return target

    if not source.is_dir():
        raise ArchiveError(f"feature folder not found: {source}")

    if target.exists():
        raise ArchiveError(
            f"archive collision for {feature_id!r}: both source {source} "
            f"and archive {target} exist; manual reconciliation required"
        )

    state_path = source / "state.json"
    if not state_path.is_file():
        raise ArchiveError(
            f"archive_feature_after_qa requires v3 feature with qa.status=done; "
            f"got missing state.json at {state_path}"
        )
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ArchiveError(
            f"archive_feature_after_qa: cannot parse state.json at {state_path}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise ArchiveError(
            f"archive_feature_after_qa: state.json at {state_path} is not a JSON object"
        )

    flow_version = payload.get("flow_version")
    if flow_version != _FLOW_VERSION_V3:
        raise ArchiveError(
            f"archive_feature_after_qa requires v3 feature with qa.status=done; "
            f"got flow_version={flow_version!r}"
        )

    phases = payload.get("phases")
    qa_status: object = None
    if isinstance(phases, dict):
        qa_block = phases.get("qa")
        if isinstance(qa_block, dict):
            qa_status = qa_block.get("status")
    if qa_status != "done":
        raise ArchiveError(
            f"archive_feature_after_qa requires v3 feature with qa.status=done; "
            f"got qa.status={qa_status!r}"
        )

    archive_root.mkdir(parents=True, exist_ok=True)
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

    Lifecycle by ``state.json.flow_version``:
        - **Absent or ``< 3`` (legacy v1/v2):** archives the feature folder at
          ship time. The returned tuple's second element is the archive path
          ``.forge/features/archive/<id>/``.
        - **``3`` (live-until-qa):** the feature folder is left in place under
          ``.forge/features/<id>/`` so post-merge ``/forge:qa --against merged``
          can resolve it. The deferred folder move is performed by
          :func:`archive_feature_after_qa` once qa completes. The returned
          tuple's second element is the still-live source path.

        Canonical spec publishing runs for both versions — only the
        feature-folder move differs.

    Preflight (all-or-nothing):
        1. Validate `feature_id` slug.
        2. Validate `capability` slug.
        3. Source `.forge/features/<feature_id>/` must be a directory.
        4. Canonical `.forge/specs/<capability>/SPEC.md` must NOT exist.
        5. Archive target `.forge/features/archive/<feature_id>/` must NOT
           exist (legacy v1/v2 only — v3 does not consult this path at ship).

    Mutation:
        1. Write canonical spec via `write_canonical_spec`.
        2. Run ``pre_archive_hook(source)`` against the still-live feature folder
           if provided. Use this to mutate ``state.json`` (e.g., mark
           ``current_phase: done``) so the live state and the archived copy
           agree. Hook failure rolls back the canonical write before re-raising.
        3. **v1/v2 only:** move feature folder via `archive_feature`. v3
           skips this step; the folder is archived later by
           ``archive_feature_after_qa``.
        4. On move failure (v1/v2), delete the canonical spec file (and its
           parent dir if it was newly created and is now empty), then re-raise.

    Args:
        repo_root: Repository root containing the .forge/ tree.
        feature_id: Feature folder name in YYYY-MM-DD-slug form.
        capability: Capability slug (lowercase letters, digits, hyphens).
        body: Full SPEC.md text content (frontmatter + body).
        pre_archive_hook: Optional callback that runs after the canonical write
            and before the archive move, given the live ``source`` path. Any
            exception is treated as ship failure and triggers canonical rollback.

    Returns:
        Tuple of (canonical_spec_path, archive_path) on success. For v3
        features ``archive_path`` is the still-live source folder (the
        deferred move runs at qa).

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

    flow_version = _read_flow_version(source / "state.json")
    defer_archive = flow_version >= _FLOW_VERSION_V3

    archive_target = repo_root / ".forge" / "features" / "archive" / feature_id
    if not defer_archive and archive_target.exists():
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

    if defer_archive:
        # v3 lifecycle: feature folder stays under .forge/features/<id>/
        # until /forge:qa --against merged completes. The deferred move is
        # performed by archive_feature_after_qa.
        return canonical, source

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
    archive_target_early = repo_root / ".forge" / "changes" / "archive" / change_id

    if not proposal_path.is_file():
        if archive_target_early.is_dir():
            raise ArchiveError(
                f"change {change_id!r} was already merged; see archive at {archive_target_early}"
            )
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

    Default hook:
        When ``pre_archive_hook`` is ``None``, ``_mark_change_merged_hook`` is
        wired automatically once preflight passes.  This guarantees that every
        archived ``proposal.md`` carries ``status: merged`` — without it, a
        caller that omits the hook would archive a stale ``status: approved``
        proposal that ``validate_health`` cannot see (it skips
        ``.forge/changes/archive``).

    Snapshot orphans:
        On a step-4 (validator) rollback, the snapshot files written in steps
        1-2 stay in the live change folder — the canonical and proposal are
        already byte-identical to their pre-call state, so the snapshots are
        no-ops but visible.  A subsequent retry overwrites them via
        ``shutil.copy2`` (idempotent).  Safe to delete manually if a chain of
        retries fails.

    Raises:
        ArchiveError: On any preflight or mutation failure.  Hook exceptions are
            wrapped in ``ArchiveError`` after rollback.

    Platform note:
        Uses ``fcntl.flock`` for an advisory file lock — POSIX only.  The
        ``fcntl`` import is deferred to keep the rest of ``tools.archive``
        importable on Windows.
    """
    # Validate slugs FIRST — before any path math touches user-controlled
    # change_id / capability.  Joining an unvalidated slug into a Path is the
    # documented anti-pattern (coding-guidance-python "First-tier bug-causers");
    # a change_id like "../../etc" would otherwise let a read-mode open()
    # target a file outside the repo before preflight rejects it.
    _validate_change_id(change_id)
    _validate_capability(capability)

    # Advisory file lock — fail fast if a concurrent merge is already in flight
    # for the same change_id.  Lock held for the entire transaction.
    # When proposal.md does not yet exist, the open fails with OSError, the
    # lock is silently skipped, and _preflight_merge surfaces "proposal not
    # found" — two concurrent callers in this state both abort at preflight,
    # so no actual race opens.  When fcntl is unavailable (Windows), the lock
    # is skipped entirely and the function relies on filesystem-level
    # preflight + atomic-replace ordering for safety.
    _lock_fh = None
    if fcntl is not None:
        _lock_path = repo_root / ".forge" / "changes" / change_id / "proposal.md"
        try:
            _lock_fh = _lock_path.open("rb")
        except OSError:
            _lock_fh = None

        if _lock_fh is not None:
            try:
                fcntl.flock(_lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                _lock_fh.close()
                raise ArchiveError(
                    f"another merge is in flight for change_id {change_id!r}"
                ) from exc

    try:
        change_folder, proposal_path, canonical_spec, archive_target = _preflight_merge(
            repo_root, change_id, capability
        )

        # Default hook — wire after preflight so proposal_path is guaranteed
        # to exist.  Guarantees every archived proposal carries status: merged.
        if pre_archive_hook is None:
            pre_archive_hook = _mark_change_merged_hook(proposal_path)

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


# ---------------------------------------------------------------------------
# Repo-wide glossary promotion
# ---------------------------------------------------------------------------


_REPO_GLOSSARY_HEADER = (
    "# Repo-Wide Domain Glossary\n\n"
    "> Auto-generated by /forge:ship --promote-domain (post-ship advisory). Do not edit by hand.\n\n"
)
_REPO_GLOSSARY_TABLE_HEADER = "| Term | Definition | Source feature(s) |\n|---|---|---|\n"
# A glossary table row needs at least Term, Definition, Source feature(s)
# (3 cells once leading/trailing pipes are stripped).
_REPO_GLOSSARY_REQUIRED_COLUMNS = 3
# Feature DOMAIN.md glossary rows have 4 columns: Term, Definition, Context, Invariants.
_FEATURE_GLOSSARY_REQUIRED_COLUMNS = 4
_GLOSSARY_BLOCK_RE = re.compile(r"(?ms)^# Glossary\b[^\n]*\n(?P<body>.*?)(?=^# |\Z)")
_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*[:\-]+\s*(?:\|\s*[:\-]+\s*)+\|?\s*$")
_CONTEXT_ANNOTATION_RE = re.compile(r"^\[(?P<term>[^\]]+)\]\(context:\s*(?P<ctx>[^)]+)\)$")


@dataclass(frozen=True)
class ConflictRow:
    """A glossary term whose feature definition diverges from the repo-wide one."""

    term: str
    feature_definition: str
    repo_definition: str


@dataclass(frozen=True)
class PromotionResult:
    """Outcome of ``promote_domain_to_repo``.

    Attributes:
        status: ``"ok"`` when promotion completed (with or without
            individual skipped terms), ``"skipped"`` when at least one
            conflict was detected and the on-disk glossary was left
            untouched. Conflicts are advisory per the locked plan
            (``docs/plans/2026-05-08-m7-confidence-and-ux-polish.md`` P1.7)
            — never raises ``ArchiveError`` on diverging definitions.
        promoted_terms: Terms newly written into the repo-wide glossary.
            Empty when ``status == "skipped"``.
        skipped_terms: Terms already present with an identical (normalized,
            case-insensitive) definition; the source-features cell may have
            been extended. Empty when ``status == "skipped"``.
        conflicts: Populated when one or more feature terms diverge from the
            repo-wide glossary. Caller surfaces these as a non-blocking
            advisory in the ship summary.
    """

    status: str
    promoted_terms: list[str]
    skipped_terms: list[str]
    conflicts: list[ConflictRow]


def _split_table_row(line: str) -> list[str] | None:
    stripped = line.strip()
    if not stripped.startswith("|"):
        return None
    inner = stripped.strip("|")
    return [cell.strip() for cell in inner.split("|")]


def _normalize_definition(text: str) -> str:
    """Return a case- and whitespace-insensitive form for definition compare.

    Two definitions are treated as identical when their normalized forms
    match. Uses ``str.casefold`` so the rule is symmetric across the whole
    string — the previous "lower first char only" form silently treated
    mid-string case differences as conflicts while ignoring leading-letter
    case (asymmetric and surprising).

    The parser intentionally duplicates a thin slice of the
    ``tools.validate.domain_glossary`` parsing approach so the public
    validator surface stays untouched here; the two parsers will be
    consolidated when a shared helper is extracted.
    """
    return " ".join(text.split()).casefold()


def _normalize_term(term: str) -> str:
    """Return a case-insensitive key for term lookups.

    Both the validator (``tools.validate.domain_glossary._duplicate_findings``)
    and the promotion path key terms by lowercase to match the documented
    duplicate-detection contract. Keeping these aligned means a feature row
    ``order`` and a repo row ``Order`` collide as expected (skip or conflict
    depending on definitions) instead of silently writing two rows.
    """
    return term.strip().casefold()


def _parse_term_cell(cell: str) -> str:
    match = _CONTEXT_ANNOTATION_RE.match(cell.strip())
    if match is None:
        return cell.strip()
    return match.group("term").strip()


def _parse_feature_glossary(domain_text: str) -> list[tuple[str, str]]:
    """Return ``(term, definition)`` rows from a feature DOMAIN.md."""
    block = _GLOSSARY_BLOCK_RE.search(domain_text)
    if block is None:
        return []
    rows: list[tuple[str, str]] = []
    for line in block.group("body").splitlines():
        if not line.strip().startswith("|"):
            continue
        if _TABLE_SEPARATOR_RE.match(line):
            continue
        cells = _split_table_row(line)
        if cells is None:
            continue
        if len(cells) < _FEATURE_GLOSSARY_REQUIRED_COLUMNS:
            continue
        if cells[0].lower() == "term":
            continue
        term = _parse_term_cell(cells[0])
        if not term:
            continue
        definition = cells[1].strip()
        rows.append((term, definition))
    return rows


def _parse_repo_glossary(text: str) -> dict[str, tuple[str, str, str]]:
    """Parse repo glossary file body.

    Returns a mapping keyed by ``_normalize_term(term)`` — the key is
    case-insensitive so a hand-edited ``Order`` row collides with a
    feature-promoted ``order`` row instead of silently producing two
    canonical entries.

    Each value is ``(display_term, definition, sources_cell)`` so the
    on-disk display spelling survives a re-render.
    """
    out: dict[str, tuple[str, str, str]] = {}
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            continue
        if _TABLE_SEPARATOR_RE.match(line):
            continue
        cells = _split_table_row(line)
        if cells is None:
            continue
        if len(cells) < _REPO_GLOSSARY_REQUIRED_COLUMNS:
            continue
        if cells[0].lower() == "term":
            continue
        # Peel any [term](context: ctx) annotation in case a hand edit
        # crept one in; the writer never emits this shape but a defensive
        # peel keeps the case-insensitive key stable across re-runs.
        display_term = _parse_term_cell(cells[0])
        if not display_term:
            continue
        definition = cells[1].strip()
        sources = cells[2].strip()
        out[_normalize_term(display_term)] = (display_term, definition, sources)
    return out


def _merge_sources(existing: str, feature_id: str) -> str:
    sources = [s.strip() for s in existing.split(",") if s.strip()]
    if feature_id not in sources:
        sources.append(feature_id)
    return ", ".join(sources)


def _render_repo_glossary(rows: dict[str, tuple[str, str, str]]) -> str:
    """Render the repo-wide glossary file body.

    Args:
        rows: ``{normalized_key: (display_term, definition, sources_cell)}``
            map. Output rows are sorted by the case-insensitive normalized
            key so re-runs against a stable input are byte-identical.
    """
    lines: list[str] = []
    for key in sorted(rows):
        display_term, definition, sources = rows[key]
        lines.append(f"| {display_term} | {definition} | {sources} |")
    table = _REPO_GLOSSARY_TABLE_HEADER + "\n".join(lines)
    if lines:
        table += "\n"
    return _REPO_GLOSSARY_HEADER + table


def _atomic_write_glossary(target: Path, body: str) -> None:
    """Write ``body`` to ``target`` via a uniquely-named sibling tempfile.

    Mirrors the contract of ``tools.constitution_amend.atomic_replace`` but
    routes through ``os.replace`` so the test suite can simulate a
    mid-flight rename failure without monkey-patching ``Path.replace``.

    The temp filename is generated with :func:`tempfile.mkstemp` (per-call
    unique suffix) so two concurrent writers cannot stomp the same staging
    path. Without uniqueness, writer B's partial content could be replaced
    over the live target by writer A's ``os.replace``.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_str = tempfile.mkstemp(
        dir=str(target.parent),
        prefix=target.name + ".",
        suffix=".tmp",
    )
    os.close(fd)
    tmp = Path(tmp_str)
    try:
        tmp.write_text(body, encoding="utf-8")
        # Routed through ``os.replace`` (not ``Path.replace``) so the test
        # suite can simulate a mid-flight rename failure via patch.object.
        os.replace(str(tmp), str(target))  # noqa: PTH105
    except OSError:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()
        raise


def promote_domain_to_repo(
    repo_root: Path,
    feature_id: str,
    *,
    feature_dir: Path | None = None,
) -> PromotionResult:
    """Merge a feature's ``DOMAIN.md`` glossary into the repo-wide glossary.

    Conflicts (duplicate term with diverging normalized definition) are
    advisory: this function returns a ``PromotionResult`` with
    ``status="skipped"`` and ``conflicts`` populated, leaving the on-disk
    glossary untouched. Per the locked plan
    (``docs/plans/2026-05-08-m7-confidence-and-ux-polish.md`` P1.7), a
    conflict MUST NOT block ship.

    Term comparison is case-insensitive (``Order`` and ``order`` collapse to
    the same key) so the repo-wide glossary cannot grow two rows for the
    same canonical term across feature ships. Display spelling is
    preserved from the first row that wrote the term.

    Args:
        repo_root: Repository root containing the ``.forge/`` tree.
        feature_id: Feature folder name in YYYY-MM-DD-slug form. Validated
            against ``_FEATURE_ID_RE`` BEFORE any path math — guards against
            traversal via ``feature_id="../foo"``.
        feature_dir: Optional explicit path to the feature folder. When
            ``None`` (default), reads from the live folder at
            ``.forge/features/<feature_id>/DOMAIN.md``. Callers running
            promotion AFTER ``ship_feature`` succeeds pass the archived
            path: ``.forge/features/archive/<feature_id>/DOMAIN.md``.

    Returns:
        ``PromotionResult`` with ``status="ok"`` on a clean merge, or
        ``status="skipped"`` when one or more conflicts were detected. In
        the skipped case ``promoted_terms`` and ``skipped_terms`` are
        empty and ``conflicts`` lists every diverging term.

    Raises:
        ArchiveError: ``feature_id`` slug malformed, or feature ``DOMAIN.md``
            missing. Conflicts do NOT raise.
        OSError: Re-raised when the atomic rename step fails. The
            pre-existing glossary file (if any) is preserved.
    """
    _validate_feature_id(feature_id)
    if feature_dir is None:
        feature_dir = repo_root / ".forge" / "features" / feature_id
    domain_path = feature_dir / "DOMAIN.md"
    if not domain_path.is_file():
        raise ArchiveError(f"DOMAIN.md missing for feature {feature_id!r}")

    feature_rows = _parse_feature_glossary(domain_path.read_text(encoding="utf-8"))

    glossary_path = repo_root / ".forge" / "domain" / "glossary.md"
    repo_rows: dict[str, tuple[str, str, str]] = {}
    if glossary_path.is_file():
        repo_rows = _parse_repo_glossary(glossary_path.read_text(encoding="utf-8"))

    promoted: list[str] = []
    skipped: list[str] = []
    conflicts: list[ConflictRow] = []
    next_rows: dict[str, tuple[str, str, str]] = dict(repo_rows)

    for term, definition in feature_rows:
        key = _normalize_term(term)
        if key not in repo_rows:
            next_rows[key] = (term, definition, feature_id)
            promoted.append(term)
            continue
        existing_display, existing_definition, existing_sources = repo_rows[key]
        if _normalize_definition(definition) == _normalize_definition(existing_definition):
            next_rows[key] = (
                existing_display,
                existing_definition,
                _merge_sources(existing_sources, feature_id),
            )
            skipped.append(term)
            continue
        conflicts.append(
            ConflictRow(
                term=existing_display,
                feature_definition=definition,
                repo_definition=existing_definition,
            ),
        )

    if conflicts:
        # Advisory — never block ship. The on-disk glossary is left
        # untouched so a partial merge cannot persist alongside the
        # conflict report.
        return PromotionResult(
            status="skipped",
            promoted_terms=[],
            skipped_terms=[],
            conflicts=conflicts,
        )

    body = _render_repo_glossary(next_rows)
    _atomic_write_glossary(glossary_path, body)

    return PromotionResult(
        status="ok",
        promoted_terms=promoted,
        skipped_terms=skipped,
        conflicts=[],
    )
