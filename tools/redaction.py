"""Shared redaction filter for cross-AI dispatch and research websearch fallback.

Operates on a structured PromptPayload (text + optional file inventory).
Returns the spec-§5.3.11 RedactionResult: excluded files (deny-glob /
.gitignore matches without allow-glob rescue), inline regex spans,
fatal-regex matches that the caller MUST refuse-dispatch on, warnings,
and the final output_text.

``filter`` shadows the Python builtin; callers should import via
``from tools import redaction; redaction.filter(payload, config)`` rather
than star-importing the symbol into local scope.

ReDoS notice: ``deny_regex`` and ``fatal_regex`` are user-supplied via
``cross_ai.redaction.*`` in ``.forge/config.json`` (loaded in P1).
This module compiles them as-is. P1 owns the bootstrap path and the
responsibility to bound user-supplied regex pathologies (e.g., reject
patterns longer than 256 chars at config-load time). P0 ships ``filter``
unbounded; ``deny_regex`` defaults to empty so P0 has no exposure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

DEFAULT_DENY_GLOBS: tuple[str, ...] = (
    "**/.env*",
    "**/*credentials*",
    "**/secrets*",
    "**/.aws/**",
    "**/.ssh/**",
)

# Cap on the ``sample`` field stored on Span / Match. Keeps RedactionResult
# bounded even when a deny_regex matches a very long substring.
_SAMPLE_MAX_CHARS: int = 80


@dataclass(frozen=True)
class PromptPayload:
    """Inputs to ``filter``.

    Attributes:
        text: Inline prompt text. Subject to ``deny_regex`` / ``fatal_regex``.
        files: POSIX paths the caller wants to forward. Caller is responsible
            for normalizing host paths to ``PurePosixPath`` before constructing
            the payload; ``filter`` does not normalize.
    """

    text: str = ""
    files: tuple[PurePosixPath, ...] = ()


@dataclass(frozen=True)
class RedactionConfig:
    """Per-call deny/allow rule set.

    Attributes:
        deny_globs: Globstar patterns; matching files are dropped.
        deny_regex: Regex patterns matched against ``payload.text``;
            matches are replaced with ``[REDACTED:<idx>]``.
        fatal_regex: Regex patterns that record a ``fatal_match`` so the
            caller refuses dispatch. Matches are NOT replaced.
        allow_globs: Explicit allow-list; rescues a file from any deny rule.
        gitignore_patterns: Globstar patterns lifted from the project's
            ``.gitignore``; treated as deny but emit a single warning when
            they overlap with ``deny_globs``.
    """

    deny_globs: tuple[str, ...] = DEFAULT_DENY_GLOBS
    deny_regex: tuple[str, ...] = ()
    fatal_regex: tuple[str, ...] = ()
    allow_globs: tuple[str, ...] = ()
    gitignore_patterns: tuple[str, ...] = ()


@dataclass(frozen=True)
class Span:
    """A redacted region in the original ``payload.text``.

    ``start`` and ``end`` are offsets into the ORIGINAL text (pre-replacement),
    so a caller can correlate the marker back to the source slice.
    """

    start: int
    end: int
    rule_id: str
    sample: str


@dataclass(frozen=True)
class Match:
    """A regex hit recorded for the caller's diagnostic / refusal logic."""

    rule_id: str
    sample: str
    file: PurePosixPath | None = None


@dataclass(frozen=True)
class RedactionResult:
    """Spec-§5.3.11 output of ``filter``.

    Tuple-typed collections defend against caller mutation; the dataclass
    itself is frozen.
    """

    excluded_files: tuple[PurePosixPath, ...] = field(default_factory=tuple)
    redacted_spans: tuple[Span, ...] = field(default_factory=tuple)
    fatal_matches: tuple[Match, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    output_text: str = ""

    @property
    def had_denials(self) -> bool:
        """True iff a file was excluded or an inline span was redacted.

        Documented nuance: ``fatal_matches`` does NOT flip ``had_denials``;
        callers handle the refusal path on ``fatal_matches`` separately.
        """
        return bool(self.excluded_files or self.redacted_spans)


def _globstar_match(path: str, glob: str) -> bool:
    """Return True iff ``path`` matches a ``**``-aware ``glob``.

    Translation rules:
      * ``**/`` (followed by ``/``) → ``(?:.*/)?``  matches zero-or-more
        leading path segments, so ``**/.env`` matches both ``.env`` (root)
        and ``project/.env`` (nested) — parity with
        ``PurePosixPath.full_match`` (Python 3.13+).
      * remaining ``**``  → ``.*``    (cross path separators)
      * single ``*``      → ``[^/]*`` (within one path segment)
      * ``?``             → ``[^/]``
      * everything else is ``re.escape``-protected.

    Stand-in for ``PurePosixPath.full_match`` (Python 3.13+); we run on 3.12
    so we ship our own. Fully anchored via ``re.fullmatch``.
    """
    out: list[str] = []
    i = 0
    while i < len(glob):
        ch = glob[i]
        if ch == "*":
            # Look-ahead: consume a ``**`` group.
            if i + 1 < len(glob) and glob[i + 1] == "*":
                # ``**/`` at start or after a separator collapses the slash so
                # the pattern matches zero leading segments too.
                if i + 2 < len(glob) and glob[i + 2] == "/":
                    out.append("(?:.*/)?")
                    i += 3
                else:
                    out.append(".*")
                    i += 2
            else:
                out.append("[^/]*")
                i += 1
        elif ch == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(ch))
            i += 1
    pattern = "".join(out)
    return re.fullmatch(pattern, path) is not None


def _matches_any(path: PurePosixPath, globs: tuple[str, ...]) -> bool:
    """True iff ``path`` matches any glob in ``globs``."""
    s = path.as_posix()
    return any(_globstar_match(s, g) for g in globs)


def _truncate_sample(text: str) -> str:
    """Truncate matched substring to the documented sample cap."""
    return text[:_SAMPLE_MAX_CHARS]


def filter(  # noqa: A001 — module-level shadow is documented in module docstring.
    payload: PromptPayload,
    config: RedactionConfig | None = None,
) -> RedactionResult:
    """Apply deny/allow filter to ``payload``; return the spec-§5.3.11 result.

    Precedence (top wins):
      1. ``allow_globs`` rescues a file from any deny rule.
      2. ``deny_globs`` excludes the file outright.
      3. ``gitignore_patterns`` excludes the file; one warning when overlap
         with ``deny_globs`` is detected.
      4. ``deny_regex`` replaces inline spans with ``[REDACTED:<idx>]``;
         the user-supplied pattern is NOT echoed into ``output_text``.
      5. ``fatal_regex`` records a ``fatal_match``; the inline span is NOT
         replaced (caller refuses anyway, the diagnostic substring is kept).

    Args:
        payload: Frozen input prompt + file inventory.
        config: Optional rule set; ``RedactionConfig()`` defaults are used
            when omitted (default deny_globs, empty regex lists).

    Returns:
        A frozen ``RedactionResult`` with tuple-typed collections.
    """
    cfg = config if config is not None else RedactionConfig()

    excluded: list[PurePosixPath] = []
    warnings: list[str] = []

    # File-level filtering (precedence steps 1-3).
    overlap_warning_emitted = False
    for f in payload.files:
        if _matches_any(f, cfg.allow_globs):
            continue  # step 1: allow-list wins.

        deny_hit = _matches_any(f, cfg.deny_globs)
        gitignore_hit = _matches_any(f, cfg.gitignore_patterns)

        if deny_hit and gitignore_hit and not overlap_warning_emitted:
            warnings.append(
                "gitignore_patterns overlap with deny_globs; files de-duplicated in excluded_files"
            )
            overlap_warning_emitted = True

        if (deny_hit or gitignore_hit) and f not in excluded:
            # De-dupe per spec §5.3.11.
            excluded.append(f)

    # Inline regex filtering (precedence steps 4-5).
    text = payload.text
    deny_compiled = [(p, re.compile(p)) for p in cfg.deny_regex]
    fatal_compiled = [(p, re.compile(p)) for p in cfg.fatal_regex]

    # Collect deny-regex hits across the ORIGINAL text. We perform replacement
    # right-to-left so earlier offsets (recorded on Span) stay valid in the
    # source text — Span.start/end refer to pre-replacement coordinates.
    deny_hits: list[tuple[int, int, str, str]] = []  # (start, end, rule, sample)
    for rule, regex in deny_compiled:
        deny_hits.extend(
            (m.start(), m.end(), rule, _truncate_sample(m.group(0))) for m in regex.finditer(text)
        )

    # Stable order: by start offset, then by rule_id (deterministic ties).
    deny_hits.sort(key=lambda h: (h[0], h[2]))
    spans: list[Span] = [
        Span(start=s, end=e, rule_id=r, sample=sample) for (s, e, r, sample) in deny_hits
    ]

    # Replace right-to-left using the marker index that matches each Span's
    # position in the ``redacted_spans`` tuple.
    output_text = text
    for idx in range(len(spans) - 1, -1, -1):
        span = spans[idx]
        output_text = output_text[: span.start] + f"[REDACTED:{idx}]" + output_text[span.end :]

    # Fatal regex: record only, NEVER mutate output_text.
    fatal: list[Match] = []
    for rule, regex in fatal_compiled:
        fatal.extend(
            Match(rule_id=rule, sample=_truncate_sample(m.group(0)), file=None)
            for m in regex.finditer(text)
        )

    return RedactionResult(
        excluded_files=tuple(excluded),
        redacted_spans=tuple(spans),
        fatal_matches=tuple(fatal),
        warnings=tuple(warnings),
        output_text=output_text,
    )
