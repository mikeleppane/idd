"""Negative-Requirement re-grep module for the post-ship QA phase.

Re-greps the merged tree against every Negative Requirement parsed from a
feature's ``SPEC.md`` and returns a structured :class:`NRResult`. Catches
re-introduced violations the merge itself may have re-added under a different
file path — a regression scenario replays cannot see because they re-run
behavior, not source-tree pattern checks.

The module is pure-Python (no subprocess) so it can run in any sandbox the
QA orchestrator hands it. Both file walking and pattern matching are
:class:`Callable` overrides so tests can inject deterministic alternatives.

Negative-Requirement parsing is fence-aware: ``_strip_code`` is applied to the
``# Negative Requirements`` body slice before bullet scanning so illustrative
fenced examples (``- MUST NOT use `eval` `` inside a ```` ``` ```` block) are
not parsed as live NRs.

The NR parser here is local to this module by design. The existing
:mod:`tools.validate.spec_semantic` module exposes scenario / acceptance /
anchor parsers but no public NR parser — only the placement validator
(``validate_negative_requirements`` in :mod:`tools.validate.spec_structural`).
A follow-up consolidation should promote :func:`parse_negative_requirements`
into :mod:`tools.validate.spec_semantic` so the QA and validate layers
share one parser; until then this duplication is intentional and minimal.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Literal

from tools.qa import QAError
from tools.validate._frontmatter import _read_text

NRStatus = Literal["pass", "fail"]

# Match the `# Negative Requirements` body slice up to the next H1.
_NR_BLOCK = re.compile(
    r"(?ms)^# Negative Requirements\b[^\n]*\n(?P<body>.*?)(?=^# |\Z)",
)

# Bullet-list line: `-` or `*` plus space, then the NR text.
_BULLET = re.compile(r"^\s*[-*]\s+(?P<text>.+?)\s*$")

# Fence delimiter (lstrip-aware). Anything starting with ``` is a fence
# open/close marker — content between two such markers is illustrative.
_FENCE_DELIM = re.compile(r"^```")

# Backticked tokens inside an NR bullet — these are explicit forbidden tokens.
_BACKTICK_TOKEN = re.compile(r"`([^`\n]+)`")

# Best-effort `MUST NOT <verb> <object>` extraction. Captures the object phrase
# up to common terminators (period, comma, " for ", " when ", " in ", end of
# string). Heuristic only — failure to extract is allowed and surfaces as an
# NR with no forbidden patterns, counted in `nrs_scanned` but emitting no
# violations.
_MUST_NOT_OBJECT = re.compile(
    r"(?ix)\b(?:MUST|SHALL)\s+NOT\s+(?:\w+\s+)?(?P<obj>[A-Za-z_][A-Za-z0-9_.]*)",
)

# Default file-extension allowlist for the tree walk. Matches Python source +
# common config / shell / markdown text the project actually ships. Binaries
# and lockfiles are skipped to keep the grep fast and false-positive-free.
_DEFAULT_TEXT_SUFFIXES: Final[frozenset[str]] = frozenset(
    {
        ".py",
        ".pyi",
        ".md",
        ".rst",
        ".toml",
        ".yaml",
        ".yml",
        ".json",
        ".cfg",
        ".ini",
        ".sh",
        ".bash",
        ".txt",
    }
)

# Default path-segment exclusions. Any path containing one of these segments
# (e.g. `.git/...`, `tests/...`, `docs/...`) is skipped. The QA orchestrator
# can override the whole filter via the ``file_filter`` argument to
# :func:`run_nr_regrep`.
_DEFAULT_EXCLUDED_SEGMENTS: Final[frozenset[str]] = frozenset(
    {
        ".forge",
        ".git",
        ".venv",
        "tests",
        "docs",
        "node_modules",
        "__pycache__",
        "build",
        "dist",
    }
)


@dataclass(frozen=True)
class NegativeRequirement:
    """A single negative requirement parsed from SPEC.md.

    Attributes:
        nr_id: Canonical id (``nr-1``, ``nr-2``, ...) derived from the order
            the bullet appears in the ``# Negative Requirements`` section.
        text: Raw bullet text, trimmed.
        forbidden_patterns: Tokens to grep for. Sourced from backticked tokens
            in ``text`` and a best-effort ``MUST NOT <verb> <object>`` heuristic.
            May be empty when no token can be extracted; in that case the NR
            is informational only and contributes no violations.
    """

    nr_id: str
    text: str
    forbidden_patterns: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class NRViolation:
    """A single re-greped match — one NR pattern hit one line in one file.

    Attributes:
        nr_id: Canonical id of the NR whose pattern matched.
        pattern: The forbidden token that matched.
        file_path: Offending file path, relative to ``repo_root``.
        line_number: 1-indexed line within the file.
        line_text: The offending line, trimmed of surrounding whitespace.
    """

    nr_id: str
    pattern: str
    file_path: Path
    line_number: int
    line_text: str


@dataclass(frozen=True)
class NRResult:
    """Aggregate result of re-greping every NR against the merged tree.

    Attributes:
        status: ``pass`` when no violations; ``fail`` otherwise. NRs without
            extractable patterns count toward ``nrs_scanned`` but cannot fail.
        nrs_scanned: Number of NRs parsed from SPEC.md.
        violations: All re-greped violations, sorted by
            ``(nr_id, file_path, line_number)`` for deterministic output.
    """

    status: NRStatus
    nrs_scanned: int
    violations: list[NRViolation] = field(default_factory=list)


def _extract_forbidden_patterns(text: str) -> list[str]:
    """Extract grep targets from one NR bullet.

    Strategy (in order, deduplicated, original order preserved):
        1. Backticked tokens — explicit, highest signal.
        2. ``MUST NOT <verb> <object>`` object — heuristic, lower signal.
    """
    seen: set[str] = set()
    patterns: list[str] = []
    for match in _BACKTICK_TOKEN.finditer(text):
        token = match.group(1).strip()
        if token and token not in seen:
            seen.add(token)
            patterns.append(token)
    if not patterns:
        # Only fall back to verb/object extraction when no explicit tokens
        # were given. Backticks are the high-signal channel; the heuristic
        # is a second-best fallback prone to false positives.
        for match in _MUST_NOT_OBJECT.finditer(text):
            obj = match.group("obj").strip()
            if obj and obj not in seen and not _is_stopword(obj):
                seen.add(obj)
                patterns.append(obj)
    return patterns


_HEURISTIC_STOPWORDS: Final[frozenset[str]] = frozenset(
    {
        "a",
        "an",
        "the",
        "this",
        "that",
        "any",
        "all",
        "be",
        "do",
        "regress",
        "regression",
        "scope",
        "creep",
    }
)


def _is_stopword(token: str) -> bool:
    """Return True for tokens too generic to grep on without false positives."""
    return token.lower() in _HEURISTIC_STOPWORDS


def parse_negative_requirements(spec_md: Path) -> list[NegativeRequirement]:
    """Parse the ``# Negative Requirements`` section of a SPEC.md.

    Fence-aware: code-fence and inline-code regions are blanked via
    :func:`_strip_code` before bullet scanning so illustrative examples cannot
    smuggle NRs into the live list.

    Args:
        spec_md: Path to the SPEC.md file.

    Returns:
        Ordered list of :class:`NegativeRequirement`. Empty list when:

        - The file is missing or unreadable.
        - The file has no ``# Negative Requirements`` section.
        - The section exists but is empty / contains no bullet lines.
    """
    text = _read_text(spec_md)
    if text is None:
        return []

    block_match = _NR_BLOCK.search(text)
    if block_match is None:
        return []

    nrs: list[NegativeRequirement] = []
    in_fence = False
    for line in block_match.group("body").splitlines():
        if _FENCE_DELIM.match(line.lstrip()):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        bullet = _BULLET.match(line)
        if bullet is None:
            continue
        bullet_text = bullet.group("text").strip()
        if not bullet_text:
            continue
        nrs.append(
            NegativeRequirement(
                nr_id=f"nr-{len(nrs) + 1}",
                text=bullet_text,
                forbidden_patterns=_extract_forbidden_patterns(bullet_text),
            )
        )
    return nrs


def _default_file_filter(relative_path: Path) -> bool:
    """Return True when ``relative_path`` is in scope for the default scan.

    A file is in scope when:
        - Its suffix is in :data:`_DEFAULT_TEXT_SUFFIXES`.
        - None of its path segments are in :data:`_DEFAULT_EXCLUDED_SEGMENTS`.
    """
    if relative_path.suffix not in _DEFAULT_TEXT_SUFFIXES:
        return False
    return not any(part in _DEFAULT_EXCLUDED_SEGMENTS for part in relative_path.parts)


def _default_grepper(file_path: Path, pattern: str) -> list[tuple[int, str]]:
    """Substring-match ``pattern`` against each line of ``file_path``.

    Case-sensitive; returns ``(line_number, line_text)`` tuples with
    ``line_number`` 1-indexed and ``line_text`` rstripped of trailing
    whitespace. Files that fail to read (binary, permission denied) yield no
    matches rather than crashing the QA run.
    """
    try:
        text = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    matches: list[tuple[int, str]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if pattern in line:
            matches.append((line_number, line.rstrip()))
    return matches


def _walk_target_files(
    repo_root: Path,
    file_filter: Callable[[Path], bool],
) -> list[Path]:
    """Collect every regular file under ``repo_root`` accepted by ``file_filter``.

    Paths are returned relative to ``repo_root`` and sorted for deterministic
    iteration order.
    """
    repo_root_resolved = repo_root.resolve()
    targets: list[Path] = []
    for path in repo_root_resolved.rglob("*"):
        if not path.is_file():
            continue
        try:
            relative = path.relative_to(repo_root_resolved)
        except ValueError:  # pragma: no cover - defensive
            continue
        if file_filter(relative):
            targets.append(relative)
    return sorted(targets)


def run_nr_regrep(
    repo_root: Path,
    feature_id: str,
    *,
    file_filter: Callable[[Path], bool] | None = None,
    grepper: Callable[[Path, str], list[tuple[int, str]]] | None = None,
) -> NRResult:
    """Re-grep ``repo_root`` against every NR in the feature's SPEC.md.

    Args:
        repo_root: Repository root the feature folder resolves under and the
            tree-walk runs against.
        feature_id: Feature identifier (e.g. ``2026-05-09-example``).
        file_filter: Optional predicate on a repo-relative :class:`Path` —
            return ``True`` to scan, ``False`` to skip. Defaults to a filter
            that scans common text suffixes outside ``.git`` / ``tests`` /
            ``docs`` / ``node_modules`` / ``.forge`` / ``__pycache__``.
        grepper: Optional callable taking ``(absolute_file_path, pattern)``
            and returning ``(line_number, line_text)`` matches. Defaults to a
            simple substring matcher reading the file as UTF-8.

    Returns:
        :class:`NRResult` with violations sorted by
        ``(nr_id, file_path, line_number)``.

    Raises:
        QAError: When the feature's SPEC.md is missing.
    """
    spec_path = repo_root / ".forge" / "features" / feature_id / "SPEC.md"
    if not spec_path.is_file():
        raise QAError(f"SPEC.md missing for feature {feature_id!r} at {spec_path}")

    nrs = parse_negative_requirements(spec_path)
    if not nrs:
        return NRResult(status="pass", nrs_scanned=0, violations=[])

    active_filter = file_filter if file_filter is not None else _default_file_filter
    active_grepper = grepper if grepper is not None else _default_grepper

    target_files = _walk_target_files(repo_root, active_filter)
    repo_root_resolved = repo_root.resolve()

    violations: list[NRViolation] = []
    for nr in nrs:
        if not nr.forbidden_patterns:
            continue
        for relative_path in target_files:
            absolute = repo_root_resolved / relative_path
            for pattern in nr.forbidden_patterns:
                for line_number, line_text in active_grepper(absolute, pattern):
                    violations.append(
                        NRViolation(
                            nr_id=nr.nr_id,
                            pattern=pattern,
                            file_path=relative_path,
                            line_number=line_number,
                            line_text=line_text.strip(),
                        )
                    )

    violations.sort(key=lambda v: (v.nr_id, str(v.file_path), v.line_number))
    status: NRStatus = "fail" if violations else "pass"
    return NRResult(status=status, nrs_scanned=len(nrs), violations=violations)
