"""Atomic Constitution edit + semver classifier.

Bump rules per M3 spec D-12:
    patch  — clarification (text edit only; same article count + same level set)
    minor  — add article OR loosen level (CRITICAL→SHOULD, SHOULD→MAY)
    major  — remove article OR tighten level (SHOULD→CRITICAL, MAY→SHOULD)

A change that triggers more than one rule uses the strongest applicable
bump (major > minor > patch).
"""

from __future__ import annotations

import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePosixPath

from tools import redaction
from tools.constitution import (
    MAX_INJECTED_WORDS,
    Article,
    ConstitutionError,
    _bare_dep_name,
    _read_package_json_top_level_deps,
    _read_pyproject_top_level_deps,
    parse_constitution_text,
)
from tools.validate._finding import EXIT_NONZERO_SEVERITIES
from tools.validate.constitution import validate_constitution


class AmendError(RuntimeError):
    """Raised when the amend lifecycle cannot complete."""


_LEVEL_RANK = {"CRITICAL": 3, "SHOULD": 2, "MAY": 1}


# Signal collection bounds. Underscore-prefixed because they are tuning knobs
# for one function rather than part of the module's public dispatch contract.
_PER_FILE_CAP_BYTES = 16384
_TOTAL_CAP_BYTES = 81920
_MAX_SIGNAL_FILES = 8
_TRUNCATION_MARKER = "\n--- truncated at 16384 bytes ---\n"
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
    """Pure-data result of :func:`collect_bootstrap_signals`."""

    files: list[SignalFile]
    dropped_for_secrets: list[PurePosixPath]
    truncated: list[PurePosixPath]
    total_bytes: int


def _name_matches_deny_glob(name: str) -> bool:
    """True iff ``name`` matches any path-level deny glob."""
    return any(redaction._globstar_match(name, g) for g in _DENY_GLOBS)


def _candidate_paths(repo_root: Path) -> list[Path]:
    """Return existing candidate file paths in priority order, first-match per name."""
    candidates: list[Path] = []
    seen: set[Path] = set()
    for name in _MANIFEST_NAMES:
        path = repo_root / name
        if path.is_file() and path not in seen:
            candidates.append(path)
            seen.add(path)
    # ``*.csproj`` is glob-matched (non-recursive, repo root only); first sorted name.
    csproj_hits = sorted(repo_root.glob("*.csproj"))
    for path in csproj_hits:
        if path.is_file() and path not in seen:
            candidates.append(path)
            seen.add(path)
            break  # only the first sorted match
    for name in _DOC_NAMES:
        path = repo_root / name
        if path.is_file() and path not in seen:
            candidates.append(path)
            seen.add(path)
    return candidates


def _read_and_truncate(path: Path) -> tuple[str, bool]:
    """Read ``path`` raw bytes, truncate to per-file cap, decode UTF-8.

    Returns ``(body, truncated)``. ``body`` ends with the truncation marker
    when the source exceeded the cap.
    """
    raw = path.read_bytes()
    if len(raw) > _PER_FILE_CAP_BYTES:
        head = raw[:_PER_FILE_CAP_BYTES]
        return head.decode("utf-8", errors="replace") + _TRUNCATION_MARKER, True
    return raw.decode("utf-8", errors="replace"), False


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
    if not repo_root.is_dir():
        raise AmendError(f"repo_root not found or not a directory: {repo_root}")

    files: list[SignalFile] = []
    dropped: list[PurePosixPath] = []
    truncated_paths: list[PurePosixPath] = []
    total_bytes = 0

    for path in _candidate_paths(repo_root):
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
        dropped_for_secrets=dropped,
        truncated=truncated_paths,
        total_bytes=total_bytes,
    )


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


def _format_decisions_entry(*, today: date, new_version: str, scope: str, body: str) -> str:
    """Render the decisions.md ADR block for one Constitution amendment."""
    return (
        f"\n## {today.isoformat()} — Constitution amendment: v{new_version}\n"
        f"**Context:** {body}\n"
        f"**Change:** {scope} bump.\n"
        f"**Alternatives considered:** —\n"
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
    crash leaves either the old or the new file intact, never a partial.
    Concurrent retries are safe — the tmpfile name is deterministic from
    ``target.name + '.tmp'``, so one retry path overwrites the previous
    tmpfile and the rename remains the single mutation that flips the
    canonical name.

    Cleanup contract: if ``tmp.replace(target)`` itself fails (rare — e.g.
    cross-device EXDEV, missing destination dir after concurrent rmtree),
    the orphan ``.tmp`` file is removed before re-raising so retry logic
    never has to step over a stale sibling.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    try:
        tmp.replace(target)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


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
        today=today, new_version=new_version, scope=scope, body=decisions_body
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


@dataclass(frozen=True, kw_only=True)
class ProposedArticle:
    """Bootstrap proposal record reviewed article-by-article by the user."""

    title: str
    level: str
    rule: str
    reference: str
    rationale: str
    exception: str = "None."


_BOOTSTRAP_PROPOSALS_CAP = 5


def propose_starter_articles(*, repo_root: Path) -> list[ProposedArticle]:
    """Generate up to 5 starter articles based on detected project signals.

    Reuses ``tools.constitution._read_pyproject_top_level_deps`` /
    ``_read_package_json_top_level_deps`` so dep parsing has one source of truth.

    Detection contract: dep names are tokenized via
    ``tools.constitution._bare_dep_name`` (strips version specifiers + extras)
    and lowercased into a set before keyword comparison. Set-membership beats
    substring match — pre-fix ``"react" in blob`` would false-match against
    ``preact>=1.0`` and silently mis-detect a non-React project.

    Deviation from plan literal: T2 commit 2a10d34 privatized these dep readers
    after the plan was authored. We import the underscored names at module top
    rather than re-implementing them; the lazy/inline import in the plan
    literal was unnecessary (no circular risk).
    """
    pyproject = repo_root / "pyproject.toml"
    package_json = repo_root / "package.json"
    raw_deps: list[str] = []
    raw_deps.extend(_read_pyproject_top_level_deps(pyproject))
    raw_deps.extend(_read_package_json_top_level_deps(package_json))
    dep_names: set[str] = {_bare_dep_name(d).lower() for d in raw_deps if d}

    proposals: list[ProposedArticle] = [
        ProposedArticle(
            title="Secrets via vault only",
            level="SHOULD",
            rule=(
                "Secrets, API keys, and credentials SHOULD be retrieved through "
                "the project's vault loader. Inline credentials are forbidden."
            ),
            reference="OWASP A02:2021, CWE-798",
            rationale="Hard-coded credentials are the most common cause of public credential leaks.",
        )
    ]

    has_analytics = bool(
        dep_names & {"segment", "amplitude", "mixpanel", "heap", "google-analytics"}
    )
    proposals.append(
        ProposedArticle(
            title="PII boundary",
            level="CRITICAL" if has_analytics else "SHOULD",
            rule=(
                "Personally identifiable information MUST stay inside the project's "
                "PII boundary. Telemetry hooks SHALL strip user-level identifiers."
            ),
            reference="GDPR Art. 25 (data protection by design)",
            rationale="Analytics integrations make PII leakage trivial without a hard boundary.",
        )
    )

    has_orm = bool(dep_names & {"sqlalchemy", "django", "peewee", "tortoise"})
    if has_orm:
        proposals.append(
            ProposedArticle(
                title="Repository pattern for data access",
                level="SHOULD",
                rule=(
                    "ORM session calls SHOULD be confined to a `repository/` layer. "
                    "Service-layer code calls repository functions, never the session "
                    "directly."
                ),
                reference="Team consensus 2026-01.",
                rationale="Direct session access in services couples business logic to schema.",
            )
        )

    # `unittest` is stdlib (never in deps) so it is dropped from the keyword
    # set; `pytest` covers the Python-test signal.
    has_test_framework = bool(dep_names & {"pytest", "jest", "vitest", "mocha", "rspec"})
    if has_test_framework:
        proposals.append(
            ProposedArticle(
                title="Test coverage floor",
                level="SHOULD",
                rule=(
                    "New modules SHOULD ship with unit tests covering the documented "
                    "public surface."
                ),
                reference="Team consensus 2026-01.",
                rationale="Untested modules accumulate bugs faster than features.",
            )
        )

    # Source: GitHub Advisory Database — node-uuid deprecated 2018, request
    # unmaintained 2020, left-pad 2016 incident.
    forbidden_packages = {"left-pad", "request", "node-uuid"}
    if dep_names & forbidden_packages:
        proposals.append(
            ProposedArticle(
                title="Forbidden deps",
                level="SHOULD",
                rule=(
                    "Pinned-deprecated packages MUST NOT be added. The Verified "
                    "Dependencies section in PLAN.md SHALL cite a current source."
                ),
                reference="GitHub Advisory Database",
                rationale="Supply-chain risk surfaces fastest in unmaintained deps.",
            )
        )

    # The if-chain produces <=5 proposals (Secrets + PII + ORM + Tests +
    # Forbidden). A runtime check (rather than a silent slice) forces a
    # reviewer to revisit `_BOOTSTRAP_PROPOSALS_CAP` whenever a sixth signal
    # is wired in. AmendError because the only legitimate caller is the
    # bootstrap lifecycle, and aborting there is the right shape for a
    # programmer error.
    if len(proposals) > _BOOTSTRAP_PROPOSALS_CAP:
        raise AmendError(
            f"propose_starter_articles produced {len(proposals)} > "
            f"{_BOOTSTRAP_PROPOSALS_CAP}; bump the cap or trim signals"
        )
    return proposals


def _format_article(proposal: ProposedArticle, number: int) -> str:
    return (
        f"\n## Article {number} — {proposal.title} [{proposal.level}]\n"
        f"**Rule:** {proposal.rule}\n"
        f"**Reference:** {proposal.reference}\n"
        f"**Rationale:** {proposal.rationale}\n"
        f"**Exception:** {proposal.exception}\n"
    )


def bootstrap_constitution(
    *,
    repo_root: Path,
    decisions_path: Path,
    review_proposal: Callable[[ProposedArticle], tuple[str, ProposedArticle | None]],
    today: date | None = None,
) -> Path:
    """Seed ``.forge/CONSTITUTION.md`` from project signals.

    ``review_proposal(proposal)`` returns ``("accept", proposal)`` |
    ``("edit", proposal')`` | ``("drop", None)``. Caller-supplied; the test
    suite drives it deterministically.

    Refuses if ``.forge/CONSTITUTION.md`` already exists. Refuses if the user
    drops every proposal (zero accepted articles is not a valid Constitution).

    Returns the path to the new Constitution.
    """
    today = today or date.today()
    constitution = repo_root / ".forge" / "CONSTITUTION.md"
    if constitution.exists():
        raise AmendError(
            f"Constitution already exists at {constitution}; use plain /forge:amend-constitution"
        )

    proposals = propose_starter_articles(repo_root=repo_root)
    accepted: list[ProposedArticle] = []
    for proposal in proposals:
        action, returned = review_proposal(proposal)
        if action in ("accept", "edit") and returned is not None:
            accepted.append(returned)
        # action == "drop" -> skip
    if not accepted:
        raise AmendError("bootstrap aborted: zero articles accepted")

    body = (
        f'---\nversion: 0.1.0\ncreated: "{today.isoformat()}"\n---\n\n'
        "# Project Constitution\n\n"
        "Project-wide guidance authored by the team. Articles below are surfaced "
        "to spec/plan/execute/review subagents as advisory context (M3) and to "
        "the reviewer subagent as severity hints.\n"
    )
    for index, proposal in enumerate(accepted, start=1):
        body += _format_article(proposal, index)

    # Validate via the structural validator before any disk mutation.
    with tempfile.NamedTemporaryFile(
        prefix="forge-constitution-bootstrap-",
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

    # Atomic-pair write: ensure decisions.md parent exists, atomically write
    # the Constitution, then append the bootstrap ADR. If the append fails
    # (read-only fs, etc.) delete the freshly-written Constitution AND any
    # decisions.md that this call created so the pair stays atomic.
    decisions_created = _ensure_decisions_file(decisions_path)
    _atomic_replace(constitution, body)
    entry = (
        f"\n## {today.isoformat()} — Constitution bootstrap: v0.1.0\n"
        f"**Context:** Bootstrap proposed {len(proposals)} starter articles; "
        f"accepted {len(accepted)}.\n"
        f"**Change:** New Constitution seeded.\n"
        f"**Alternatives considered:** Skip (default).\n"
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

    Mirrors :func:`bootstrap_constitution`'s disk lifecycle but takes the
    final markdown body from the caller (the skill) instead of running the
    per-article proposer + reviewer dance. Refuses when a Constitution
    already exists at ``repo_root/.forge/CONSTITUTION.md``.

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
    if constitution.exists():
        raise AmendError(
            f"Constitution already exists at {constitution}; use plain /forge:amend-constitution"
        )

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

    decisions_created = ensure_decisions_file(decisions_path)
    atomic_replace(constitution, body)
    entry = (
        f"\n## {today.isoformat()} — Constitution bootstrap: v{version} (skill-drafted)\n"
        f"**Context:** Skill-drafted starter Constitution with {len(articles)} article(s).\n"
        f"**Change:** New Constitution seeded.\n"
        f"**Alternatives considered:** Skip (default).\n"
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
