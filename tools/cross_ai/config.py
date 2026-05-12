"""Cross-AI configuration loader.

Loads ``.forge/config.json`` cross_ai block, validates against
``schemas/cross-ai-config.schema.json``, applies ReDoS guards on
user-supplied regex patterns.

The schema lives alongside the source tree; we resolve it from the package
install location (``Path(__file__)``-relative) rather than the runtime
``repo_root`` argument so callers may point at any working directory
without colocating a copy of the schemas.

ReDoS guard: every pattern in ``redaction.deny_regex`` and
``redaction.fatal_regex`` passes three checks at load time so a
malformed or pathological pattern fails immediately rather than at
first use:

  * Length cap (``REDACTION_REGEX_MAX_LEN`` = 256 chars).
  * Nested-unbounded-quantifier sanity filter via
    :func:`tools.conventions_runtime.has_nested_unbounded_quantifier`
    (catches ``(a+)+``, ``(a*)*``, ``(?:a+)+`` — necessary not
    sufficient against ReDoS, but covers the obvious foot-guns).
  * Probe-compile via ``re.compile``.

The compiled object is discarded; production redaction in
``tools.redaction`` performs its own compile.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import jsonschema

from tools import redaction
from tools.conventions_runtime import has_nested_unbounded_quantifier

# Schema resolved from the package install location, not the caller-supplied
# ``repo_root`` (tests pass ``tmp_path`` with no schemas/ subdirectory).
# Pattern mirrors ``tools/check_schemas.py``: ``parents[1]`` for tools/, plus
# one more level here because this module lives one directory deeper.
_SCHEMAS_DIR: Path = Path(__file__).resolve().parents[2] / "schemas"
_SCHEMA_PATH: Path = _SCHEMAS_DIR / "cross-ai-config.schema.json"

REDACTION_REGEX_MAX_LEN: int = 256


class CrossAiMode(StrEnum):
    """Dispatch mode for cross-AI peer review."""

    manual = "manual"
    auto = "auto"
    disabled = "disabled"


@dataclass(frozen=True)
class RetryPolicy:
    """Retry policy for a single peer-review dispatch."""

    max: int = 1
    backoff_seconds: int = 30


@dataclass(frozen=True)
class RedactionRules:
    """User-supplied deny/allow rule overrides for the redaction filter."""

    deny_globs: tuple[str, ...] = ()
    deny_regex: tuple[str, ...] = ()
    fatal_regex: tuple[str, ...] = ()
    allow_globs: tuple[str, ...] = ()


@dataclass(frozen=True)
class CrossAiConfig:
    """Resolved cross_ai block. Frozen so consumers cannot mutate at runtime."""

    mode: CrossAiMode = CrossAiMode.manual
    allowed_clis: tuple[str, ...] = ()
    timeout_seconds: int = 120
    max_prompt_tokens: int = 100_000
    cost_warn_threshold_usd: float = 0.50
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    redaction: RedactionRules = field(default_factory=RedactionRules)
    dispatch_approved_at: str | None = None
    dispatch_approved_by: str | None = None


class CrossAiConfigError(ValueError):
    """Raised when .forge/config.json cross_ai block is malformed or unsafe."""


def _validate_regex_pattern(pattern: str) -> None:
    """Apply the ReDoS guard to one user-supplied pattern.

    Three-step check, ordered from cheapest to most surgical so the
    error message points at the actual problem:

      1. Length cap (256 chars). Catches paste-bomb inputs before any
         engine work.
      2. Nested-unbounded-quantifier sanity filter via
         :func:`tools.conventions_runtime.has_nested_unbounded_quantifier`.
         Rejects the most obvious ReDoS foot-guns (``(a+)+``, ``(a*)*``,
         ``(?:a+)+``). This is *necessary not sufficient* — alternation
         pathologies (``(a|a)*``), greedy-wildcard nesting (``(.*)+``),
         and backreference shapes still slip through. Patterns that
         compile but match slowly should be exercised against
         adversarial input.
      3. Probe-compile. ``re.error`` becomes a ``CrossAiConfigError``
         so the loader does not raise a bare regex error from inside an
         unrelated layer.

    Each failure raises :class:`CrossAiConfigError` with a preview of
    the offending pattern.
    """
    if len(pattern) > REDACTION_REGEX_MAX_LEN:
        preview = pattern[:32]
        raise CrossAiConfigError(f"regex pattern exceeds 256-char ReDoS guard: {preview!r}...")
    if has_nested_unbounded_quantifier(pattern):
        preview = pattern[:64]
        raise CrossAiConfigError(
            f"regex pattern has nested unbounded quantifier (ReDoS foot-gun): {preview!r}"
        )
    try:
        re.compile(pattern)
    except re.error as exc:
        raise CrossAiConfigError(f"invalid regex: {pattern!r}") from exc


def _build_redaction_rules(block: dict[str, Any]) -> RedactionRules:
    """Materialize ``RedactionRules`` from the schema-validated raw block.

    Probe-compiles every regex pattern; the compiled objects are discarded.
    """
    deny_regex = tuple(block.get("deny_regex", ()))
    fatal_regex = tuple(block.get("fatal_regex", ()))
    for pattern in deny_regex:
        _validate_regex_pattern(pattern)
    for pattern in fatal_regex:
        _validate_regex_pattern(pattern)
    return RedactionRules(
        deny_globs=tuple(block.get("deny_globs", ())),
        deny_regex=deny_regex,
        fatal_regex=fatal_regex,
        allow_globs=tuple(block.get("allow_globs", ())),
    )


def _build_retry_policy(block: dict[str, Any]) -> RetryPolicy:
    """Materialize ``RetryPolicy`` from the schema-validated raw block."""
    return RetryPolicy(
        max=int(block.get("max", 1)),
        backoff_seconds=int(block.get("backoff_seconds", 30)),
    )


def to_redaction_config(
    rules: RedactionRules,
    *,
    gitignore_patterns: tuple[str, ...] = (),
) -> redaction.RedactionConfig:
    """Adapt a config-side :class:`RedactionRules` into a redactor-side config.

    The two dataclasses overlap on four fields (``deny_globs``,
    ``deny_regex``, ``fatal_regex``, ``allow_globs``) but
    :class:`tools.redaction.RedactionConfig` carries an additional
    ``gitignore_patterns`` field that the cross-AI config block does not
    surface (gitignore lifting is out of scope for the manual-mode
    contract). The adapter widens the rules into the redactor shape so
    the skill never has to construct ``RedactionConfig(...)`` by hand
    and cannot accidentally pass the wrong dataclass.

    Deny-glob merge contract: the user-configured ``deny_globs`` are
    UNIONED with :data:`tools.redaction.DEFAULT_DENY_GLOBS` so the
    secret-shaped defaults (``**/.env``, ``**/.aws/**``, ``**/.ssh/**``)
    cannot be silently disabled by an empty ``deny_globs: []`` block.
    Operators who genuinely want to forward one of the default-denied
    paths must use ``allow_globs`` to whitelist it explicitly — that
    keeps the override audit-trail visible at the config layer.

    Args:
        rules: Resolved ``cross_ai.redaction`` block from
            :func:`load_config` (or a default ``RedactionRules()``).
        gitignore_patterns: Optional gitignore overlay. Defaults to the
            empty tuple — the manual-mode skill does not lift
            ``.gitignore`` rules today; the parameter exists so a future
            caller can opt in without changing this signature.

    Returns:
        A :class:`tools.redaction.RedactionConfig` ready to pass to
        :func:`tools.redaction.filter`.
    """
    # Union (preserve order, dedupe) so a user's custom deny patterns
    # extend the defaults rather than replace them.
    seen: set[str] = set()
    merged_deny: list[str] = []
    for glob in (*redaction.DEFAULT_DENY_GLOBS, *rules.deny_globs):
        if glob in seen:
            continue
        seen.add(glob)
        merged_deny.append(glob)
    return redaction.RedactionConfig(
        deny_globs=tuple(merged_deny),
        deny_regex=rules.deny_regex,
        fatal_regex=rules.fatal_regex,
        allow_globs=rules.allow_globs,
        gitignore_patterns=gitignore_patterns,
    )


def load_config(repo_root: Path) -> CrossAiConfig:
    """Load + validate the cross_ai block from ``<repo_root>/.forge/config.json``.

    Returns ``CrossAiConfig()`` defaults when the file or block is absent.
    Raises ``CrossAiConfigError`` on schema mismatch, an unsafe regex
    (length-cap or probe-compile failure), or unreadable JSON.
    """
    config_path = repo_root / ".forge" / "config.json"
    if not config_path.exists():
        return CrossAiConfig()

    try:
        document: dict[str, Any] = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CrossAiConfigError(f"malformed .forge/config.json: {exc.msg}") from exc

    block = document.get("cross_ai")
    if block is None:
        return CrossAiConfig()

    # Re-load the schema fresh per call so test fixtures may evolve it without
    # cross-test pollution from a module-cached validator.
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    # Enable the default format checker so ``format: date-time`` declared on
    # ``dispatch_approved_at`` (RFC 3339) is enforced at load time. Mirrors
    # ``tools/state.py``'s validator construction so the two loaders agree on
    # the contract surface.
    validator = jsonschema.Draft202012Validator(
        schema,
        format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER,
    )
    try:
        validator.validate(block)
    except jsonschema.ValidationError as exc:
        path = list(exc.absolute_path)
        raise CrossAiConfigError(f"{exc.message} at {path}") from exc

    redaction = _build_redaction_rules(block.get("redaction", {}))
    retry = _build_retry_policy(block.get("retry", {}))

    return CrossAiConfig(
        mode=CrossAiMode(block.get("mode", "manual")),
        allowed_clis=tuple(block.get("allowed_clis", ())),
        timeout_seconds=int(block.get("timeout_seconds", 120)),
        max_prompt_tokens=int(block.get("max_prompt_tokens", 100_000)),
        cost_warn_threshold_usd=float(block.get("cost_warn_threshold_usd", 0.50)),
        retry=retry,
        redaction=redaction,
        dispatch_approved_at=block.get("dispatch_approved_at"),
        dispatch_approved_by=block.get("dispatch_approved_by"),
    )
