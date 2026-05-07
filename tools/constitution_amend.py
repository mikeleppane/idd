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


def _ensure_decisions_file(decisions_path: Path) -> None:
    """Create ``decisions.md`` with the standard header if absent.

    Constitution amends are repo-level decisions and the validator's
    deviations cross-ref needs a decisions.md to exist. Auto-create on first
    amend rather than crash mid-lifecycle.
    """
    if not decisions_path.exists():
        decisions_path.parent.mkdir(parents=True, exist_ok=True)
        decisions_path.write_text("# Decisions\n\n", encoding="utf-8")


def _atomic_replace(target: Path, body: str) -> None:
    """Write ``body`` to ``target`` via ``Path.replace`` from a sibling tempfile.

    POSIX semantics: rename within the same directory is atomic, so any
    crash leaves either the old or the new file intact, never a partial.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(target)


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

    _ensure_decisions_file(decisions_path)

    # Atomic-pair write: Constitution first via os.replace, then decisions
    # append. On decisions-append failure, restore Constitution to `before`
    # so both files end at pre-amend state.
    _atomic_replace(constitution, after)
    entry = _format_decisions_entry(
        today=today, new_version=new_version, scope=scope, body=decisions_body
    )
    try:
        with decisions_path.open("a", encoding="utf-8") as fh:
            fh.write(entry)
    except OSError as exc:
        # Restore Constitution; surface the underlying error.
        _atomic_replace(constitution, before)
        raise AmendError(
            f"decisions.md append failed; Constitution restored to v{current_version}: {exc}"
        ) from exc

    return AmendResult(
        scope=scope,
        old_version=current_version,
        new_version=new_version,
        decisions_entry=entry,
    )
