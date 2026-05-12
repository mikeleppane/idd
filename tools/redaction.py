"""Shared redaction filter for cross-AI dispatch and research websearch fallback.

Operates on a structured PromptPayload (text + optional file inventory).
Returns the spec-§5.3.11 RedactionResult: excluded files (deny-glob /
.gitignore matches without allow-glob rescue), inline regex spans,
fatal-regex matches that the caller MUST refuse-dispatch on, warnings,
and the final output_text.

``filter`` shadows the Python builtin; callers should import via
``from tools import redaction; redaction.filter(payload, config)`` rather
than star-importing the symbol into local scope.

Marker semantics: each ``[REDACTED:<idx>]`` marker references a position in
the **merged interval set**, not a position in ``redacted_spans``. Multiple
overlapping ``deny_regex`` matches collapse into one merged interval that
emits a single marker; ``redacted_spans`` still records every individual
match for caller-side audit. Without this collapse, right-to-left
replacement using original-text offsets corrupts ``output_text`` whenever
two patterns overlap (a later match's tail can survive an earlier match's
replacement because the slice references the new, shorter ``output_text``).

ReDoS notice: ``deny_regex`` and ``fatal_regex`` are user-supplied via
``cross_ai.redaction.*`` in ``.forge/config.json``. This module compiles
them as-is. The config loader owns the bootstrap path and the
responsibility to bound user-supplied regex pathologies (e.g., reject
patterns longer than 256 chars at config-load time). The base ``filter``
implementation ships unbounded; ``deny_regex`` defaults to empty so the
default-config path has no exposure.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from tools._glob import globstar_match as _globstar_match
from tools.conventions_runtime import has_nested_unbounded_quantifier

# Back-compat alias retained so any existing private import (and the lock-down
# regression suite under tests/tools/test_redaction_globstar.py) keeps working.
# New consumers should import :func:`tools._glob.globstar_match` directly.

DEFAULT_DENY_GLOBS: tuple[str, ...] = (
    "**/.env*",
    "**/*credentials*",
    "**/secrets*",
    "**/.aws/**",
    "**/.ssh/**",
)

# Inline-secret patterns that fire UNCONDITIONALLY at default config. The
# patterns target well-known credential shapes that have no legitimate reason
# to land verbatim in a cross-AI prompt:
#
#   * AWS access key id (AKIA + 16 base32 chars)
#   * GitHub PATs (classic ghp_ + 36; fine-grained github_pat_ + 82)
#   * Anthropic API key (sk-ant-api03- / sk-ant-admin01- + 93-108 chars)
#   * Anthropic OAuth bearer (sk-ant-oauth- + 40+ chars)
#   * OpenAI / Project API keys (sk- or sk-proj- + 20+ chars)
#   * Google AI / Gemini key (AIza + 35 char id)
#   * Slack bot/user/refresh tokens (xox{a,b,p,r,s})
#   * PEM private key markers (any flavour)
#   * Authorization: Bearer ... header context
#   * UPPER_CASE = ... env-line shape carrying SECRET / TOKEN / API_KEY /
#     PASSWORD in the key name
#
# No allowlist hook exists for these — a false positive on documentation
# costs a single ``[REDACTED:<idx>]`` marker, while a leak costs a real key.
DEFAULT_DENY_REGEX: tuple[str, ...] = (
    r"AKIA[0-9A-Z]{16}",
    r"ghp_[A-Za-z0-9]{36}",
    r"github_pat_[A-Za-z0-9_]{82}",
    r"sk-ant-(api03|admin01)-[A-Za-z0-9_\-]{93,108}",
    r"sk-ant-oauth-[A-Za-z0-9_\-]{40,}",
    r"sk-(proj-)?[A-Za-z0-9_\-]{20,}",
    r"AIza[0-9A-Za-z_\-]{35}",
    r"xox[abprs]-[0-9]+-[0-9]+-[A-Za-z0-9-]+",
    r"-----BEGIN (RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----",
    r"(?i)authorization:\s*bearer\s+[A-Za-z0-9_\-\.]+",
    r"(?im)^[A-Z_]{0,32}(SECRET|TOKEN|API_KEY|PASSWORD)\s*=\s*\S+",
)


# Load-time regression guard: every default pattern must compile cleanly and
# pass the rough ReDoS sanity filter. A typo introducing a nested unbounded
# quantifier (e.g. ``(a+)+``) into DEFAULT_DENY_REGEX would otherwise ship
# silently. Refusing the import surfaces the bug at the first test run.
def _assert_default_patterns_safe() -> None:
    for pattern in DEFAULT_DENY_REGEX:
        try:
            re.compile(pattern)
        except re.error as exc:  # pragma: no cover - regression guard
            msg = f"tools.redaction: DEFAULT_DENY_REGEX entry does not compile: {pattern!r}: {exc}"
            raise RuntimeError(msg) from exc
        if has_nested_unbounded_quantifier(pattern):  # pragma: no cover - regression guard
            msg = (
                f"tools.redaction: DEFAULT_DENY_REGEX entry has nested unbounded "
                f"quantifier (ReDoS foot-gun): {pattern!r}"
            )
            raise RuntimeError(msg)


_assert_default_patterns_safe()

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
            matches are replaced with ``[REDACTED:<idx>]``. Defaults to
            :data:`DEFAULT_DENY_REGEX` so common credential shapes are
            redacted out of the box. Callers extending the list should
            append to ``DEFAULT_DENY_REGEX`` rather than overwriting it
            unless they have a deliberate reason to disable the defaults.
        fatal_regex: Regex patterns that record a ``fatal_match`` so the
            caller refuses dispatch. Matches are NOT replaced.
        allow_globs: Explicit allow-list; rescues a file from any deny rule.
        gitignore_patterns: Globstar patterns lifted from the project's
            ``.gitignore``; treated as deny but emit a single warning when
            they overlap with ``deny_globs``.
    """

    deny_globs: tuple[str, ...] = DEFAULT_DENY_GLOBS
    deny_regex: tuple[str, ...] = DEFAULT_DENY_REGEX
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

    # Coalesce overlapping ranges into one merged interval each. Two patterns
    # firing on the same secret (or any positionally-overlapping substrings)
    # must emit a single marker covering the union — otherwise right-to-left
    # replacement using original-text offsets clobbers the earlier marker and
    # leaves a stray fragment of one match in ``output_text``. ``spans`` keeps
    # the per-rule audit trail; ``merged_intervals`` drives marker emission.
    merged_intervals: list[tuple[int, int]] = []
    for span in spans:
        if merged_intervals and span.start < merged_intervals[-1][1]:
            cur_start, cur_end = merged_intervals[-1]
            merged_intervals[-1] = (cur_start, max(cur_end, span.end))
        else:
            merged_intervals.append((span.start, span.end))

    # Replace right-to-left so earlier offsets stay valid against the original
    # text. Marker index references the merged interval set.
    output_text = text
    for idx in range(len(merged_intervals) - 1, -1, -1):
        m_start, m_end = merged_intervals[idx]
        output_text = output_text[:m_start] + f"[REDACTED:{idx}]" + output_text[m_end:]

    # Operator visibility: every redaction emits a single stderr line. The
    # sample is intentionally NOT echoed (it's the secret); we surface only
    # the rule id and the marker index so the operator knows something fired
    # without leaking the matched substring into terminal scrollback.
    if spans:
        for span in spans:
            print(
                f"WARN: tools.redaction: inline secret redacted (rule={span.rule_id!r})",
                file=sys.stderr,
            )

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
