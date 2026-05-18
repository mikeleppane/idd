"""Stdlib-only runtime for ``.forge/conventions.json``.

The dispatch hook (``hooks/check_budget.py``) runs from arbitrary plugin
roots where third-party deps (``jsonschema``, ``yaml``) are not guaranteed
to be available. This module provides the minimum surface the hook needs —
parse, build typed rules, run a single rule against text — using only the
Python standard library.

The strict schema-validated loader and the runtime ``Finding``-shaped
validator live in :mod:`tools.validate.conventions`; that module
``import``s the symbols here and adds the schema / regex-compile / scope
checks on top. Consumers that have the dev environment available should
prefer the strict path — keep this module for the hook and any future
stdlib-only consumer (e.g. a pre-commit hook).
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, cast

from tools._glob import globstar_match

PatternKind = Literal["forbidden_text", "required_text", "filename_glob_forbidden"]
Scope = Literal["commit_body", "diff", "dispatch_brief"]
Severity = Literal["BLOCK", "HIGH", "MEDIUM", "LOW", "WARN", "INFO"]

_CONVENTIONS_FILENAME: Final[str] = "conventions.json"

# Cap on text length passed to a regex engine. The diff scope can legitimately
# carry multi-MB content; backtracking regex engines can pin a core on chosen
# inputs. Truncating at this cap is a defense-in-depth ReDoS mitigation that
# costs essentially nothing for legitimate rules (which match on prefixes /
# small spans). Callers that need full-payload matching for a specific rule
# should split the work into bounded chunks rather than raising the cap.
TEXT_MATCH_CAP_BYTES: Final[int] = 256 * 1024  # 256 KiB

# Rough heuristic catching nested-unbounded-quantifier shapes at load time:
# groups like ``(a+)+``, ``(a*)*``, ``(?:a+)+``. Linear time, narrow false-
# positive surface; rules that need this shape can re-express the intent or
# switch to bounded quantifiers.
#
# This regex is intentionally narrow. It does NOT detect alternation-overlap
# patterns like ``(a|a)*``, nor ``(.*)+``-style traps that overlap via
# wildcards, nor backreference-driven pathologies. Authors writing patterns
# that compile but match slowly on adversarial input should still profile
# against the inputs they actually expect — the strict loader is a sanity
# filter, not a complete ReDoS classifier.
_NESTED_UNBOUNDED_QUANTIFIER_RE = re.compile(
    r"\([^()]*[+*][^()]*\)[+*]"
    r"|\(\?:[^()]*[+*][^()]*\)[+*]"
)


@dataclass(frozen=True, kw_only=True)
class Convention:
    """One pattern-based convention rule from ``.forge/conventions.json``.

    Attributes:
        id: Globally unique rule identifier.
        source_file: Repo-relative path of the authoring document.
        source_line: 1-based line number in ``source_file``.
        pattern_kind: Match-engine selector.
        pattern: The regex or glob source string.
        scope: Tuple of scopes this rule applies to. Immutable + hashable
            so ``Convention`` stays usable as a dict key or set member.
        severity: Severity emitted on a match.
    """

    id: str
    source_file: str
    source_line: int
    pattern_kind: PatternKind
    pattern: str
    scope: tuple[Scope, ...]
    severity: Severity


_REQUIRED_KEYS: Final[frozenset[str]] = frozenset(
    {"id", "source_file", "source_line", "pattern_kind", "pattern", "scope", "severity"},
)


def _conventions_path(repo_root: Path) -> Path:
    return repo_root / ".forge" / _CONVENTIONS_FILENAME


def _build_one(idx: int, entry: dict[str, object]) -> Convention:
    missing = _REQUIRED_KEYS - entry.keys()
    if missing:
        raise ValueError(
            f"entry[{idx}] missing required key(s): {sorted(missing)}",
        )
    scope_raw = entry["scope"]
    if not isinstance(scope_raw, list) or not scope_raw:
        raise ValueError(f"entry[{idx}].scope must be a non-empty list")
    scope_strs = [s for s in scope_raw if isinstance(s, str)]
    if len(scope_strs) != len(scope_raw):
        raise ValueError(f"entry[{idx}].scope items must all be strings")
    scope: tuple[Scope, ...] = cast("tuple[Scope, ...]", tuple(scope_strs))
    rule_id = entry["id"]
    if not isinstance(rule_id, str):
        raise ValueError(f"entry[{idx}].id must be a string")
    source_file = entry["source_file"]
    if not isinstance(source_file, str):
        raise ValueError(f"entry[{idx}].source_file must be a string")
    source_line = entry["source_line"]
    if not isinstance(source_line, int) or isinstance(source_line, bool):
        raise ValueError(f"entry[{idx}].source_line must be an integer")
    pattern_kind = entry["pattern_kind"]
    if pattern_kind not in ("forbidden_text", "required_text", "filename_glob_forbidden"):
        raise ValueError(f"entry[{idx}].pattern_kind not in the documented set")
    pattern = entry["pattern"]
    if not isinstance(pattern, str) or not pattern:
        raise ValueError(f"entry[{idx}].pattern must be a non-empty string")
    severity = entry["severity"]
    if severity not in ("BLOCK", "HIGH", "MEDIUM", "LOW", "WARN"):
        # The schema's enum excludes INFO; mirror that here so the permissive
        # path stays consistent with the strict path's accepted vocabulary.
        raise ValueError(f"entry[{idx}].severity not in the documented set")
    return Convention(
        id=rule_id,
        source_file=source_file,
        source_line=source_line,
        pattern_kind=pattern_kind,
        pattern=pattern,
        scope=scope,
        severity=severity,
    )


def load_conventions_permissive(repo_root: Path) -> list[Convention]:
    """Parse ``.forge/conventions.json`` with stdlib-only validation.

    Designed for the dispatch hook: no jsonschema / yaml deps, no
    ``_frontmatter`` import chain. Performs the minimum shape work
    required to build typed :class:`Convention` records.

    Args:
        repo_root: Repository root containing the ``.forge`` directory.

    Returns:
        List of :class:`Convention` records, empty when the file is absent.

    Raises:
        ValueError: When the file is present and fails JSON parse, root
            shape, required-field, or duplicate-id checks. The strict
            schema (additional properties, id regex, scope enum, etc.) is
            owned by :func:`tools.validate.conventions.load_conventions`.
    """
    path = _conventions_path(repo_root)
    if not path.is_file():
        return []
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"failed to read conventions.json: {exc}") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"failed to parse conventions.json: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(
            f"conventions root must be a JSON object {{schema_version, rules: [...]}}, "
            f"got {type(payload).__name__}",
        )
    rules_raw = payload.get("rules", [])
    if not isinstance(rules_raw, list):
        raise ValueError(
            f"conventions.rules must be a JSON array, got {type(rules_raw).__name__}",
        )
    rules: list[Convention] = []
    seen_ids: set[str] = set()
    duplicates: list[str] = []
    for idx, entry in enumerate(rules_raw):
        if not isinstance(entry, dict):
            raise ValueError(f"rules[{idx}] must be a JSON object")
        rule = _build_one(idx, entry)
        if rule.id in seen_ids and rule.id not in duplicates:
            duplicates.append(rule.id)
        seen_ids.add(rule.id)
        rules.append(rule)
    if duplicates:
        raise ValueError(f"duplicate id(s); ids must be globally unique: {sorted(duplicates)}")
    return _drop_dead_letter_dispatch_brief_rules(rules)


# Severities the dispatch hook enforces. Mirrors
# ``hooks/check_budget.py::_CONVENTION_DENY_SEVERITIES`` and
# ``tools.validate.conventions._DISPATCH_BRIEF_ENFORCED_SEVERITIES``.
_DISPATCH_BRIEF_ENFORCED_SEVERITIES: Final[frozenset[str]] = frozenset({"BLOCK", "HIGH"})


def _drop_dead_letter_dispatch_brief_rules(rules: list[Convention]) -> list[Convention]:
    """Drop ``dispatch_brief``-scope rules whose severity the hook ignores.

    The strict loader in :mod:`tools.validate.conventions` raises on such
    rules at load time. The permissive runtime path (this module) is the
    hook's entry point and must fail-permissive on shape issues so a single
    bad rule does not silently disable enforcement of the rest. We log a
    one-line stderr warning per skipped rule and continue.
    """
    kept: list[Convention] = []
    for rule in rules:
        if (
            "dispatch_brief" in rule.scope
            and rule.severity not in _DISPATCH_BRIEF_ENFORCED_SEVERITIES
        ):
            print(
                f"[forge-check-budget] skipping dead-letter rule {rule.id!r}: "
                f"scope 'dispatch_brief' with severity {rule.severity!r} is not "
                "enforced by the dispatch hook (only BLOCK / HIGH fire)",
                file=sys.stderr,
            )
            continue
        kept.append(rule)
    return kept


def _compile_or_none(pattern: str) -> re.Pattern[str] | None:
    try:
        return re.compile(pattern)
    except re.error:
        return None


def _bound_text(text: str) -> str:
    """Return ``text`` truncated to :data:`TEXT_MATCH_CAP_BYTES` UTF-8 bytes."""
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= TEXT_MATCH_CAP_BYTES:
        return text
    return encoded[:TEXT_MATCH_CAP_BYTES].decode("utf-8", errors="replace")


def has_nested_unbounded_quantifier(pattern: str) -> bool:
    """Detect nested unbounded quantifier shapes that commonly cause ReDoS.

    Catches: ``(a+)+``, ``(a*)*``, ``(?:a+)+``.

    Does NOT catch: ``(a|a)*``, ``(.*)+``, alternation-overlap, or
    backreference pathologies. The check is a rough sanity filter, not a
    complete ReDoS classifier. Authors writing patterns that compile but
    match slowly should still test against adversarial input.

    Used by the strict loader to reject the most obvious foot-guns at load
    time.
    """
    return _NESTED_UNBOUNDED_QUANTIFIER_RE.search(pattern) is not None


def match_convention(rule: Convention, *, text: str, scope: Scope) -> bool:
    """Return ``True`` iff ``rule`` fires against ``text`` for ``scope``.

    Returns ``False`` when the scope filter rejects the rule, when the
    regex fails to compile (the strict validator surfaces the compile
    error separately), or when the violation condition does not hold.

    ``text`` is truncated to :data:`TEXT_MATCH_CAP_BYTES` UTF-8 bytes
    before being passed to the regex engine — a defense-in-depth ReDoS
    mitigation. Callers that need full-payload matching for a specific
    rule should split the work into bounded chunks.
    """
    if scope not in rule.scope:
        return False
    bounded = _bound_text(text)
    if rule.pattern_kind == "filename_glob_forbidden":
        return globstar_match(bounded, rule.pattern)
    compiled = _compile_or_none(rule.pattern)
    if compiled is None:
        return False
    found = compiled.search(bounded) is not None
    if rule.pattern_kind == "forbidden_text":
        return found
    # required_text: violation iff NOT found.
    return not found
