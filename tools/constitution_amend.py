"""Atomic Constitution edit + semver classifier.

Bump rules per M3 spec D-12:
    patch  — clarification (text edit only; same article count + same level set)
    minor  — add article OR loosen level (CRITICAL→SHOULD, SHOULD→MAY)
    major  — remove article OR tighten level (SHOULD→CRITICAL, MAY→SHOULD)

A change that triggers more than one rule uses the strongest applicable
bump (major > minor > patch).
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Literal

from tools import redaction
from tools._glob import globstar_match
from tools.constitution import (
    MAX_INJECTED_WORDS,
    Article,
    ConstitutionError,
    parse_constitution_text,
)
from tools.validate._finding import EXIT_NONZERO_SEVERITIES
from tools.validate.constitution import validate_constitution
from tools.validate.conventions import Convention, load_conventions


class AmendError(RuntimeError):
    """Raised when the amend lifecycle cannot complete."""


_LEVEL_RANK = {"CRITICAL": 3, "SHOULD": 2, "MAY": 1}


# Signal collection bounds. Underscore-prefixed because they are tuning knobs
# for one function rather than part of the module's public dispatch contract.
_PER_FILE_CAP_BYTES = 16384
_TOTAL_CAP_BYTES = 81920
_MAX_SIGNAL_FILES = 8
_TRUNCATION_MARKER = f"\n--- truncated at {_PER_FILE_CAP_BYTES} bytes ---\n"
# Defense-in-depth. The hardcoded _MANIFEST_NAMES / _DOC_NAMES candidate list
# never contains a name that matches these globs today, so the deny-glob
# branch in collect_bootstrap_signals is unreachable under default config.
# Keep both the globs and the branch so a future maintainer adding a
# sensitive-named candidate (e.g. ".env.example", "deploy.pem") gets the
# filter for free — the regression test forces the branch via monkeypatch so
# nobody prunes it for "dead code".
_DENY_GLOBS: tuple[str, ...] = (".env*", "*.pem", "*.key", "id_rsa*")
_MANIFEST_NAMES: tuple[str, ...] = (
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "Gemfile",
    "pom.xml",
    "build.gradle",
    "mix.exs",
    "composer.json",
)
_DOC_NAMES: tuple[str, ...] = ("AGENTS.md", "CLAUDE.md", "README.md")

# Content-level secret regex: triggered when a key-shaped label sits next to a
# value-shaped token long enough to look like a credential. Used as a fallback
# alongside ``tools.redaction.filter`` so unconfigured deny_regex still catches
# obvious leaks in bootstrap signals.
_SECRET_CONTENT_RE = re.compile(
    r"(?i)(api[_-]?key|secret|password|token)\s*[:=]\s*['\"]?[A-Za-z0-9+/=_-]{12,}",
)


@dataclass(frozen=True, kw_only=True)
class SignalFile:
    """One collected signal file: repo-relative POSIX path + bounded contents."""

    relative_path: PurePosixPath
    body: str
    truncated: bool


@dataclass(frozen=True, kw_only=True)
class BootstrapSignals:
    """Pure-data result of :func:`collect_bootstrap_signals`.

    ``dropped`` carries every path that was rejected before reaching the
    ``files`` list, regardless of cause — both deny-glob matches (sensitive
    name shape like ``.env``, ``id_rsa``) and secret-content rejections
    (the body contained a credential-shaped substring). Callers that need
    to distinguish the two causes can re-scan paths via the documented
    deny-glob set, but the public surface treats them uniformly: a dropped
    path is one that did NOT make it into the bootstrap payload.

    Attributes:
        files: Files that survived all filters, in priority order.
        dropped: Paths skipped or removed by either the deny-glob filter
            or the secret-content scan.
        truncated: Paths whose body was capped at the per-file byte limit.
        total_bytes: Total UTF-8 byte count across :attr:`files`.
    """

    files: list[SignalFile]
    dropped: list[PurePosixPath]
    truncated: list[PurePosixPath]
    total_bytes: int

    @property
    def dropped_for_secrets(self) -> list[PurePosixPath]:
        """Back-compat alias for :attr:`dropped`.

        Kept so existing tests / skill orchestrators continue to read the
        old field name. Prefer :attr:`dropped` in new code — the rename
        reflects that this list also includes deny-glob rejections, not
        only secret-content rejections.
        """
        return self.dropped


def _name_matches_deny_glob(name: str) -> bool:
    """True iff ``name`` matches any path-level deny glob."""
    return any(globstar_match(name, g) for g in _DENY_GLOBS)


def _candidate_paths(repo_root: Path, *, names: tuple[str, ...]) -> list[Path]:
    """Return existing candidate file paths in priority order, first-match per name.

    ``names`` is the parameterized name list — caller decides whether the
    full bootstrap set (manifests + docs) or the narrower resync set (docs
    only) drives the walk. ``*.csproj`` is included only when at least one
    manifest name is requested (the glob is part of the manifest sweep).
    """
    candidates: list[Path] = []
    seen: set[Path] = set()
    for name in names:
        if name not in _MANIFEST_NAMES:
            continue
        path = repo_root / name
        if path.is_file() and path not in seen:
            candidates.append(path)
            seen.add(path)
    # ``*.csproj`` is glob-matched (non-recursive, repo root only); first sorted name.
    # Only sweep when manifests are part of the requested name set — the resync
    # signal collector restricts to docs and must skip the csproj glob.
    if any(n in _MANIFEST_NAMES for n in names):
        csproj_hits = sorted(repo_root.glob("*.csproj"))
        for path in csproj_hits:
            if path.is_file() and path not in seen:
                candidates.append(path)
                seen.add(path)
                break  # only the first sorted match
    for name in names:
        if name not in _DOC_NAMES:
            continue
        path = repo_root / name
        if path.is_file() and path not in seen:
            candidates.append(path)
            seen.add(path)
    return candidates


def _read_and_truncate(path: Path) -> tuple[str, bool]:
    """Read ``path`` up to the per-file byte cap, decode UTF-8.

    Bounded I/O: opens the file in binary mode and reads at most
    ``_PER_FILE_CAP_BYTES + 1`` bytes — never the whole file. The +1
    sentinel byte lets us distinguish "exactly at cap" (no truncation
    marker) from "over cap" (marker appended). A multi-GB README cannot
    inflate memory or stall the bootstrap.

    Returns ``(body, truncated)``. ``body`` ends with the truncation
    marker when the source exceeded the cap.
    """
    with path.open("rb") as fh:
        head = fh.read(_PER_FILE_CAP_BYTES + 1)
    if len(head) > _PER_FILE_CAP_BYTES:
        capped = head[:_PER_FILE_CAP_BYTES]
        return capped.decode("utf-8", errors="replace") + _TRUNCATION_MARKER, True
    return head.decode("utf-8", errors="replace"), False


def _looks_like_secret(body: str) -> bool:
    """Run body through ``tools.redaction.filter`` then a focused regex fallback.

    ``redaction.filter`` honors any caller-configured ``deny_regex`` /
    ``fatal_regex``; the default config carries empty regex lists, so the
    fallback below is what fires when no project config has been threaded in.
    """
    result = redaction.filter(
        redaction.PromptPayload(text=body, files=()),
        redaction.RedactionConfig(),
    )
    if result.had_denials or result.fatal_matches:
        return True
    return bool(_SECRET_CONTENT_RE.search(body))


def _collect_signals(repo_root: Path, *, names: tuple[str, ...]) -> BootstrapSignals:
    """Shared engine: walk candidate files under ``names`` with the bootstrap bounds.

    Encapsulates the iteration, per-file size cap, truncation marker, total
    payload cap, deny-glob filter, secret-content scan, and the
    ``_MAX_SIGNAL_FILES`` cap. ``names`` is the parameterized candidate-set
    selector — :func:`collect_bootstrap_signals` passes manifests + docs,
    :func:`collect_resync_signals` passes docs only.
    """
    if not repo_root.is_dir():
        raise AmendError(f"repo_root not found or not a directory: {repo_root}")

    files: list[SignalFile] = []
    dropped: list[PurePosixPath] = []
    truncated_paths: list[PurePosixPath] = []
    total_bytes = 0

    for path in _candidate_paths(repo_root, names=names):
        if len(files) >= _MAX_SIGNAL_FILES:
            break
        rel = PurePosixPath(path.relative_to(repo_root).as_posix())

        if _name_matches_deny_glob(rel.name):
            dropped.append(rel)
            continue

        body, was_truncated = _read_and_truncate(path)

        if _looks_like_secret(body):
            dropped.append(rel)
            continue

        body_bytes = len(body.encode("utf-8"))
        if total_bytes + body_bytes > _TOTAL_CAP_BYTES:
            break

        files.append(SignalFile(relative_path=rel, body=body, truncated=was_truncated))
        if was_truncated:
            truncated_paths.append(rel)
        total_bytes += body_bytes

    return BootstrapSignals(
        files=files,
        dropped=dropped,
        truncated=truncated_paths,
        total_bytes=total_bytes,
    )


def collect_bootstrap_signals(repo_root: Path) -> BootstrapSignals:
    """Collect bounded project-shape signals for skill-driven Constitution drafting.

    Walks a fixed priority list of manifest and documentation files at the
    repo root, reads each up to ``_PER_FILE_CAP_BYTES``, and stops once the
    payload reaches ``_MAX_SIGNAL_FILES`` files or ``_TOTAL_CAP_BYTES`` bytes.
    Files matching a path-level deny glob are skipped before reading; files
    whose decoded body contains secret-shaped content (per ``tools.redaction``
    or a focused fallback regex) are dropped after read and recorded in
    ``dropped_for_secrets``. The function performs no LLM calls and no
    network access.

    Args:
        repo_root: Absolute path to the repository root.

    Returns:
        A frozen :class:`BootstrapSignals` whose ``files`` list is in
        priority order. Two invocations on the same tree return equal results.

    Raises:
        AmendError: If ``repo_root`` does not exist or is not a directory.
    """
    return _collect_signals(repo_root, names=_MANIFEST_NAMES + _DOC_NAMES)


def collect_resync_signals(repo_root: Path) -> BootstrapSignals:
    """Collect bounded doc-only signals for the conventions resync workflow.

    Same bounds as :func:`collect_bootstrap_signals` (16 KiB per file, 80 KiB
    total payload, 8-file cap, deny-glob filter, secret-content drop) but
    restricts the candidate set to ``_DOC_NAMES`` — ``AGENTS.md`` /
    ``CLAUDE.md`` / ``README.md``. Manifests are intentionally excluded:
    the resync flow inspects prose-authored convention rules, not project
    structure.

    Args:
        repo_root: Absolute path to the repository root.

    Returns:
        A frozen :class:`BootstrapSignals` (data shape unchanged so both
        entry points share the same surface).

    Raises:
        AmendError: If ``repo_root`` does not exist or is not a directory.
    """
    return _collect_signals(repo_root, names=_DOC_NAMES)


def classify_change(before: str, after: str) -> str:
    """Return 'patch' | 'minor' | 'major' for the diff between two Constitution texts.

    Parses both bodies in memory via ``parse_constitution_text``; raises
    AmendError if either side fails to parse.
    """
    if before == after:
        return "patch"  # noop; caller may abort separately
    try:
        before_articles = parse_constitution_text(before)
        after_articles = parse_constitution_text(after)
    except ConstitutionError as exc:
        raise AmendError(f"cannot classify amend: {exc}") from exc

    before_ids = {a.id: a for a in before_articles}
    after_ids = {a.id: a for a in after_articles}

    if before_ids.keys() - after_ids.keys():
        return "major"  # removal
    if after_ids.keys() - before_ids.keys():
        return "minor"  # addition

    # Iterate ALL level diffs before deciding so a later tightening can't be
    # missed when an earlier diff was a loosening (or vice versa).
    saw_tighten = False
    saw_loosen = False
    for aid, before_article in before_ids.items():
        after_article = after_ids[aid]
        if before_article.level == after_article.level:
            continue
        before_rank = _LEVEL_RANK[before_article.level]
        after_rank = _LEVEL_RANK[after_article.level]
        if after_rank > before_rank:
            saw_tighten = True
        else:
            saw_loosen = True
    if saw_tighten:
        return "major"  # tighten always wins (even mixed with loosen)
    if saw_loosen:
        return "minor"
    return "patch"


def bump_version(current: str, scope: str) -> str:
    """Return the next semver string for ``current`` given the bump ``scope``."""
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", current)
    if match is None:
        raise AmendError(f"invalid semver: {current!r}")
    major, minor, patch = (int(g) for g in match.groups())
    if scope == "patch":
        return f"{major}.{minor}.{patch + 1}"
    if scope == "minor":
        return f"{major}.{minor + 1}.0"
    if scope == "major":
        return f"{major + 1}.0.0"
    raise AmendError(f"invalid bump scope: {scope!r}")


_FRONTMATTER_DELIMITER_PARTS = 3


@dataclass(frozen=True, kw_only=True)
class AmendResult:
    """Outcome record returned by ``amend_constitution`` on success."""

    scope: str  # "patch" | "minor" | "major"
    old_version: str
    new_version: str
    decisions_entry: str


# Anchor frontmatter regexes to the first `---` block only — running these
# unanchored across the body would falsely match article text quoting
# "version: 1.2.3" or similar.
_FRONTMATTER_VERSION_RE = re.compile(r"^version:\s*['\"]?(\d+\.\d+\.\d+)['\"]?\s*$", re.MULTILINE)
_FRONTMATTER_UPDATED_RE = re.compile(r"^updated:.*$", re.MULTILINE)
# Accepts both quoted (``created: "2026-05-11"``) and unquoted
# (``created: 2026-05-11``) forms — parity with ``_FRONTMATTER_VERSION_RE``.
# Hand-editing the draft in ``$EDITOR`` (skill step 7) commonly drops the
# quotes; the YAML loader tolerates both, so the regex must too. Date
# semantics (real month/day) are not the responsibility of this structural
# gate.
_FRONTMATTER_CREATED_RE = re.compile(
    r'^created:\s*["\']?(\d{4}-\d{2}-\d{2})["\']?\s*$', re.MULTILINE
)


def _split_frontmatter(text: str) -> tuple[str, str]:
    r"""Return (frontmatter_block, rest). Raise if frontmatter missing.

    Splits on the first two ``---\n`` boundaries so subsequent frontmatter
    regex operations are scoped to the leading YAML block.
    """
    if not text.startswith("---\n"):
        raise AmendError("Constitution missing leading frontmatter delimiter")
    parts = text.split("---\n", 2)
    if len(parts) < _FRONTMATTER_DELIMITER_PARTS:
        raise AmendError("Constitution frontmatter is unterminated (missing second `---`)")
    _, fm, rest = parts
    return fm, rest


def _read_current_version(text: str) -> str:
    """Return the ``version:`` string from the leading frontmatter block."""
    fm, _ = _split_frontmatter(text)
    match = _FRONTMATTER_VERSION_RE.search(fm)
    if not match:
        raise AmendError("Constitution frontmatter missing `version:`")
    return match.group(1)


def _validate_constitution_body(target: Path) -> None:
    """Run the Constitution structural validator directly. Raise on BLOCK/HIGH.

    Calls :func:`tools.validate.constitution.validate_constitution` in-process
    rather than re-launching ``python -m tools.validate`` — avoids subprocess
    overhead per amend, and surfaces structured ``Finding`` records instead
    of stdout/stderr text.
    """
    findings = validate_constitution(target)
    blocking = [f for f in findings if f.severity in EXIT_NONZERO_SEVERITIES]
    if blocking:
        rendered = "; ".join(f"{f.severity} {f.message}" for f in blocking)
        raise AmendError(f"Constitution validation failed: {rendered}")


def _replace_or_append_frontmatter(text: str, *, new_version: str, today: date) -> str:
    r"""Update version + updated fields inside the frontmatter block only.

    Splits at ``---\n`` boundaries before regex-substituting so an article body
    quoting ``version: 1.2.3`` cannot be mistaken for frontmatter.
    """
    fm, rest = _split_frontmatter(text)
    fm = _FRONTMATTER_VERSION_RE.sub(f"version: {new_version}", fm, count=1)
    iso = today.isoformat()
    if _FRONTMATTER_UPDATED_RE.search(fm):
        fm = _FRONTMATTER_UPDATED_RE.sub(f'updated: "{iso}"', fm, count=1)
    else:
        # Insert `updated:` right after the (now bumped) version line.
        fm = re.sub(
            r"(version:\s*\d+\.\d+\.\d+\s*\n)",
            rf'\1updated: "{iso}"\n',
            fm,
            count=1,
        )
    return f"---\n{fm}---\n{rest}"


_DecisionsKind = Literal["amendment", "bootstrap"]


def _format_decisions_entry(
    *,
    today: date,
    new_version: str,
    context: str,
    change_line: str,
    kind: _DecisionsKind = "amendment",
    title_suffix: str = "",
    alternatives: str = "—",
) -> str:
    """Render the decisions.md ADR block for a Constitution write.

    One helper covers both lifecycles so the amend and bootstrap entries
    cannot drift in shape. ``kind`` selects the title label,
    ``title_suffix`` appends a parenthetical qualifier (e.g.
    ``(skill-drafted)`` for bootstrap), and ``alternatives`` lets the
    bootstrap path record a meaningful "what the user said no to" line
    instead of a dash.
    """
    label = "Constitution amendment" if kind == "amendment" else "Constitution bootstrap"
    suffix = f" {title_suffix}" if title_suffix else ""
    return (
        f"\n## {today.isoformat()} — {label}: v{new_version}{suffix}\n"
        f"**Context:** {context}\n"
        f"**Change:** {change_line}\n"
        f"**Alternatives considered:** {alternatives}\n"
    )


def ensure_decisions_file(decisions_path: Path) -> bool:
    """Create ``decisions.md`` with the standard ``# Decisions`` H1 header if absent.

    Constitution amends and ACK-hook deviation entries are repo-level
    decisions and the validator's deviations cross-ref needs a decisions.md
    to exist. Auto-create on first call rather than crash mid-lifecycle.
    Both the amend lifecycle and the ship-time ACK hook share this helper so
    a freshly-bootstrapped decisions.md always starts with the same header.

    Returns:
        True when the file was created in this call (rollback callers must
        remove it on append failure to keep the atomic-pair contract). False
        when the file already existed.
    """
    if decisions_path.exists():
        return False
    decisions_path.parent.mkdir(parents=True, exist_ok=True)
    decisions_path.write_text("# Decisions\n\n", encoding="utf-8")
    return True


def atomic_replace(target: Path, body: str) -> None:
    """Write ``body`` to ``target`` via ``Path.replace`` from a sibling tempfile.

    POSIX semantics: rename within the same directory is atomic, so any
    process crash leaves either the old or the new file intact, never a
    partial. The tmpfile data and the parent-dir entry are both
    ``fsync``-ed so a power loss between the write and the rename cannot
    leave the new dentry pointing at unwritten blocks. Concurrent retries
    are safe — the tmpfile name is deterministic from
    ``target.name + '.tmp'``, so one retry path overwrites the previous
    tmpfile and the rename remains the single mutation that flips the
    canonical name.

    Cleanup contract: if ``tmp.replace(target)`` itself fails (rare — e.g.
    cross-device EXDEV, missing destination dir after concurrent rmtree),
    the orphan ``.tmp`` file is removed before re-raising so retry logic
    never has to step over a stale sibling. ``fsync`` failures on the
    parent directory are best-effort — some filesystems / platforms
    return EINVAL for directory fsync; we swallow the OSError because the
    rename already succeeded.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    # Stay on ``Path.write_text`` so all existing call-site mocks (which
    # patch ``Path.write_text`` to simulate write failure) keep tripping
    # after the fsync hardening below.
    tmp.write_text(body, encoding="utf-8")
    # Force tmp data to disk BEFORE the rename so a power-loss between
    # write and rename cannot leave the new dentry pointing at unwritten
    # blocks. Best-effort on filesystems that reject fsync (e.g. some
    # tmpfs configurations).
    try:
        fd = os.open(tmp, os.O_RDONLY)
    except OSError:
        fd = -1
    if fd >= 0:
        try:
            with contextlib.suppress(OSError):
                os.fsync(fd)
        finally:
            os.close(fd)
    try:
        tmp.replace(target)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
    # Force the rename to disk so a crash after the user sees success
    # cannot un-do the dentry flip.
    try:
        dir_fd = os.open(target.parent, os.O_RDONLY)
    except OSError:
        return
    try:
        with contextlib.suppress(OSError):
            os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


# Backwards-compatible private aliases for in-module call sites.
_ensure_decisions_file = ensure_decisions_file
_atomic_replace = atomic_replace


def amend_constitution(
    *,
    repo_root: Path,
    decisions_path: Path,
    editor: Callable[[Path], None],
    prompter: Callable[[str, str], str],
    today: date | None = None,
) -> AmendResult:
    """Run the atomic-pair amend lifecycle.

    Order:
        1. Read ``before`` from disk.
        2. Open editor against a tempfile copy of ``before``; read user output.
        3. Bail with ``AmendError`` on no-op diff.
        4. Classify + bump version + apply frontmatter rewrite.
        5. Gather decisions body via prompter (BEFORE any disk write).
        6. Validate the proposed Constitution body via subprocess.
        7. Auto-create ``decisions.md`` if absent.
        8. Atomically write the new Constitution via ``_atomic_replace``.
        9. Append the decisions entry. On failure, restore Constitution to
           ``before`` via ``_atomic_replace`` so both files end at pre-amend state.

    See module docstring for bump rules.
    """
    today = today or date.today()
    constitution = repo_root / ".forge" / "CONSTITUTION.md"
    if not constitution.exists():
        raise AmendError(f"Constitution not found at {constitution}")
    before = constitution.read_text(encoding="utf-8")
    current_version = _read_current_version(before)

    # Open editor on a temp working copy so the original survives editor crash.
    with tempfile.NamedTemporaryFile(
        prefix="forge-constitution-",
        suffix=".md",
        delete=False,
        mode="w",
        encoding="utf-8",
    ) as handle:
        handle.write(before)
        working = Path(handle.name)
    try:
        editor(working)
        after = working.read_text(encoding="utf-8")
    finally:
        working.unlink(missing_ok=True)

    if before == after:
        raise AmendError("no changes detected; nothing to amend")
    scope = classify_change(before, after)
    new_version = bump_version(current_version, scope)
    after = _replace_or_append_frontmatter(after, new_version=new_version, today=today)

    # Gather decisions body BEFORE any mutation. If the user aborts the prompt,
    # AmendError propagates and disk is unchanged.
    decisions_body = prompter(scope, new_version)
    if not decisions_body.strip():
        raise AmendError("decisions entry is empty; provide a non-trivial reason")

    # Validate via the structural validator. No disk mutation yet.
    with tempfile.NamedTemporaryFile(
        prefix="forge-constitution-validated-",
        suffix=".md",
        delete=False,
        mode="w",
        encoding="utf-8",
    ) as handle:
        handle.write(after)
        candidate = Path(handle.name)
    try:
        _validate_constitution_body(candidate)
    finally:
        candidate.unlink(missing_ok=True)

    decisions_created = _ensure_decisions_file(decisions_path)

    # Atomic-pair write: Constitution first via os.replace, then decisions
    # append. On decisions-append failure, restore Constitution to `before`
    # so both files end at pre-amend state. If we created decisions.md in
    # _ensure_decisions_file, remove it on rollback — leaving the bare header
    # behind would violate the "both files end at pre-amend state" contract.
    _atomic_replace(constitution, after)
    entry = _format_decisions_entry(
        today=today,
        new_version=new_version,
        context=decisions_body,
        change_line=f"{scope} bump.",
        kind="amendment",
    )
    try:
        with decisions_path.open("a", encoding="utf-8") as fh:
            fh.write(entry)
    except OSError as exc:
        _atomic_replace(constitution, before)
        if decisions_created:
            decisions_path.unlink(missing_ok=True)
        raise AmendError(
            f"decisions.md append failed; Constitution restored to v{current_version}: {exc}"
        ) from exc

    return AmendResult(
        scope=scope,
        old_version=current_version,
        new_version=new_version,
        decisions_entry=entry,
    )


_VALID_LEVELS: frozenset[str] = frozenset({"CRITICAL", "SHOULD", "MAY"})
_DUPLICATE_TRIGGER_COUNT = 2


def _check_draft_frontmatter(text: str) -> None:
    """Raise ``AmendError`` if frontmatter lacks a valid ``version:`` or ``created:``."""
    try:
        fm, _ = _split_frontmatter(text)
    except AmendError as exc:
        raise AmendError(f"draft frontmatter invalid: {exc}") from exc
    if not _FRONTMATTER_VERSION_RE.search(fm):
        raise AmendError(
            "draft frontmatter missing or malformed `version:` (expected semver MAJOR.MINOR.PATCH)"
        )
    if not _FRONTMATTER_CREATED_RE.search(fm):
        raise AmendError(
            'draft frontmatter missing or malformed `created:` (expected "YYYY-MM-DD")'
        )


def _check_draft_articles(articles: list[Article]) -> None:
    """Raise ``AmendError`` on per-article shape, duplicates, or budget violations."""
    for article in articles:
        if article.level not in _VALID_LEVELS:
            raise AmendError(
                f"draft article {article.id}: level {article.level!r} not in "
                f"{sorted(_VALID_LEVELS)}"
            )
        if not article.rule:
            raise AmendError(f"draft article {article.id}: empty `Rule:` field")
        if article.reference is None:
            raise AmendError(f"draft article {article.id}: missing `Reference:` field")
        if article.rationale is None:
            raise AmendError(f"draft article {article.id}: missing `Rationale:` field")

    seen: dict[str, int] = {}
    duplicates: list[str] = []
    for article in articles:
        seen[article.id] = seen.get(article.id, 0) + 1
        if seen[article.id] == _DUPLICATE_TRIGGER_COUNT:
            duplicates.append(article.id)
    if duplicates:
        raise AmendError(f"draft has duplicate article numbers: {sorted(duplicates)}")

    over_cap = [(a.id, a.body_words) for a in articles if a.body_words > MAX_INJECTED_WORDS]
    if over_cap:
        rendered = ", ".join(f"{aid}={words} words" for aid, words in over_cap)
        raise AmendError(f"draft article(s) exceed {MAX_INJECTED_WORDS}-word cap: {rendered}")


def validate_drafted_markdown(text: str) -> list[Article]:
    """Validate a skill-drafted Constitution body and return parsed Articles.

    The skill (not Python) produces the markdown; this function is the gate
    that catches structural, vocabulary, and budget violations before any
    disk mutation. It performs no I/O.

    Validation order:
        1. Parser shape (delegated to ``parse_constitution_text``).
        2. Frontmatter ``version:`` (semver) + ``created:`` (YYYY-MM-DD).
        3. Per-article field presence (level vocabulary, rule, reference,
           rationale).
        4. Per-article body word count vs ``MAX_INJECTED_WORDS``.
        5. Zero-article check.

    Args:
        text: Full Constitution body — frontmatter plus articles — as a
            single string.

    Returns:
        Parsed :class:`tools.constitution.Article` records in declaration
        order.

    Raises:
        AmendError: When the parser rejects the body, when frontmatter
            shape is wrong, when an article is missing a required field,
            when an article body exceeds the injection-budget word cap, or
            when the draft carries zero articles.
    """
    try:
        articles = parse_constitution_text(text)
    except ConstitutionError as exc:
        raise AmendError(f"draft parse failed: {exc}") from exc

    # Frontmatter `version:` / `created:` are a bootstrap contract above what
    # the loader checks; keep them out of the loader to avoid widening its
    # surface for the amend path.
    _check_draft_frontmatter(text)
    _check_draft_articles(articles)

    if not articles:
        raise AmendError("draft has zero articles; bootstrap requires at least one")

    return articles


def persist_drafted_constitution(
    *,
    repo_root: Path,
    body: str,
    decisions_path: Path,
    today: date | None = None,
) -> Path:
    """Persist a skill-drafted Constitution body via the atomic-pair contract.

    Takes the final markdown body from the caller (the skill) and runs the
    atomic-pair disk lifecycle: validate, write Constitution, append the
    bootstrap ADR. Refuses when a Constitution already exists at
    ``repo_root/.forge/CONSTITUTION.md``.

    Order:
        1. Refuse if ``.forge/CONSTITUTION.md`` already exists.
        2. Run :func:`validate_drafted_markdown` against ``body`` — propagate
           ``AmendError`` on failure with no disk mutation.
        3. Run the structural validator via a temp file. Propagate on failure.
        4. ``ensure_decisions_file(decisions_path)``.
        5. Atomically write the Constitution.
        6. Append the bootstrap ADR entry to ``decisions.md``.
        7. On append failure, delete the Constitution AND any freshly-created
           ``decisions.md`` so both files end at pre-call state.

    Args:
        repo_root: Repository root containing ``.forge/``.
        body: Full Constitution markdown to persist verbatim.
        decisions_path: Target ``decisions.md`` for the ADR append.
        today: Optional override for ``date.today()``; used by tests for
            stable ADR timestamps.

    Returns:
        Absolute path to the newly-written Constitution.

    Raises:
        AmendError: When the Constitution already exists, when validation
            (skill-shape or structural) rejects the body, or when the
            atomic-pair write cannot complete.
    """
    today = today or date.today()
    constitution = repo_root / ".forge" / "CONSTITUTION.md"

    # Validate BEFORE touching the disk. A bad body never produces a
    # placeholder file. The exists-check uses ``os.O_EXCL`` below as the
    # actual claim, so we don't pre-check here — race-free.
    articles = validate_drafted_markdown(body)
    version = _read_current_version(body)

    # Run the structural validator against a temp copy so a failure leaves
    # no on-disk Constitution.
    with tempfile.NamedTemporaryFile(
        prefix="forge-constitution-drafted-",
        suffix=".md",
        delete=False,
        mode="w",
        encoding="utf-8",
    ) as handle:
        handle.write(body)
        candidate = Path(handle.name)
    try:
        _validate_constitution_body(candidate)
    finally:
        candidate.unlink(missing_ok=True)

    # Claim the Constitution path atomically. ``O_CREAT | O_EXCL`` raises
    # FileExistsError if any process (including a concurrent bootstrap)
    # already owns the path — replacing the prior best-effort
    # ``exists()`` check that was TOCTOU-vulnerable.
    constitution.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(constitution, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError as exc:
        raise AmendError(
            f"Constitution already exists at {constitution}; use plain /forge:amend-constitution"
        ) from exc
    os.close(fd)

    decisions_created = ensure_decisions_file(decisions_path)
    try:
        atomic_replace(constitution, body)
    except OSError as exc:
        # Atomic rename failed after the O_EXCL claim — clean up the
        # placeholder and any freshly-created decisions.md before
        # re-raising so the caller sees a pristine repo.
        constitution.unlink(missing_ok=True)
        if decisions_created:
            decisions_path.unlink(missing_ok=True)
        raise AmendError(f"atomic write of Constitution failed: {exc}") from exc

    entry = _format_decisions_entry(
        today=today,
        new_version=version,
        context=f"Skill-drafted starter Constitution with {len(articles)} article(s).",
        change_line="New Constitution seeded.",
        kind="bootstrap",
        title_suffix="(skill-drafted)",
        alternatives="Decline bootstrap and continue without a Constitution.",
    )
    try:
        with decisions_path.open("a", encoding="utf-8") as fh:
            fh.write(entry)
    except OSError as exc:
        constitution.unlink(missing_ok=True)
        if decisions_created:
            decisions_path.unlink(missing_ok=True)
        raise AmendError(f"decisions.md append failed: {exc}") from exc
    return constitution


def _serialize_convention(rule: Convention) -> dict[str, object]:
    """Render a :class:`Convention` into its on-disk JSON dict shape.

    Matches the keys and order in ``schemas/conventions.schema.json``; the
    list-typed ``scope`` field is materialized from the tuple so the JSON
    output round-trips through :func:`load_conventions`.
    """
    return {
        "id": rule.id,
        "source_file": rule.source_file,
        "source_line": rule.source_line,
        "pattern_kind": rule.pattern_kind,
        "pattern": rule.pattern,
        "scope": list(rule.scope),
        "severity": rule.severity,
    }


def _detect_inner_duplicates(entries: list[Convention]) -> list[str]:
    """Return the list of ids that appear more than once within ``entries``."""
    seen: set[str] = set()
    duplicates: list[str] = []
    for entry in entries:
        if entry.id in seen and entry.id not in duplicates:
            duplicates.append(entry.id)
        seen.add(entry.id)
    return duplicates


def _read_existing_conventions(
    repo_root: Path,
    conventions_path: Path,
) -> tuple[list[Convention], str | None, bool]:
    """Return ``(existing_rules, previous_body, file_existed)`` for rollback bookkeeping."""
    file_existed = conventions_path.is_file()
    if not file_existed:
        return [], None, False
    try:
        previous_body = conventions_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AmendError(f"failed to read existing conventions.json: {exc}") from exc
    try:
        existing_rules = load_conventions(repo_root)
    except ValueError as exc:
        raise AmendError(f"existing conventions.json invalid: {exc}") from exc
    return existing_rules, previous_body, True


def _format_conventions_adr(*, today: date, entries: list[Convention]) -> str:
    """Render the ADR row for an ``append_conventions_entries`` call."""
    ids_csv = ", ".join(entry.id for entry in entries)
    n = len(entries)
    plural = "entry" if n == 1 else "entries"
    return (
        f"\n## {today.isoformat()} — Conventions resync (--resync-agents)\n"
        f"**Context:** Added {n} convention {plural}: {ids_csv}.\n"
        f"**Change:** .forge/conventions.json updated.\n"
        f"**Alternatives considered:** Manual conventions.json edit, "
        f"skipped to preserve schema validation.\n"
    )


def append_conventions_entries(
    repo_root: Path,
    entries: list[Convention],
    *,
    decisions_path: Path | None = None,
    today: date | None = None,
) -> Path:
    """Append ``entries`` to ``.forge/conventions.json`` and log a decisions ADR row.

    Reads any pre-existing ``.forge/conventions.json`` (parsed via
    :func:`tools.validate.conventions.load_conventions`), refuses on id
    collision (existing or duplicate inside ``entries``), writes the merged
    array atomically via :func:`atomic_replace`, then appends a single ADR
    row to ``decisions.md`` describing the added ids. On decisions-append
    failure the conventions file is restored to its pre-call state so the
    pair ends at pre-call shape (file removed if it was absent before;
    body restored otherwise).

    Args:
        repo_root: Repository root containing the ``.forge`` directory.
        entries: New :class:`Convention` records to append. Must be
            non-empty; ids must be unique across ``entries`` and against
            any pre-existing rules.
        decisions_path: Target ``decisions.md`` for the ADR row; defaults
            to ``<repo_root>/decisions.md``.
        today: Optional override for ``date.today()``; used by tests for
            stable ADR timestamps.

    Returns:
        Absolute path to ``.forge/conventions.json``.

    Raises:
        AmendError: When ``entries`` is empty, when any new id collides
            with an existing entry or with another new entry, when the
            merged file fails ``load_conventions`` (schema, duplicate, or
            shape rejection), or when the atomic-pair write cannot
            complete (conventions.json is restored before the error
            surfaces).
    """
    if not entries:
        raise AmendError("append_conventions_entries requires at least one new entry")

    today = today or date.today()
    decisions_path = decisions_path or repo_root / "decisions.md"
    conventions_path = repo_root / ".forge" / "conventions.json"

    inner_dupes = _detect_inner_duplicates(entries)
    if inner_dupes:
        raise AmendError(f"duplicate id(s) within new entries: {sorted(inner_dupes)}")

    existing_rules, previous_body, file_existed = _read_existing_conventions(
        repo_root, conventions_path
    )

    new_ids = [entry.id for entry in entries]
    collisions = sorted(set(new_ids) & {rule.id for rule in existing_rules})
    if collisions:
        raise AmendError(f"id collision with existing entries: {collisions}")

    merged = [_serialize_convention(rule) for rule in existing_rules]
    merged.extend(_serialize_convention(entry) for entry in entries)
    # Preserve the schema's declared field order documented in
    # ``_serialize_convention`` — ``sort_keys=True`` would alphabetize and
    # produce large spurious diffs on the first append against any
    # pre-existing conventions.json. Python dict insertion order is stable
    # since 3.7, so the in-memory ordering is the on-disk ordering.
    serialized = json.dumps(merged, indent=2) + "\n"

    # Write merged body, then re-validate via the strict ``load_conventions``
    # to catch corner cases (bad regex, mis-scoped filename_glob_forbidden,
    # ReDoS-shape patterns) BEFORE we touch decisions.md. The strict path
    # now bundles regex compile + ReDoS-shape + scope-shape checks, so the
    # earlier ad-hoc ``_check_merged_patterns`` pass became redundant and
    # was removed.
    conventions_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        atomic_replace(conventions_path, serialized)
    except OSError as exc:
        raise AmendError(f"atomic write of conventions.json failed: {exc}") from exc

    try:
        load_conventions(repo_root)
    except ValueError as exc:
        _restore_conventions(conventions_path, previous_body, file_existed)
        raise AmendError(f"merged conventions.json failed validation: {exc}") from exc

    decisions_created = ensure_decisions_file(decisions_path)
    adr = _format_conventions_adr(today=today, entries=entries)
    try:
        with decisions_path.open("a", encoding="utf-8") as fh:
            fh.write(adr)
    except OSError as exc:
        _restore_conventions(conventions_path, previous_body, file_existed)
        if decisions_created:
            decisions_path.unlink(missing_ok=True)
        raise AmendError(
            f"decisions.md append failed; conventions.json restored: {exc}",
        ) from exc

    return conventions_path


@dataclass(frozen=True, kw_only=True)
class AdvisoryEntry:
    """One advisory-only convention row logged to ``decisions.md``.

    Mirrors the surface of :class:`tools.conventions_runtime.Convention`'s
    ``source_file`` / ``source_line`` fields so the resync skill can hand
    the same tuple it already authors for ``hook`` / ``validator`` rules.
    """

    rule_text: str
    source_file: str
    source_line: int


def _format_advisory_adr(*, today: date, entries: list[AdvisoryEntry]) -> str:
    """Render the ADR row for an :func:`log_advisory_entries` call."""
    bullets = "\n".join(
        f"  - {entry.rule_text} (from {entry.source_file}:{entry.source_line})" for entry in entries
    )
    return (
        f"\n## {today.isoformat()} — Conventions resync: advisory items\n"
        f"**Context:** The following AGENTS.md / CLAUDE.md / README.md prose rules\n"
        f"stay honor-system (advisory only):\n{bullets}\n"
        f"**Change:** No mechanical enforcement added.\n"
        f"**Alternatives considered:** Promote to reviewer-tag, validator, or hook.\n"
    )


def log_advisory_entries(
    *,
    repo_root: Path,
    entries: list[AdvisoryEntry],
    decisions_path: Path | None = None,
    today: date | None = None,
) -> Path:
    """Append a single ADR row recording prose rules left as advisory-only.

    The resync skill (:doc:`forge-resync-agents`) routes accepted entries
    by mechanism: ``hook`` and ``validator`` entries flow through
    :func:`append_conventions_entries`; ``reviewer-tag`` entries are
    surfaced as TODOs pointing at ``/forge:amend-constitution``;
    ``advisory`` entries used to be inlined directly into a raw
    ``decisions.md`` write inside the skill prose. This helper makes the
    advisory write a typed, atomic operation symmetric with the other
    mechanism paths — same ADR shape, same date stamp, same auto-bootstrap
    of ``decisions.md`` when it does not yet exist.

    Args:
        repo_root: Repository root containing the ``.forge`` directory.
            Used only to derive the default ``decisions_path`` when one is
            not supplied.
        entries: Non-empty list of :class:`AdvisoryEntry` rows. Each row
            renders as one bullet in the appended ADR.
        decisions_path: Target ``decisions.md`` for the ADR row; defaults
            to ``<repo_root>/decisions.md``.
        today: Optional override for ``date.today()`` (test seam).

    Returns:
        Absolute path to ``decisions.md``.

    Raises:
        AmendError: When ``entries`` is empty or when the file append
            cannot complete. Auto-creation of ``decisions.md`` failures
            are unwrapped to ``AmendError`` for consistency.
    """
    if not entries:
        raise AmendError("log_advisory_entries requires at least one entry")
    today = today or date.today()
    decisions_path = decisions_path or repo_root / "decisions.md"
    decisions_created = ensure_decisions_file(decisions_path)
    adr = _format_advisory_adr(today=today, entries=entries)
    try:
        with decisions_path.open("a", encoding="utf-8") as fh:
            fh.write(adr)
    except OSError as exc:
        if decisions_created:
            decisions_path.unlink(missing_ok=True)
        raise AmendError(f"decisions.md append failed: {exc}") from exc
    return decisions_path


def _restore_conventions(
    conventions_path: Path,
    previous_body: str | None,
    file_existed: bool,
) -> None:
    """Roll ``conventions.json`` back to its pre-call shape.

    File absent before the call ⇒ delete the new file. File present
    before ⇒ atomically replace with the captured body so the file's
    inode flip is the only mutation. Best-effort: the caller is already
    in an error path, so OS failures during rollback are swallowed.
    """
    if not file_existed:
        conventions_path.unlink(missing_ok=True)
        return
    if previous_body is not None:
        with contextlib.suppress(OSError):
            atomic_replace(conventions_path, previous_body)
