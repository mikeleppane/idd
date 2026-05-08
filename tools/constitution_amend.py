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
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from tools.constitution import (
    Article,
    ConstitutionError,
    _read_package_json_top_level_deps,
    _read_pyproject_top_level_deps,
    parse_constitution,
)


class AmendError(RuntimeError):
    """Raised when the amend lifecycle cannot complete."""


_LEVEL_RANK = {"CRITICAL": 3, "SHOULD": 2, "MAY": 1}


def _articles_from_text(text: str, tmp_path: Path) -> list[Article]:
    """Parse the in-memory body via the tmpfile route to reuse parse_constitution."""
    target = tmp_path / "constitution.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return parse_constitution(target)


def classify_change(before: str, after: str) -> str:
    """Return 'patch' | 'minor' | 'major' for the diff between two Constitution texts.

    Implementation reads both into Article lists by way of a tmpfile parser
    pass; raises AmendError if either side fails to parse.
    """
    if before == after:
        return "patch"  # noop; caller may abort separately
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        try:
            before_articles = _articles_from_text(before, tmp / "before")
            after_articles = _articles_from_text(after, tmp / "after")
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
    rank = "patch"
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
    return rank


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
    """Run ``python -m tools.validate --target constitution <path>``. Raise on failure.

    Uses ``sys.executable`` so the subprocess inherits the project's venv and the
    same ``tools.validate`` import path the parent process can resolve. NEVER
    use ``shutil.which("python")`` here — system Python may lack pyyaml/jsonschema.
    """
    proc = subprocess.run(
        [sys.executable, "-m", "tools.validate", "--target", "constitution", str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise AmendError(
            f"Constitution validation failed (exit {proc.returncode}): "
            f"{proc.stdout.strip()} {proc.stderr.strip()}"
        )


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
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(target)


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
    constitution = repo_root / ".idd" / "CONSTITUTION.md"
    if not constitution.exists():
        raise AmendError(f"Constitution not found at {constitution}")
    before = constitution.read_text(encoding="utf-8")
    current_version = _read_current_version(before)

    # Open editor on a temp working copy so the original survives editor crash.
    with tempfile.NamedTemporaryFile(
        prefix="idd-constitution-",
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
        prefix="idd-constitution-validated-",
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

    Deviation from plan literal: T2 commit 2a10d34 privatized these dep readers
    after the plan was authored. We import the underscored names at module top
    rather than re-implementing them; the lazy/inline import in the plan
    literal was unnecessary (no circular risk).
    """
    pyproject = repo_root / "pyproject.toml"
    package_json = repo_root / "package.json"
    deps_python = " ".join(_read_pyproject_top_level_deps(pyproject))
    deps_node = " ".join(_read_package_json_top_level_deps(package_json))
    blob = (deps_python + " " + deps_node).lower()

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

    has_analytics = any(
        kw in blob for kw in ("segment", "amplitude", "mixpanel", "heap", "google-analytics")
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

    has_orm = any(kw in blob for kw in ("sqlalchemy", "django", "peewee", "tortoise"))
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

    has_test_framework = any(
        kw in blob for kw in ("pytest", "unittest", "jest", "vitest", "mocha", "rspec")
    )
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

    forbidden_packages = {"left-pad", "request", "node-uuid"}  # extend as needed
    if any(pkg in blob for pkg in forbidden_packages):
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

    # Cap kept defensively; the if-chain currently produces <=5 but a future
    # signal could push past. _BOOTSTRAP_PROPOSALS_CAP is the contract floor.
    return proposals[:_BOOTSTRAP_PROPOSALS_CAP]


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
    """Seed ``.idd/CONSTITUTION.md`` from project signals.

    ``review_proposal(proposal)`` returns ``("accept", proposal)`` |
    ``("edit", proposal')`` | ``("drop", None)``. Caller-supplied; the test
    suite drives it deterministically.

    Refuses if ``.idd/CONSTITUTION.md`` already exists. Refuses if the user
    drops every proposal (zero accepted articles is not a valid Constitution).

    Returns the path to the new Constitution.
    """
    today = today or date.today()
    constitution = repo_root / ".idd" / "CONSTITUTION.md"
    if constitution.exists():
        raise AmendError(
            f"Constitution already exists at {constitution}; use plain /idd:amend-constitution"
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
        prefix="idd-constitution-bootstrap-",
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
